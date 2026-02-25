"""
Playlist preference routes.

Manages per-user playlist display ordering, visibility,
and pinning.
"""

import logging

from shuffify.routes import (
    main,
    require_auth_and_db,
    json_error,
    json_success,
    validate_json,
)
from shuffify.services.playlist_preference_service import (
    PlaylistPreferenceService,
    PlaylistPreferenceError,
)
from shuffify.schemas.playlist_preference_requests import (
    SaveOrderRequest,
)

logger = logging.getLogger(__name__)


@main.route(
    "/api/playlist-preferences/order",
    methods=["POST"],
)
@require_auth_and_db
def save_playlist_order(client=None, user=None):
    """Save reordered playlist IDs."""
    req, err = validate_json(SaveOrderRequest)
    if err:
        return err

    try:
        count = PlaylistPreferenceService.save_order(
            user.id, req.playlist_ids
        )
        return json_success(
            f"Saved order for {count} playlists",
            count=count,
        )
    except PlaylistPreferenceError as e:
        logger.error("Failed to save order: %s", e)
        return json_error(
            "Failed to save playlist order", 500
        )


@main.route(
    "/api/playlist-preferences/"
    "<playlist_id>/toggle-hidden",
    methods=["POST"],
)
@require_auth_and_db
def toggle_playlist_hidden(
    playlist_id, client=None, user=None
):
    """Toggle hidden state for a playlist."""
    try:
        is_hidden = (
            PlaylistPreferenceService.toggle_hidden(
                user.id, playlist_id
            )
        )
        action = "hidden" if is_hidden else "shown"
        return json_success(
            f"Playlist {action}",
            is_hidden=is_hidden,
        )
    except PlaylistPreferenceError as e:
        logger.error(
            "Failed to toggle hidden: %s", e
        )
        return json_error(
            "Failed to update visibility", 500
        )


@main.route(
    "/api/playlist-preferences/"
    "<playlist_id>/toggle-pinned",
    methods=["POST"],
)
@require_auth_and_db
def toggle_playlist_pinned(
    playlist_id, client=None, user=None
):
    """Toggle pinned state for a playlist."""
    try:
        is_pinned = (
            PlaylistPreferenceService.toggle_pinned(
                user.id, playlist_id
            )
        )
        action = "pinned" if is_pinned else "unpinned"
        return json_success(
            f"Playlist {action}",
            is_pinned=is_pinned,
        )
    except PlaylistPreferenceError as e:
        logger.error(
            "Failed to toggle pinned: %s", e
        )
        return json_error(
            "Failed to update pin state", 500
        )


@main.route(
    "/api/playlist-preferences/reset",
    methods=["POST"],
)
@require_auth_and_db
def reset_playlist_preferences(
    client=None, user=None
):
    """Reset all playlist preferences."""
    try:
        count = (
            PlaylistPreferenceService.reset_preferences(
                user.id
            )
        )
        return json_success(
            f"Reset {count} playlist preferences",
            count=count,
        )
    except PlaylistPreferenceError as e:
        logger.error(
            "Failed to reset preferences: %s", e
        )
        return json_error(
            "Failed to reset preferences", 500
        )
