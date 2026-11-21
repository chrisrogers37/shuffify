"""Forced playlist reads share a per-user limit before Spotify client construction."""

import time
from unittest.mock import call, create_autospec, patch

import pytest

from config import TestConfig
from shuffify import create_app
from shuffify.services import AuthService, UserService
from shuffify.spotify.api import SpotifyAPI

PATHS = ("/api/library", "/api/user-playlists")
SHARED_IP = {"REMOTE_ADDR": "192.0.2.10"}
OTHER_IP = {"REMOTE_ADDR": "198.51.100.20"}


@pytest.fixture
def refresh_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test_id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test_secret")
    monkeypatch.setattr(TestConfig, "REDIS_URL", None)
    monkeypatch.chdir(tmp_path)
    return create_app("testing")


def sign_in(app, user_id, token):
    profile = {"id": user_id, "display_name": user_id}
    with app.app_context():
        UserService.upsert_from_spotify(profile)
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_data=profile, spotify_token=token)
    return client


def test_shared_refresh_budget(refresh_app, sample_token):
    alice = sign_in(refresh_app, "alice", sample_token)
    alice_second_session = sign_in(refresh_app, "alice", sample_token)
    bob = sign_in(refresh_app, "bob", sample_token)
    spotify = create_autospec(SpotifyAPI, instance=True)
    spotify.get_user_playlists.return_value = []
    start = time.time()
    with (
        patch.object(AuthService, "get_authenticated_api", autospec=True, return_value=spotify) as build_client,
        patch("time.time", return_value=start) as now,
    ):
        for path in PATHS:
            assert alice.get(path, environ_base=SHARED_IP).status_code == 200
            assert alice.options(path + "?refresh=1", environ_base=SHARED_IP).status_code == 200
        assert spotify.get_user_playlists.call_args_list == [call(skip_cache=False)] * 2
        build_client.reset_mock()
        spotify.reset_mock()

        for path in PATHS * 5:
            assert alice.get(path + "?refresh=1", environ_base=SHARED_IP).status_code == 200
        assert spotify.get_user_playlists.call_args_list == [call(skip_cache=True)] * 10
        assert build_client.call_count == 10

        now.return_value = start + 30
        for path in PATHS:
            blocked = alice_second_session.get(path + "?refresh=1", environ_base=OTHER_IP)
            assert blocked.status_code == 429
            assert blocked.json["success"] is False
            assert blocked.json["error_code"] == "rate_limited"
            assert "refresh=1" in blocked.json["message"]
            assert 30 <= int(blocked.headers["Retry-After"]) <= 31
            assert alice.head(path + "?refresh=1", environ_base=SHARED_IP).status_code == 429
        assert build_client.call_count == spotify.get_user_playlists.call_count == 10

        spotify.reset_mock()
        for path in PATHS:
            for query in ("", "?refresh=0", "?refresh=true"):
                assert alice.get(path + query, environ_base=SHARED_IP).status_code == 200
        assert spotify.get_user_playlists.call_args_list == [call(skip_cache=False)] * 6

        assert bob.get(PATHS[1] + "?refresh=1", environ_base=SHARED_IP).status_code == 200
        spotify.get_user_playlists.assert_called_with(skip_cache=True)
        now.return_value = start + 61
        assert alice.get(PATHS[0] + "?refresh=1", environ_base=OTHER_IP).status_code == 200
        spotify.get_user_playlists.assert_called_with(skip_cache=True)


@pytest.mark.parametrize("profile_only", [False, True])
def test_signed_out_refresh_keeps_auth_response(refresh_app, profile_only):
    client = refresh_app.test_client()
    if profile_only:
        with client.session_transaction() as session:
            session["user_data"] = {"id": "alice"}

    with patch.object(AuthService, "get_authenticated_api", autospec=True) as build_client:
        for path in PATHS * 6:
            response = client.get(path + "?refresh=1", environ_base=SHARED_IP)
            assert response.status_code == 401
            assert response.json["error_code"] == "authentication_required"
            assert response.json["message"] == "Please log in first."
        build_client.assert_not_called()
