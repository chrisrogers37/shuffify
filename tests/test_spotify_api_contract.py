"""The injected Spotify client is a SpotifyAPI, and call sites match it.

Retiring the SpotifyClient facade (SR-031) moved every caller onto SpotifyAPI
directly. The facade forwarded most calls unchanged but renamed one on the way
through (``get_track_audio_features`` -> ``get_audio_features``), so a call
site inherited from the facade names a method that does not exist -- and it
fails at runtime, in whichever route happens to be exercised, not at import.

These are static and exhaustive on purpose: they cover routes no functional
test drives, which is most of the 77 that receive the injected client.
"""

import ast
import pathlib

import pytest

from shuffify.routes import require_auth
from shuffify.spotify.api import SpotifyAPI

SOURCE_DIRS = [
    pathlib.Path("shuffify/routes"),
    pathlib.Path("shuffify/services"),
    # Playlist.from_spotify lives here and is the sole call site of the method
    # the facade renamed; omitting models/ would skip the exact defect class.
    pathlib.Path("shuffify/models"),
]


def _source_files():
    for directory in SOURCE_DIRS:
        yield from sorted(directory.rglob("*.py"))


def _api_call_sites():
    """Map every ``api.<attr>`` / ``self._api.<attr>`` to where it is used."""
    called = {}
    for path in _source_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            base = node.value
            is_api = (isinstance(base, ast.Name) and base.id == "api") or (
                isinstance(base, ast.Attribute) and base.attr == "_api"
            )
            if is_api:
                called.setdefault(node.attr, set()).add(f"{path}:{node.lineno}")
    return called


class TestApiCallSites:
    def test_every_call_site_resolves_on_spotify_api(self):
        available = {a for a in dir(SpotifyAPI) if not a.startswith("__")}
        unknown = {
            method: sorted(sites)
            for method, sites in _api_call_sites().items()
            if method not in available
        }
        assert unknown == {}

    def test_the_scan_actually_finds_call_sites(self):
        """Guard the check above against passing because it found nothing."""
        assert len(_api_call_sites()) >= 10

    def test_the_facades_old_method_name_is_gone(self):
        """``get_track_audio_features`` was the facade's name for it."""
        assert "get_track_audio_features" not in _api_call_sites()
        assert hasattr(SpotifyAPI, "get_audio_features")


class TestInjectedClient:
    def test_require_auth_returns_a_spotify_api(self, app, sample_token):
        from flask import session

        # Config reads these at class-definition time, so the env vars the app
        # fixture exports land too late to reach current_app.config. Set them
        # here rather than mock the credential lookup -- the point of this test
        # is that the real resolution path produces a real SpotifyAPI.
        app.config.update(
            SPOTIFY_CLIENT_ID="contract_client_id",
            SPOTIFY_CLIENT_SECRET="contract_client_secret",
            SPOTIFY_REDIRECT_URI="http://localhost:5000/callback",
        )

        with app.test_request_context():
            session["spotify_token"] = sample_token
            assert isinstance(require_auth(), SpotifyAPI)

    def test_no_route_still_declares_a_client_kwarg(self):
        """The decorator injects ``api``; a stale ``client`` param is a TypeError.

        Nothing statically references the injected name, so a missed rename is
        invisible until that route is requested.
        """
        stale = []
        for path in sorted(pathlib.Path("shuffify/routes").glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    args = node.args.args + node.args.kwonlyargs
                    if any(a.arg == "client" for a in args):
                        stale.append(f"{path}:{node.lineno} {node.name}")
        assert stale == []

    def test_routes_do_declare_the_injected_kwarg(self):
        """Counterpart to the check above, so it cannot pass vacuously."""
        declaring = 0
        for path in sorted(pathlib.Path("shuffify/routes").glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    args = node.args.args + node.args.kwonlyargs
                    if any(a.arg == "api" for a in args):
                        declaring += 1
        assert declaring >= 50


AUTHENTICATED_GET_ROUTES = [
    "/api/user-playlists",
    "/playlist/playlist123",
    "/playlist/playlist123/stats",
    "/activity",
    "/schedules",
    "/settings",
    "/workshop",
    "/workshop/playlist123",
    "/playlist/playlist123/pair",
]


class TestAuthenticatedRouteDispatch:
    """Routes dispatch through the real decorator with the network stubbed.

    Stubbed at the HTTP boundary rather than at SpotifyAPI: a mocked API would
    answer to any method name and prove nothing about the rename.
    """

    @pytest.mark.parametrize("route", AUTHENTICATED_GET_ROUTES)
    def test_route_dispatches(self, auth_client, route, monkeypatch):
        from unittest.mock import MagicMock

        http = MagicMock()
        http.get.return_value = {
            "id": "playlist123",
            "name": "Contract",
            "owner": {"id": "user123"},
            "tracks": {"total": 0, "items": []},
            "collaborative": False,
            "items": [],
            "total": 0,
        }
        http.get_all_pages.return_value = []
        http.post.return_value = {"snapshot_id": "s"}
        http.put.return_value = {"snapshot_id": "s"}
        http.delete.return_value = None
        monkeypatch.setattr(
            "shuffify.spotify.api.SpotifyHTTPClient", lambda *a, **kw: http
        )

        response = auth_client.get(route)

        # Any real answer is fine -- 2xx, a redirect, even a 4xx. Only a route
        # that raised or 500'd indicates the refactor broke its wiring.
        assert response.status_code < 500
