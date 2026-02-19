"""
Snapshot routes: list, create, view, restore, and delete
playlist snapshots.
"""

import logging

from flask import session, request, jsonify
from pydantic import ValidationError

from shuffify.routes import (
    main,
    require_auth_and_db,
    json_error,
    json_success,
)
from shuffify.services import (
    PlaylistService,
    PlaylistSnapshotService,
    PlaylistSnapshotError,
    PlaylistSnapshotNotFoundError,
    StateService,
)
from shuffify.schemas import ManualSnapshotRequest
from shuffify.enums import SnapshotType

logger = logging.getLogger(__name__)


@main.route(
    "/playlist/<playlist_id>/snapshots", methods=["GET"]
)
@require_auth_and_db
def list_snapshots(playlist_id, client=None, user=None):
    """List all snapshots for a playlist."""
    limit = request.args.get("limit", 20, type=int)
    limit = max(1, min(limit, 100))

    snapshots = PlaylistSnapshotService.get_snapshots(
        user.id, playlist_id, limit=limit
    )
    return jsonify({
        "success": True,
        "snapshots": [s.to_dict() for s in snapshots],
    })


@main.route(
    "/playlist/<playlist_id>/snapshots", methods=["POST"]
)
@require_auth_and_db
def create_manual_snapshot(
    playlist_id, client=None, user=None
):
    """Create a manual snapshot of a playlist."""
    data = request.get_json()
    if not data:
        return json_error(
            "Request body must be JSON.", 400
        )

    try:
        snap_request = ManualSnapshotRequest(**data)
    except ValidationError as e:
        return json_error(
            f"Invalid request: {e.error_count()} "
            f"validation error(s).",
            400,
        )

    try:
        snapshot = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id=playlist_id,
            playlist_name=snap_request.playlist_name,
            track_uris=snap_request.track_uris,
            snapshot_type=SnapshotType.MANUAL,
            trigger_description=(
                snap_request.trigger_description
            ),
        )
        logger.info(
            f"User {user.spotify_id} created manual "
            f"snapshot for playlist {playlist_id}"
        )
        return json_success(
            "Snapshot created.",
            snapshot=snapshot.to_dict(),
        )
    except PlaylistSnapshotError as e:
        return json_error(str(e), 500)


@main.route(
    "/snapshots/<int:snapshot_id>", methods=["GET"]
)
@require_auth_and_db
def view_snapshot(snapshot_id, client=None, user=None):
    """View a snapshot's details."""
    try:
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user.id
        )
        return jsonify({
            "success": True,
            "snapshot": snapshot.to_dict(),
        })
    except PlaylistSnapshotNotFoundError:
        return json_error("Snapshot not found.", 404)


@main.route(
    "/snapshots/<int:snapshot_id>/restore",
    methods=["POST"],
)
@require_auth_and_db
def restore_snapshot(
    snapshot_id, client=None, user=None
):
    """Restore a playlist from a snapshot."""
    try:
        # Get the snapshot's track URIs
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user.id
        )
        restore_uris = snapshot.track_uris
        playlist_id = snapshot.playlist_id

        if not restore_uris:
            return json_error(
                "Snapshot contains no tracks.", 400
            )

        # Auto-snapshot the CURRENT state before restoring
        # (so the user can undo the restore)
        if PlaylistSnapshotService.is_auto_snapshot_enabled(
            user.id
        ):
            try:
                playlist_service = PlaylistService(client)
                playlist = playlist_service.get_playlist(
                    playlist_id, include_features=False
                )
                current_uris = [
                    t["uri"] for t in playlist.tracks
                ]
                PlaylistSnapshotService.create_snapshot(
                    user_id=user.id,
                    playlist_id=playlist_id,
                    playlist_name=playlist.name,
                    track_uris=current_uris,
                    snapshot_type=(
                        SnapshotType.AUTO_PRE_COMMIT
                    ),
                    trigger_description=(
                        f"Before restoring snapshot "
                        f"{snapshot_id}"
                    ),
                )
            except Exception as e:
                logger.warning(
                    "Failed to auto-snapshot before "
                    f"restore: {e}"
                )

        # Apply restoration to Spotify
        playlist_service = PlaylistService(client)
        playlist_service.update_playlist_tracks(
            playlist_id, restore_uris
        )

        # Update session state if it exists
        StateService.ensure_playlist_initialized(
            session, playlist_id, restore_uris
        )
        StateService.record_new_state(
            session, playlist_id, restore_uris
        )

        logger.info(
            f"Restored snapshot {snapshot_id} for "
            f"playlist {playlist_id}"
        )

        return json_success(
            f"Playlist restored from snapshot "
            f"({snapshot.track_count} tracks).",
            playlist_id=playlist_id,
            snapshot=snapshot.to_dict(),
        )

    except PlaylistSnapshotNotFoundError:
        return json_error("Snapshot not found.", 404)
    except Exception as e:
        logger.error(
            f"Failed to restore snapshot "
            f"{snapshot_id}: {e}",
            exc_info=True,
        )
        return json_error(
            "Failed to restore snapshot. "
            "Please try again.",
            500,
        )


@main.route(
    "/snapshots/<int:snapshot_id>", methods=["DELETE"]
)
@require_auth_and_db
def delete_snapshot(
    snapshot_id, client=None, user=None
):
    """Delete a snapshot."""
    try:
        PlaylistSnapshotService.delete_snapshot(
            snapshot_id, user.id
        )
        return json_success("Snapshot deleted.")
    except PlaylistSnapshotNotFoundError:
        return json_error("Snapshot not found.", 404)
    except PlaylistSnapshotError as e:
        return json_error(str(e), 500)
