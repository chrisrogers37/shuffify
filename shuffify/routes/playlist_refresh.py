"""Share one refresh budget between the two views of the user's playlist list."""

import time

from flask import make_response, request, session

from shuffify.routes import is_authenticated, json_error


def playlist_refresh_requested():
    return request.args.get("refresh") == "1"


def _exempt_from_refresh_limit():
    profile = session.get("user_data")
    return not (
        isinstance(profile, dict)
        and profile.get("id")
        and playlist_refresh_requested()
        and is_authenticated()
    )


def _refresh_limit_response(limit):
    response = make_response(json_error(
        "Too many playlist refreshes. Try again in a minute, or omit refresh=1 to use cached playlists.", 429
    ))
    response.retry_after = max(1, int(limit.reset_at - time.time()))
    return response


def apply_playlist_refresh_limit(app, limiter):
    refresh_limit = limiter.shared_limit(
        "10/minute", scope="playlist-list-refresh", methods=["GET", "HEAD"],
        exempt_when=_exempt_from_refresh_limit, on_breach=_refresh_limit_response,
    )
    for endpoint in ("api.library_context", "api.api_user_playlists"):
        app.view_functions[endpoint] = refresh_limit(app.view_functions[endpoint])
