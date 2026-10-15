"""One Spotify time budget spans each API request, including authentication and paging."""

from flask import g, request

from shuffify.spotify.deadline import spotify_deadline


def register_spotify_deadline(app):
    """Bound Spotify work below the web server timeout and reset context on every exit."""
    @app.before_request
    def begin_spotify_budget():
        if request.path == "/api" or request.path.startswith("/api/"):
            scope = spotify_deadline(app.config.get("SPOTIFY_READ_BUDGET_SECONDS", 20))
            scope.__enter__()
            g._spotify_deadline_scope = scope

    @app.teardown_request
    def end_spotify_budget(error):
        scope = g.pop("_spotify_deadline_scope", None)
        if scope is not None:
            scope.__exit__(None, None, None)
