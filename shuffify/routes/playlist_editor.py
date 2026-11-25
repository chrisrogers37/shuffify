"""Caller and provider checks for routes that require a fresh playlist editor proof."""

from flask import session
from pydantic import TypeAdapter, ValidationError

from shuffify.routes import json_error
from shuffify.schemas.workshop_finalize_requests import PlaylistID
from shuffify.spotify.exceptions import SpotifyForbiddenError, SpotifyNotFoundError


def playlist_editor_request_error(playlist_id, user):
    """Bind the provider path and the database user to this signed-in request."""
    try:
        TypeAdapter(PlaylistID).validate_python(playlist_id)
    except ValidationError:
        return json_error("Invalid playlist ID.", 400)
    if user.spotify_id != (session.get("user_data") or {}).get("id"):
        return json_error("Please log in again.", 401)


def read_editor_playlist(api, playlist_id, spotify_id, *, with_snapshot=False):
    """Return fresh target metadata and prove non-owner membership with the caller's token."""
    try:
        if with_snapshot:
            playlist = api.get_playlist(playlist_id, skip_cache=True)
            snapshot = playlist.get("snapshot_id") if isinstance(playlist, dict) else None
            if not isinstance(snapshot, str) or not snapshot or playlist.get("id") != playlist_id:
                return None, json_error("Spotify could not confirm the playlist version.", 502)
        else:
            playlist = api.get_playlist_summary(playlist_id)
            if not isinstance(playlist, dict) or playlist.get("id") != playlist_id:
                return None, json_error("Spotify could not confirm the requested playlist.", 502)
        if (playlist.get("owner") or {}).get("id") != spotify_id:
            if playlist.get("collaborative") is not True:
                raise SpotifyForbiddenError("Not a collaborative playlist")
            # Spotify restricts /items to editors. A valid total of zero proves access too.
            items = api.get_playlist_items_raw(playlist_id, fields="total", limit=1)
            total = items.get("total") if isinstance(items, dict) else None
            if type(total) is not int or total < 0:
                return None, json_error("Spotify could not confirm playlist access.", 502)
    except (SpotifyForbiddenError, SpotifyNotFoundError):
        return None, json_error("This playlist is unavailable or you cannot edit it.", 403)
    return playlist, None
