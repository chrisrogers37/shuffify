"""
Tests for playlist routes.

Tests cover refresh-playlists, get-playlist, get-stats,
and user-playlists API.
"""

import time
import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db
from shuffify.services.user_service import UserService


@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"
    os.environ.pop("DATABASE_URL", None)

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///:memory:"
    )
    app.config["TESTING"] = True
    app.config["SCHEDULER_ENABLED"] = False

    with app.app_context():
        db.drop_all()
        db.create_all()
        UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def auth_client(db_app):
    """Authenticated test client with session user data."""
    with db_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "expires_at": time.time() + 3600,
                "refresh_token": "test_refresh",
            }
            sess["user_data"] = {
                "id": "user123",
                "display_name": "Test User",
            }
        yield client


class TestRefreshPlaylists:
    """Tests for POST /refresh-playlists."""

    @patch("shuffify.routes.playlists.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post("/refresh-playlists")
            assert resp.status_code == 401

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_success_returns_playlists(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = [
            {"id": "p1", "name": "Playlist 1"}
        ]
        mock_ps_class.return_value = mock_ps

        resp = auth_client.post("/refresh-playlists")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 1
        mock_ps.get_user_playlists.assert_called_once_with(
            skip_cache=True
        )

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_playlist_error_returns_500(
        self, mock_auth, mock_ps_class, auth_client
    ):
        from shuffify.services import PlaylistError

        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.side_effect = (
            PlaylistError("fail")
        )
        mock_ps_class.return_value = mock_ps

        resp = auth_client.post("/refresh-playlists")
        assert resp.status_code == 500


class TestGetPlaylist:
    """Tests for GET /playlist/<playlist_id>."""

    @patch("shuffify.routes.playlists.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/playlist/p1")
            assert resp.status_code == 401

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_returns_playlist_dict(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_playlist = MagicMock()
        mock_playlist.to_dict.return_value = {
            "id": "p1",
            "name": "My Playlist",
        }
        mock_ps = MagicMock()
        mock_ps.get_playlist.return_value = mock_playlist
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get("/playlist/p1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == "p1"

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_features_param_passed_through(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_playlist = MagicMock()
        mock_playlist.to_dict.return_value = {"id": "p1"}
        mock_ps = MagicMock()
        mock_ps.get_playlist.return_value = mock_playlist
        mock_ps_class.return_value = mock_ps

        auth_client.get("/playlist/p1?features=true")
        mock_ps.get_playlist.assert_called_once_with(
            "p1", True
        )


class TestGetPlaylistStats:
    """Tests for GET /playlist/<playlist_id>/stats."""

    @patch("shuffify.routes.playlists.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/playlist/p1/stats")
            assert resp.status_code == 401

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_returns_stats(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_playlist_stats.return_value = {
            "avg_tempo": 120.0
        }
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get("/playlist/p1/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "avg_tempo" in data


class TestApiUserPlaylists:
    """Tests for GET /api/user-playlists."""

    @patch("shuffify.routes.playlists.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/api/user-playlists")
            assert resp.status_code == 401

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_returns_formatted_playlists(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = [
            {
                "id": "p1",
                "name": "Playlist 1",
                "tracks": {"total": 10},
                "images": [
                    {"url": "https://example.com/img.jpg"}
                ],
            }
        ]
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get("/api/user-playlists")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 1
        assert data["playlists"][0]["track_count"] == 10
        assert (
            data["playlists"][0]["image_url"] is not None
        )

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_playlist_without_images(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = [
            {
                "id": "p1",
                "name": "No Image",
                "tracks": {"total": 5},
            }
        ]
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get("/api/user-playlists")
        data = resp.get_json()
        assert data["playlists"][0]["image_url"] is None

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_playlist_error_returns_500(
        self, mock_auth, mock_ps_class, auth_client
    ):
        from shuffify.services import PlaylistError

        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.side_effect = (
            PlaylistError("fail")
        )
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get("/api/user-playlists")
        assert resp.status_code == 500
