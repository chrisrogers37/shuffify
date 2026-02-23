"""
Tests for shuffle routes.

Tests cover POST /shuffle/<playlist_id> and POST /undo/<playlist_id>.
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


class TestShuffleAuth:
    """Auth guard tests for shuffle endpoints."""

    @patch("shuffify.routes.require_auth")
    def test_shuffle_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/shuffle/playlist123",
                data={"algorithm": "BasicShuffle"},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_undo_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post("/undo/playlist123")
            assert resp.status_code == 401


class TestShuffleEndpoint:
    """Tests for POST /shuffle/<playlist_id>."""

    @patch(
        "shuffify.routes.shuffle.PlaylistSnapshotService"
    )
    @patch("shuffify.routes.shuffle.StateService")
    @patch("shuffify.routes.shuffle.ShuffleService")
    @patch("shuffify.routes.shuffle.PlaylistService")
    @patch("shuffify.routes.require_auth")
    def test_successful_shuffle(
        self,
        mock_auth,
        mock_ps_class,
        mock_ss,
        mock_state,
        mock_snap,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        mock_playlist = MagicMock()
        mock_playlist.name = "Test Playlist"
        mock_playlist.tracks = [
            {"uri": f"spotify:track:t{i}"}
            for i in range(5)
        ]
        mock_playlist.to_dict.return_value = {
            "id": "p1",
            "name": "Test Playlist",
        }

        mock_ps = MagicMock()
        mock_ps.get_playlist.return_value = mock_playlist
        mock_ps_class.return_value = mock_ps

        mock_algorithm = MagicMock()
        mock_algorithm.name = "Basic Shuffle"
        mock_ss.get_algorithm.return_value = mock_algorithm
        shuffled = [
            f"spotify:track:t{i}" for i in range(4, -1, -1)
        ]
        mock_ss.execute.return_value = shuffled
        mock_ss.shuffle_changed_order.return_value = True
        mock_ss.prepare_tracks_for_shuffle.return_value = (
            mock_playlist.tracks
        )

        mock_state.get_current_uris.return_value = [
            f"spotify:track:t{i}" for i in range(5)
        ]
        mock_state_obj = MagicMock()
        mock_state_obj.to_dict.return_value = {
            "current_index": 1
        }
        mock_state.record_new_state.return_value = (
            mock_state_obj
        )

        resp = auth_client.post(
            "/shuffle/playlist123",
            data={"algorithm": "BasicShuffle"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        mock_ps.update_playlist_tracks.assert_called_once()

    @patch(
        "shuffify.routes.shuffle.PlaylistSnapshotService"
    )
    @patch("shuffify.routes.shuffle.StateService")
    @patch("shuffify.routes.shuffle.ShuffleService")
    @patch("shuffify.routes.shuffle.PlaylistService")
    @patch("shuffify.routes.require_auth")
    def test_shuffle_no_change_returns_info(
        self,
        mock_auth,
        mock_ps_class,
        mock_ss,
        mock_state,
        mock_snap,
        auth_client,
    ):
        """When shuffle produces same order, returns
        success=False."""
        mock_auth.return_value = MagicMock()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        mock_playlist = MagicMock()
        mock_playlist.tracks = [
            {"uri": "spotify:track:t1"}
        ]
        mock_ps = MagicMock()
        mock_ps.get_playlist.return_value = mock_playlist
        mock_ps_class.return_value = mock_ps

        uris = ["spotify:track:t1"]
        mock_ss.execute.return_value = uris
        mock_ss.shuffle_changed_order.return_value = False
        mock_ss.prepare_tracks_for_shuffle.return_value = (
            mock_playlist.tracks
        )
        mock_state.get_current_uris.return_value = uris

        resp = auth_client.post(
            "/shuffle/playlist123",
            data={"algorithm": "BasicShuffle"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is False
        assert "did not change" in data["message"]


class TestUndoEndpoint:
    """Tests for POST /undo/<playlist_id>."""

    @patch("shuffify.routes.shuffle.StateService")
    @patch("shuffify.routes.shuffle.PlaylistService")
    @patch("shuffify.routes.require_auth")
    def test_successful_undo(
        self,
        mock_auth,
        mock_ps_class,
        mock_state,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()

        original_uris = [
            "spotify:track:t1",
            "spotify:track:t2",
        ]
        mock_state.undo.return_value = original_uris

        mock_playlist = MagicMock()
        mock_playlist.to_dict.return_value = {"id": "p1"}
        mock_ps = MagicMock()
        mock_ps.get_playlist.return_value = mock_playlist
        mock_ps_class.return_value = mock_ps

        mock_state.get_state_info.return_value = {
            "current_index": 0
        }

        resp = auth_client.post("/undo/playlist123")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "restored" in data["message"].lower()

    @patch("shuffify.routes.shuffle.StateService")
    @patch("shuffify.routes.shuffle.PlaylistService")
    @patch("shuffify.routes.require_auth")
    def test_undo_update_fails_reverts_state(
        self,
        mock_auth,
        mock_ps_class,
        mock_state,
        auth_client,
    ):
        """When Spotify update fails, state should be
        reverted."""
        mock_auth.return_value = MagicMock()
        mock_state.undo.return_value = [
            "spotify:track:t1"
        ]

        from shuffify.services import PlaylistUpdateError

        mock_ps = MagicMock()
        mock_ps.update_playlist_tracks.side_effect = (
            PlaylistUpdateError("API fail")
        )
        mock_ps_class.return_value = mock_ps

        resp = auth_client.post("/undo/playlist123")
        assert resp.status_code == 500
        mock_state.revert_undo.assert_called_once()
