"""
Playlist pair (archive) routes.

Manages production-archive playlist pairings and archive operations.
"""

import logging

from flask import request, jsonify
from pydantic import ValidationError

from shuffify import is_db_available
from shuffify.routes import (
    main,
    require_auth,
    get_db_user,
    json_error,
    json_success,
)
from shuffify.services.playlist_pair_service import (
    PlaylistPairService,
    PlaylistPairExistsError,
    PlaylistPairNotFoundError,
)
from shuffify.services.activity_log_service import (
    ActivityLogService,
)
from shuffify.enums import ActivityType
from shuffify.schemas.playlist_pair_requests import (
    CreatePairRequest,
    ArchiveTracksRequest,
    UnarchiveTracksRequest,
)

logger = logging.getLogger(__name__)


@main.route(
    "/playlist/<playlist_id>/pair", methods=["GET"]
)
def get_pair(playlist_id):
    """Get archive pair info for a playlist."""
    sp = require_auth()
    if not sp:
        return json_error("Authentication required", 401)

    if not is_db_available():
        return json_error("Database unavailable", 503)

    user = get_db_user()
    if not user:
        return json_error("User not found", 404)

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
def create_pair(playlist_id):
    """Create an archive pair for a playlist."""
    sp = require_auth()
    if not sp:
        return json_error("Authentication required", 401)

    if not is_db_available():
        return json_error("Database unavailable", 503)

    user = get_db_user()
    if not user:
        return json_error("User not found", 404)

    data = request.get_json(silent=True)
    if not data:
        return json_error("JSON body required", 400)

    try:
        req = CreatePairRequest(**data)
    except ValidationError as e:
        return json_error(str(e.errors()[0]["msg"]), 400)

    try:
        if req.create_new:
            prod_name = (
                req.production_playlist_name or "Playlist"
            )
            archive_id, archive_name = (
                PlaylistPairService.create_archive_playlist(
                    sp._sp,
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

        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.PAIR_CREATE,
            description=(
                f"Paired with archive '{archive_name}'"
            ),
            playlist_id=playlist_id,
            playlist_name=req.production_playlist_name,
        )

        return jsonify({
            "success": True,
            "message": "Archive pair created",
            "pair": pair.to_dict(),
        })

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
    "/playlist/<playlist_id>/pair", methods=["DELETE"]
)
def delete_pair(playlist_id):
    """Remove the archive pair for a playlist."""
    sp = require_auth()
    if not sp:
        return json_error("Authentication required", 401)

    if not is_db_available():
        return json_error("Database unavailable", 503)

    user = get_db_user()
    if not user:
        return json_error("User not found", 404)

    try:
        PlaylistPairService.delete_pair(
            user.id, playlist_id
        )
        ActivityLogService.log(
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
def archive_tracks(playlist_id):
    """Archive tracks to the paired archive playlist."""
    sp = require_auth()
    if not sp:
        return json_error("Authentication required", 401)

    if not is_db_available():
        return json_error("Database unavailable", 503)

    user = get_db_user()
    if not user:
        return json_error("User not found", 404)

    pair = PlaylistPairService.get_pair_for_playlist(
        user.id, playlist_id
    )
    if not pair:
        return json_error("No archive pair found", 404)

    data = request.get_json(silent=True)
    if not data:
        return json_error("JSON body required", 400)

    try:
        req = ArchiveTracksRequest(**data)
    except ValidationError as e:
        return json_error(str(e.errors()[0]["msg"]), 400)

    try:
        count = PlaylistPairService.archive_tracks(
            sp._sp, pair.archive_playlist_id, req.track_uris
        )
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.ARCHIVE_TRACKS,
            description=(
                f"Archived {count} tracks"
            ),
            playlist_id=playlist_id,
            metadata={"track_count": count},
        )
        return jsonify({
            "success": True,
            "message": f"Archived {count} tracks",
            "archived_count": count,
        })
    except Exception as e:
        logger.error("Failed to archive tracks: %s", e)
        return json_error(
            "Failed to archive tracks", 500
        )


@main.route(
    "/playlist/<playlist_id>/pair/unarchive",
    methods=["POST"],
)
def unarchive_tracks(playlist_id):
    """Unarchive tracks back to production playlist."""
    sp = require_auth()
    if not sp:
        return json_error("Authentication required", 401)

    if not is_db_available():
        return json_error("Database unavailable", 503)

    user = get_db_user()
    if not user:
        return json_error("User not found", 404)

    pair = PlaylistPairService.get_pair_for_playlist(
        user.id, playlist_id
    )
    if not pair:
        return json_error("No archive pair found", 404)

    data = request.get_json(silent=True)
    if not data:
        return json_error("JSON body required", 400)

    try:
        req = UnarchiveTracksRequest(**data)
    except ValidationError as e:
        return json_error(str(e.errors()[0]["msg"]), 400)

    try:
        count = PlaylistPairService.unarchive_tracks(
            sp._sp,
            pair.production_playlist_id,
            pair.archive_playlist_id,
            req.track_uris,
        )
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.UNARCHIVE_TRACKS,
            description=(
                f"Unarchived {count} tracks"
            ),
            playlist_id=playlist_id,
            metadata={"track_count": count},
        )
        return jsonify({
            "success": True,
            "message": f"Unarchived {count} tracks",
            "unarchived_count": count,
        })
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
def list_archive_tracks(playlist_id):
    """List tracks in the archive playlist."""
    sp = require_auth()
    if not sp:
        return json_error("Authentication required", 401)

    if not is_db_available():
        return json_error("Database unavailable", 503)

    user = get_db_user()
    if not user:
        return json_error("User not found", 404)

    pair = PlaylistPairService.get_pair_for_playlist(
        user.id, playlist_id
    )
    if not pair:
        return json_error("No archive pair found", 404)

    try:
        results = sp._sp.playlist_items(
            pair.archive_playlist_id,
            fields=(
                "items(track(id,name,uri,artists(name),"
                "album(name,images),duration_ms))"
            ),
            limit=100,
        )
        tracks = []
        for item in results.get("items", []):
            track = item.get("track")
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
            "total": len(tracks),
        })
    except Exception as e:
        logger.error(
            "Failed to list archive tracks: %s", e
        )
        return json_error(
            "Failed to load archive tracks", 500
        )
