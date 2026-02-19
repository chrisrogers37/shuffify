"""
Tests for snapshot routes.

Tests cover list, create, view, restore, and delete endpoints.
"""

import time
import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db
from shuffify.services.user_service import UserService
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)
from shuffify.enums import SnapshotType


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
        result = UserService.upsert_from_spotify({
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


class TestListSnapshots:
    """Tests for GET /playlist/<id>/snapshots."""

    @patch("shuffify.routes.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(
                "/playlist/p1/snapshots"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    @patch("shuffify.is_db_available")
    def test_db_unavailable_returns_503(
        self, mock_db, mock_auth, db_app
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = False
        with db_app.test_client() as client:
            with client.session_transaction() as sess:
                sess["spotify_token"] = {
                    "access_token": "t",
                    "expires_at": time.time() + 3600,
                }
                sess["user_data"] = {"id": "user123"}
            resp = client.get(
                "/playlist/p1/snapshots"
            )
            assert resp.status_code == 503

    @patch("shuffify.routes.require_auth")
    def test_list_empty(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.get(
            "/playlist/p1/snapshots"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["snapshots"] == []

    @patch("shuffify.routes.require_auth")
    def test_list_with_snapshots(
        self, mock_auth, auth_client, db_app
    ):
        mock_auth.return_value = MagicMock()

        # Create a snapshot directly
        with db_app.app_context():
            user = UserService.get_by_spotify_id(
                "user123"
            )
            PlaylistSnapshotService.create_snapshot(
                user.id,
                "p1",
                "Test",
                ["spotify:track:a"],
                SnapshotType.MANUAL,
            )

        resp = auth_client.get(
            "/playlist/p1/snapshots"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["snapshots"]) == 1

    @patch("shuffify.routes.require_auth")
    def test_list_respects_limit_param(
        self, mock_auth, auth_client, db_app
    ):
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            user = UserService.get_by_spotify_id(
                "user123"
            )
            for i in range(5):
                PlaylistSnapshotService.create_snapshot(
                    user.id,
                    "p1",
                    f"S{i}",
                    [f"spotify:track:{i}"],
                    SnapshotType.MANUAL,
                )

        resp = auth_client.get(
            "/playlist/p1/snapshots?limit=2"
        )
        data = resp.get_json()
        assert len(data["snapshots"]) == 2


class TestCreateManualSnapshot:
    """Tests for POST /playlist/<id>/snapshots."""

    @patch("shuffify.routes.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/snapshots",
                json={},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_missing_json_body(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/snapshots",
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_invalid_request_body(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/snapshots",
            json={"track_uris": ["invalid_uri"]},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_create_success(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/snapshots",
            json={
                "playlist_name": "My Playlist",
                "track_uris": [
                    "spotify:track:a",
                    "spotify:track:b",
                ],
                "trigger_description": "Manual save",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["snapshot"]["track_count"] == 2
        assert (
            data["snapshot"]["snapshot_type"]
            == SnapshotType.MANUAL
        )

    @patch("shuffify.routes.require_auth")
    def test_create_empty_track_list(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/snapshots",
            json={
                "playlist_name": "Empty",
                "track_uris": [],
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["snapshot"]["track_count"] == 0


class TestViewSnapshot:
    """Tests for GET /snapshots/<id>."""

    @patch("shuffify.routes.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/snapshots/1")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_not_found(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.get("/snapshots/99999")
        assert resp.status_code == 404

    @patch("shuffify.routes.require_auth")
    def test_view_success(
        self, mock_auth, auth_client, db_app
    ):
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            user = UserService.get_by_spotify_id(
                "user123"
            )
            snap = (
                PlaylistSnapshotService.create_snapshot(
                    user.id,
                    "p1",
                    "Test",
                    ["spotify:track:a"],
                    SnapshotType.MANUAL,
                )
            )
            snap_id = snap.id

        resp = auth_client.get(f"/snapshots/{snap_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["snapshot"]["id"] == snap_id


class TestDeleteSnapshot:
    """Tests for DELETE /snapshots/<id>."""

    @patch("shuffify.routes.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete("/snapshots/1")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_not_found(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.delete("/snapshots/99999")
        assert resp.status_code == 404

    @patch("shuffify.routes.require_auth")
    def test_delete_success(
        self, mock_auth, auth_client, db_app
    ):
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            user = UserService.get_by_spotify_id(
                "user123"
            )
            snap = (
                PlaylistSnapshotService.create_snapshot(
                    user.id,
                    "p1",
                    "Test",
                    ["spotify:track:a"],
                    SnapshotType.MANUAL,
                )
            )
            snap_id = snap.id

        resp = auth_client.delete(
            f"/snapshots/{snap_id}"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify deleted
        resp2 = auth_client.get(
            f"/snapshots/{snap_id}"
        )
        assert resp2.status_code == 404


class TestRestoreSnapshot:
    """Tests for POST /snapshots/<id>/restore."""

    @patch("shuffify.routes.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/snapshots/1/restore"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_not_found(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/snapshots/99999/restore"
        )
        assert resp.status_code == 404

    @patch(
        "shuffify.routes.snapshots.PlaylistService"
    )
    @patch("shuffify.routes.require_auth")
    def test_restore_success(
        self,
        mock_auth,
        mock_playlist_service_class,
        auth_client,
        db_app,
    ):
        mock_client = MagicMock()
        mock_auth.return_value = mock_client

        # Mock PlaylistService
        mock_ps = MagicMock()
        mock_playlist_service_class.return_value = (
            mock_ps
        )
        mock_playlist = MagicMock()
        mock_playlist.name = "Test Playlist"
        mock_playlist.tracks = [
            {"uri": "spotify:track:x"},
            {"uri": "spotify:track:y"},
        ]
        mock_ps.get_playlist.return_value = (
            mock_playlist
        )

        with db_app.app_context():
            user = UserService.get_by_spotify_id(
                "user123"
            )
            snap = (
                PlaylistSnapshotService.create_snapshot(
                    user.id,
                    "p1",
                    "Test",
                    [
                        "spotify:track:a",
                        "spotify:track:b",
                    ],
                    SnapshotType.MANUAL,
                )
            )
            snap_id = snap.id

        resp = auth_client.post(
            f"/snapshots/{snap_id}/restore"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "restored" in data["message"].lower()

        # Verify update_playlist_tracks was called
        mock_ps.update_playlist_tracks.assert_called()
