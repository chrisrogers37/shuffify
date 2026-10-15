"""
Playlist pair (archive) routes.

Manages playlist-archive pairings and archive operations.
"""

import logging

from flask import jsonify, request
from pydantic import ValidationError

from shuffify.enums import ActivityType
from shuffify.routes import (
    api_bp,
    json_error,
    json_reply,
    json_success,
    log_activity,
    require_auth_and_db,
    require_session_and_db,
    validate_json,
)
from shuffify.routes.finalizer_queue import admit_finalizer
from shuffify.schemas.playlist_pair_requests import (
    CreatePairRequest,
    UnarchiveTracksRequest,
    UpdatePairRequest,
)
from shuffify.schemas.workshop_finalize_requests import (
    FinalizeArchiveRequest,
    FinalizeRestoreRequest,
)
from shuffify.services.api_response import busy_error
from shuffify.services.playlist_lock import PlaylistBusyError, playlist_configuration_lock
from shuffify.services.playlist_pair_service import (
    PlaylistPairExistsError,
    PlaylistPairNotFoundError,
    PlaylistPairService,
)
from shuffify.services.playlist_setup_service import CREATION_UNKNOWN, refusal
from shuffify.services.workshop_finalize_service import (
    WorkshopFinalizeResult,
    WorkshopFinalizeService,
)
from shuffify.spotify.exceptions import SpotifyWriteUnverifiedError

logger = logging.getLogger(__name__)


# The playlist-items resource names the nested object "track" on the legacy
# response shape and "item" on the current one. Readers accept whichever
# arrives, so a field filter has to ask for both -- naming only one strips the
# other back out of the response, yielding an empty list rather than an error.
_TRACK_SHAPE = "id,name,uri,artists(name),album(name,images),duration_ms"
PLAYLIST_ITEM_FIELDS = f"items(track({_TRACK_SHAPE}),item({_TRACK_SHAPE}))"


@api_bp.route(
    "/playlist/<playlist_id>/pair", methods=["GET"]
)
@require_auth_and_db
def get_pair(playlist_id, api=None, user=None):
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


@api_bp.route(
    "/playlist/<playlist_id>/pair", methods=["POST"]
)
@require_auth_and_db
def create_pair(playlist_id, api=None, user=None):
    """Create an archive pair for a playlist."""
    req, err = validate_json(CreatePairRequest)
    if err:
        return err

    try:
        # Serialize the existing-pair check, external creation, and DB
        # association. Busy responses must precede the Spotify write.
        with playlist_configuration_lock(playlist_id, timeout_s=2.0) as acquired:
            if not acquired:
                raise PlaylistBusyError("Playlist is busy; please retry.")

            if PlaylistPairService.get_pair_for_playlist(user.id, playlist_id):
                raise PlaylistPairExistsError("A pair already exists for this playlist")

            if req.create_new:
                prod_name = (
                    req.production_playlist_name or "Playlist"
                )
                try:
                    archive_id, archive_name = (
                        PlaylistPairService.create_archive_playlist(
                            api,
                            prod_name,
                        )
                    )
                except SpotifyWriteUnverifiedError:
                    logger.warning(
                        "Archive playlist creation for %s has an unknown outcome",
                        playlist_id,
                        exc_info=True,
                    )
                    return json_reply(refusal("creation_unknown", 502, CREATION_UNKNOWN, outcome="unknown"))
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
    except PlaylistBusyError:
        return json_reply(busy_error())
    except Exception as e:
        logger.error("Failed to create pair: %s", e)
        return json_error(
            "Failed to create archive pair", 500
        )


@api_bp.route(
    "/playlist/<playlist_id>/pair", methods=["PATCH"]
)
@require_auth_and_db
def update_pair(playlist_id, api=None, user=None):
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
    except PlaylistBusyError:
        return json_reply(busy_error())
    except Exception as e:
        logger.error("Failed to update pair: %s", e)
        return json_error(
            "Failed to update pair settings", 500
        )


@api_bp.route(
    "/playlist/<playlist_id>/pair", methods=["DELETE"]
)
@require_auth_and_db
def delete_pair(playlist_id, api=None, user=None):
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
    except PlaylistBusyError:
        return json_reply(busy_error())


def _rejected(count_key):
    def respond(message, status_code):
        result = WorkshopFinalizeResult.request_rejected(
            request.get_json(silent=True), message, status_code, count_key
        )
        return jsonify(result.to_dict()), result.http_status

    return respond


_archive_rejected = _rejected("archived_count")
_restore_rejected = _rejected("restored_count")


def _finalize_archive_task(playlist_id, user, *, restore):
    rejected = _restore_rejected if restore else _archive_rejected
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return rejected("Request body must be a JSON object.", 400)
    schema = FinalizeRestoreRequest if restore else FinalizeArchiveRequest
    try:
        req = schema(**data)
    except ValidationError:
        return rejected(
            "Review the source changes again; the captured task is incomplete or invalid.", 400
        )
    if not req.inspect_only:
        return admit_finalizer(user, playlist_id,
                               "workshop_finalize_restore" if restore else "workshop_archive_append", req)
    from shuffify.routes import require_auth

    api = require_auth()
    if api is None:
        return rejected("Please log in first.", 401)
    service = WorkshopFinalizeService(api)
    finalize = service.finalize_restore if restore else service.append_archive
    result = finalize(playlist_id, user, **req.model_dump())
    return jsonify(result.to_dict()), result.http_status


@api_bp.route("/playlist/<playlist_id>/pair/archive", methods=["POST"])
@require_session_and_db(on_preflight_error=_archive_rejected)
def archive_tracks(playlist_id, user=None):
    """Append the captured occurrences and verify the complete archive order."""
    return _finalize_archive_task(playlist_id, user, restore=False)


@api_bp.route(
    "/playlist/<playlist_id>/pair/unarchive",
    methods=["POST"],
)
@require_auth_and_db
def unarchive_tracks(
    playlist_id, api=None, user=None
):
    """Unarchive tracks back to the playlist the archive is paired with."""
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
            api,
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


@api_bp.route("/playlist/<playlist_id>/pair/finalize-restore", methods=["POST"])
@require_session_and_db(on_preflight_error=_restore_rejected)
def finalize_restore(playlist_id, user=None):
    """Verify target membership before removing all source URI occurrences."""
    return _finalize_archive_task(playlist_id, user, restore=True)


@api_bp.route(
    "/playlist/<playlist_id>/pair/archive-tracks",
    methods=["GET"],
)
@require_auth_and_db
def list_archive_tracks(
    playlist_id, api=None, user=None
):
    """List tracks in the archive playlist."""
    pair = PlaylistPairService.get_pair_for_playlist(
        user.id, playlist_id
    )
    if not pair:
        return json_error("No archive pair found", 404)

    try:
        track_fields = PLAYLIST_ITEM_FIELDS

        # Get total count first (cheap call, minimal items)
        # Spotify API rejects limit=0; use limit=1 instead
        count_result = api.get_playlist_items_raw(
            pair.archive_playlist_id,
            fields="total",
            limit=1,
        )
        total = count_result.get("total", 0)

        # Fetch only the last 25 (most recently archived)
        display_limit = 25
        offset = max(0, total - display_limit)
        results = api.get_playlist_items_raw(
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
