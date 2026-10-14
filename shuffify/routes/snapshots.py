"""
Snapshot routes: list, create, view, restore, and delete
playlist snapshots.
"""

import logging

from flask import jsonify, request, session

from shuffify.enums import SnapshotType
from shuffify.routes import (
    context_preflight,
    json_error,
    json_reply,
    json_success,
    main,
    no_store,
    require_auth_and_db,
    validate_json,
)
from shuffify.schemas import ManualSnapshotRequest
from shuffify.services import (
    PlaylistSnapshotError,
    PlaylistSnapshotNotFoundError,
    PlaylistSnapshotService,
)
from shuffify.services.live_action_context_service import LiveActionContextService
from shuffify.services.live_operation_service import (
    direct_rejected_result,
    direct_response_payload,
    preflight_error_code,
)
from shuffify.services.live_playlist_service import LivePlaylistService
from shuffify.services.live_review_bindings import (
    ReviewBindingError,
    invalid_binding_result,
    request_binding,
)
from shuffify.services.live_review_context import context_unavailable

logger = logging.getLogger(__name__)


@main.route(
    "/playlist/<playlist_id>/snapshots", methods=["GET"]
)
@require_auth_and_db
def list_snapshots(playlist_id, api=None, user=None):
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
    playlist_id, api=None, user=None
):
    """Create a manual snapshot of a playlist."""
    snap_request, err = validate_json(
        ManualSnapshotRequest
    )
    if err:
        return err

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
def view_snapshot(snapshot_id, api=None, user=None):
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


def _snapshot_restore_response(result):
    payload, status_code = direct_response_payload(result, "snapshot_restore")
    return jsonify(payload), status_code


def _snapshot_restore_preflight(message, status_code):
    return _snapshot_restore_response(direct_rejected_result(
        "snapshot_restore", error_code=preflight_error_code(status_code),
        http_status=status_code, retryable=status_code in (401, 503), message=message,
    ))


@main.route("/snapshots/<int:snapshot_id>/restore", methods=["POST"])
@require_auth_and_db(on_preflight_error=_snapshot_restore_preflight)
def restore_snapshot(snapshot_id, api=None, user=None):
    """Restore the owned snapshot through the verified direct target service."""
    # An optional reviewed bundle; other body keys stay ignored, and a
    # malformed bundle is refused before any database or Spotify read.
    try:
        reviewed = request_binding(request, "snapshot_restore")
    except ReviewBindingError:
        return _snapshot_restore_response(invalid_binding_result("snapshot_restore"))
    try:
        snapshot = PlaylistSnapshotService.get_snapshot(snapshot_id, user.id)
    except PlaylistSnapshotNotFoundError:
        return _snapshot_restore_response(direct_rejected_result(
            "snapshot_restore", error_code="snapshot_not_found", http_status=404,
            message="Snapshot not found.",
        ))
    except Exception:
        logger.exception("Snapshot lookup unavailable for %s", snapshot_id)
        return _snapshot_restore_response(direct_rejected_result(
            "snapshot_restore", error_code="snapshot_unavailable", http_status=503,
            retryable=True, message="Snapshot could not be read. No changes were sent.",
        ))
    try:
        result = LivePlaylistService(api).restore_snapshot(snapshot, user, session, reviewed=reviewed)
    except Exception:
        logger.exception("Snapshot restore outcome unavailable for %s", snapshot_id)
        result = direct_rejected_result(
            "snapshot_restore", error_code="outcome_unavailable", http_status=502, unknown=True,
            message="The restore could not be verified. Check the playlist before another change.",
        )
    return _snapshot_restore_response(result)


@main.route("/snapshots/<int:snapshot_id>/restore-context", methods=["GET"])
@no_store
@require_auth_and_db(on_preflight_error=context_preflight("snapshot_restore"))
def snapshot_restore_context(snapshot_id, api=None, user=None):
    """Read-only review of restoring one owned snapshot now; changes nothing."""
    try:
        reply = LiveActionContextService(api).snapshot_restore(snapshot_id, user)
    except Exception:
        logger.exception("Snapshot restore review unavailable for %s", snapshot_id)
        reply = context_unavailable("snapshot_restore")
    return json_reply(reply)


@main.route(
    "/snapshots/<int:snapshot_id>", methods=["DELETE"]
)
@require_auth_and_db
def delete_snapshot(
    snapshot_id, api=None, user=None
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
