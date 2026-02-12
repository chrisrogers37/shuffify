"""
Playlist API routes: fetch, refresh, and query playlists.
"""

import logging

from flask import request, jsonify

from shuffify.routes import main, require_auth, json_error, json_success
from shuffify.services import PlaylistService, PlaylistError
from shuffify.schemas import PlaylistQueryParams

logger = logging.getLogger(__name__)


@main.route("/refresh-playlists", methods=["POST"])
def refresh_playlists():
    """Refresh playlists from Spotify without losing undo state."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    try:
        playlist_service = PlaylistService(client)
        playlists = playlist_service.get_user_playlists(
            skip_cache=True
        )

        logger.info(
            f"Refreshed {len(playlists)} playlists from Spotify"
        )
        return json_success(
            "Playlists refreshed successfully.",
            playlists=playlists,
        )
    except PlaylistError as e:
        logger.error(f"Failed to refresh playlists: {e}")
        return json_error(
            "Failed to refresh playlists. Please try again.",
            500,
        )


@main.route("/playlist/<playlist_id>")
def get_playlist(playlist_id):
    """Get playlist data with optional audio features."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    query_params = PlaylistQueryParams(
        features=request.args.get("features", "false")
    )

    playlist_service = PlaylistService(client)
    playlist = playlist_service.get_playlist(
        playlist_id, query_params.features
    )
    return jsonify(playlist.to_dict())


@main.route("/playlist/<playlist_id>/stats")
def get_playlist_stats(playlist_id):
    """Get playlist audio feature statistics."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    playlist_service = PlaylistService(client)
    stats = playlist_service.get_playlist_stats(playlist_id)
    return jsonify(stats)


@main.route("/api/user-playlists")
def api_user_playlists():
    """Return the user's editable playlists as JSON."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    try:
        playlist_service = PlaylistService(client)
        playlists = playlist_service.get_user_playlists()

        result = []
        for p in playlists:
            result.append({
                "id": p["id"],
                "name": p["name"],
                "track_count": p.get("tracks", {}).get(
                    "total", 0
                ),
                "image_url": (
                    p["images"][0]["url"]
                    if p.get("images")
                    else None
                ),
            })

        logger.debug(f"API returned {len(result)} playlists")
        return jsonify({"success": True, "playlists": result})
    except PlaylistError as e:
        logger.error(
            f"Failed to fetch playlists for API: {e}"
        )
        return json_error(
            "Failed to fetch playlists.", 500
        )
