"""
Track lock routes.

Manages per-track position locks within playlists.
Locks are set from the Workshop page and protect tracks
from shuffle, rotation, and drag-and-drop displacement.
"""

import logging

from flask import jsonify

from shuffify.routes import (
    main,
    require_auth_and_db,
    json_error,
    json_success,
    validate_json,
    log_activity,
)
from shuffify.enums import ActivityType
from shuffify.services.track_lock_service import (
    TrackLockService,
    TrackLockError,
)
from shuffify.schemas.track_lock_requests import (
    TrackLockToggleRequest,
    TrackLockBulkUnlockRequest,
)

logger = logging.getLogger(__name__)


@main.route(
    "/workshop/<playlist_id>/locks",
    methods=["GET"],
)
@require_auth_and_db
def get_track_locks(
    playlist_id, client=None, user=None
):
    """Get all active locks for a playlist."""
    try:
        locks = (
            TrackLockService.get_locks_for_playlist(
                user.id, playlist_id
            )
        )
        return jsonify({
            "success": True,
            "locks": [lock.to_dict() for lock in locks],
        })
    except TrackLockError as e:
        logger.error(
            "Failed to get locks for %s: %s",
            playlist_id, e,
        )
        return json_error(
            "Failed to load track locks", 500
        )


@main.route(
    "/workshop/<playlist_id>/locks/toggle",
    methods=["POST"],
)
@require_auth_and_db
def toggle_track_lock(
    playlist_id, client=None, user=None
):
    """Toggle lock tier for a track."""
    req, err = validate_json(TrackLockToggleRequest)
    if err:
        return err

    try:
        result = TrackLockService.toggle_lock(
            user.id,
            playlist_id,
            req.track_uri,
            req.position,
        )

        if result is None:
            log_activity(
                user_id=user.id,
                activity_type=ActivityType.TRACK_UNLOCK,
                description=(
                    "Unlocked track in playlist"
                ),
                playlist_id=playlist_id,
                metadata={
                    "track_uri": req.track_uri,
                    "position": req.position,
                },
            )
            return json_success(
                "Track unlocked",
                lock=None,
            )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.TRACK_LOCK,
            description=(
                f"Locked track "
                f"({result['lock_tier']}) "
                f"in playlist"
            ),
            playlist_id=playlist_id,
            metadata={
                "track_uri": req.track_uri,
                "position": req.position,
                "lock_tier": result["lock_tier"],
            },
        )
        return json_success(
            f"Track locked ({result['lock_tier']})",
            lock=result,
        )

    except TrackLockError as e:
        logger.error(
            "Failed to toggle lock: %s", e
        )
        return json_error(
            "Failed to toggle track lock", 500
        )


@main.route(
    "/workshop/<playlist_id>/locks/unlock-all",
    methods=["POST"],
)
@require_auth_and_db
def unlock_all_tracks(
    playlist_id, client=None, user=None
):
    """Unlock all (or specific) tracks in a playlist."""
    from flask import request as flask_request
    data = flask_request.get_json(silent=True) or {}

    try:
        req = TrackLockBulkUnlockRequest(**data)
    except Exception:
        return json_error("Invalid request.", 400)

    try:
        count = TrackLockService.bulk_unlock(
            user.id, playlist_id, req.track_uris
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.TRACK_UNLOCK,
            description=(
                f"Unlocked {count} tracks in playlist"
            ),
            playlist_id=playlist_id,
            metadata={"count": count},
        )

        return json_success(
            f"Unlocked {count} track(s)",
            count=count,
        )

    except TrackLockError as e:
        logger.error(
            "Failed to bulk unlock: %s", e
        )
        return json_error(
            "Failed to unlock tracks", 500
        )
