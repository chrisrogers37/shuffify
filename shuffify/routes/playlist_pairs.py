"""
Playlist pair (archive) routes.

Manages production-archive playlist pairings and archive operations.
"""

import logging

from flask import jsonify

from shuffify.routes import (
    main,
    require_auth_and_db,
    json_error,
    json_success,
    log_activity,
    validate_json,
)
from shuffify.services.playlist_pair_service import (
    PlaylistPairService,
    PlaylistPairExistsError,
    PlaylistPairNotFoundError,
)
from shuffify.enums import ActivityType
from shuffify.schemas.playlist_pair_requests import (
    CreatePairRequest,
    UpdatePairRequest,
    ArchiveTracksRequest,
    UnarchiveTracksRequest,
)

logger = logging.getLogger(__name__)


@main.route(
    "/playlist/<playlist_id>/pair", methods=["GET"]
)
@require_auth_and_db
def get_pair(playlist_id, client=None, user=None):
    """Get archive pair info for a playlist."""
    pair = PlaylistPairService.get_pair_for_playlist(
        user.id, playlist_id
    )
    if not pair:
        return jsonify({"success": True, "paired": False})

    return jsonify({
        "success": True,
        "paired": True,
        "pair": pair.to_dict(),
    })


@main.route(
    "/playlist/<playlist_id>/pair", methods=["POST"]
)
@require_auth_and_db
def create_pair(playlist_id, client=None, user=None):
    """Create an archive pair for a playlist."""
    req, err = validate_json(CreatePairRequest)
    if err:
        return err

    try:
        if req.create_new:
            prod_name = (
                req.production_playlist_name or "Playlist"
            )
            archive_id, archive_name = (
                PlaylistPairService.create_archive_playlist(
                    client.api,
                    user.spotify_id,
                    prod_name,
                )
            )
        else:
            archive_id = req.archive_playlist_id
            archive_name = req.archive_playlist_name

        pair = PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id=playlist_id,
            archive_playlist_id=archive_id,
            production_playlist_name=(
                req.production_playlist_name
            ),
            archive_playlist_name=archive_name,
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.PAIR_CREATE,
            description=(
                f"Paired with archive '{archive_name}'"
            ),
            playlist_id=playlist_id,
            playlist_name=req.production_playlist_name,
        )

        return json_success(
            "Archive pair created",
            pair=pair.to_dict(),
        )

    except PlaylistPairExistsError:
        return json_error(
            "A pair already exists for this playlist", 409
        )
    except Exception as e:
        logger.error("Failed to create pair: %s", e)
        return json_error(
            "Failed to create archive pair", 500
        )


@main.route(
    "/playlist/<playlist_id>/pair", methods=["PATCH"]
)
@require_auth_and_db
def update_pair(playlist_id, client=None, user=None):
    """Update archive pair settings."""
    req, err = validate_json(UpdatePairRequest)
    if err:
        return err

    try:
        pair = PlaylistPairService.update_pair(
            user.id,
            playlist_id,
            auto_archive_on_remove=(
                req.auto_archive_on_remove
            ),
        )
        return json_success(
            "Pair settings updated",
            pair=pair.to_dict(),
        )
    except PlaylistPairNotFoundError:
        return json_error("No pair found", 404)
    except Exception as e:
        logger.error("Failed to update pair: %s", e)
        return json_error(
            "Failed to update pair settings", 500
        )


@main.route(
    "/playlist/<playlist_id>/pair", methods=["DELETE"]
)
@require_auth_and_db
def delete_pair(playlist_id, client=None, user=None):
    """Remove the archive pair for a playlist."""
    try:
        PlaylistPairService.delete_pair(
            user.id, playlist_id
        )
        log_activity(
            user_id=user.id,
            activity_type=ActivityType.PAIR_DELETE,
            description="Removed archive pairing",
            playlist_id=playlist_id,
        )
        return json_success("Archive pair removed")
    except PlaylistPairNotFoundError:
        return json_error("No pair found", 404)


@main.route(
    "/playlist/<playlist_id>/pair/archive",
    methods=["POST"],
)
@require_auth_and_db
def archive_tracks(playlist_id, client=None, user=None):
    """Archive tracks to the paired archive playlist."""
    pair = PlaylistPairService.get_pair_for_playlist(
        user.id, playlist_id
    )
    if not pair:
        return json_error("No archive pair found", 404)

    req, err = validate_json(ArchiveTracksRequest)
    if err:
        return err

    try:
        count = PlaylistPairService.archive_tracks(
            client.api,
            pair.archive_playlist_id,
            req.track_uris,
        )
        log_activity(
            user_id=user.id,
            activity_type=ActivityType.ARCHIVE_TRACKS,
            description=(
                f"Archived {count} tracks"
            ),
            playlist_id=playlist_id,
            metadata={"track_count": count},
        )
        return json_success(
            f"Archived {count} tracks",
            archived_count=count,
        )
    except Exception as e:
        logger.error("Failed to archive tracks: %s", e)
        return json_error(
            "Failed to archive tracks", 500
        )


@main.route(
    "/playlist/<playlist_id>/pair/unarchive",
    methods=["POST"],
)
@require_auth_and_db
def unarchive_tracks(
    playlist_id, client=None, user=None
):
    """Unarchive tracks back to production playlist."""
    pair = PlaylistPairService.get_pair_for_playlist(
        user.id, playlist_id
    )
    if not pair:
        return json_error("No archive pair found", 404)

    req, err = validate_json(UnarchiveTracksRequest)
    if err:
        return err

    try:
        count = PlaylistPairService.unarchive_tracks(
            client.api,
            pair.production_playlist_id,
            pair.archive_playlist_id,
            req.track_uris,
        )
        log_activity(
            user_id=user.id,
            activity_type=ActivityType.UNARCHIVE_TRACKS,
            description=(
                f"Unarchived {count} tracks"
            ),
            playlist_id=playlist_id,
            metadata={"track_count": count},
        )
        return json_success(
            f"Unarchived {count} tracks",
            unarchived_count=count,
        )
    except Exception as e:
        logger.error(
            "Failed to unarchive tracks: %s", e
        )
        return json_error(
            "Failed to unarchive tracks", 500
        )


@main.route(
    "/playlist/<playlist_id>/pair/archive-tracks",
    methods=["GET"],
)
@require_auth_and_db
def list_archive_tracks(
    playlist_id, client=None, user=None
):
    """List tracks in the archive playlist."""
    pair = PlaylistPairService.get_pair_for_playlist(
        user.id, playlist_id
    )
    if not pair:
        return json_error("No archive pair found", 404)

    try:
        # TODO: Spotify API migrating "track" → "item" key.
        # Field filter requests both until migration completes.
        track_fields = (
            "items(track(id,name,uri,artists(name),"
            "album(name,images),duration_ms))"
        )

        # Get total count first (cheap call, minimal items)
        # Spotify API rejects limit=0; use limit=1 instead
        count_result = client.api.get_playlist_items_raw(
            pair.archive_playlist_id,
            fields="total",
            limit=1,
        )
        total = count_result.get("total", 0)

        # Fetch only the last 25 (most recently archived)
        display_limit = 25
        offset = max(0, total - display_limit)
        results = client.api.get_playlist_items_raw(
            pair.archive_playlist_id,
            fields=track_fields,
            limit=display_limit,
            offset=offset,
        )

        tracks = []
        for item in results.get("items", []):
            track = (
                item.get("track") or item.get("item")
            )
            if not track or not track.get("uri"):
                continue
            artists = [
                a["name"]
                for a in track.get("artists", [])
            ]
            album = track.get("album", {})
            images = album.get("images", [])
            tracks.append({
                "id": track.get("id"),
                "name": track.get("name"),
                "uri": track.get("uri"),
                "artists": artists,
                "album_name": album.get("name", ""),
                "album_image_url": (
                    images[0]["url"] if images else ""
                ),
                "duration_ms": track.get(
                    "duration_ms", 0
                ),
            })

        return jsonify({
            "success": True,
            "tracks": tracks,
            "total": total,
        })
    except Exception as e:
        logger.error(
            "Failed to list archive tracks: %s", e
        )
        return json_error(
            "Failed to load archive tracks", 500
        )
