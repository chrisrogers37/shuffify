"""
Tests for raid panel routes.

Tests cover authentication, validation, DB unavailability,
and basic success paths.
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


# =============================================================================
# Authentication Tests
# =============================================================================


class TestRaidAuthRequired:
    """All raid panel endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_status_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(
                "/playlist/p1/raid-status"
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_watch_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/raid-watch",
                json={"source_playlist_id": "s1"},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_raid_now_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/raid-now",
                json={},
            )
            assert resp.status_code == 401


# =============================================================================
# Validation Tests
# =============================================================================


class TestRaidValidation:
    """Request validation tests."""

    @patch("shuffify.routes.require_auth")
    def test_watch_invalid_body(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-watch",
            json={"source_playlist_id": ""},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_unwatch_missing_source_id(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-unwatch",
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_raid_now_empty_source_list(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/raid-now",
            json={"source_playlist_ids": []},
        )
        assert resp.status_code == 400


# =============================================================================
# DB Unavailable Tests
# =============================================================================


class TestRaidDbUnavailable:
    """DB unavailable returns 503."""

    @patch("shuffify.routes.require_auth")
    @patch("shuffify.is_db_available")
    def test_status_db_unavailable(
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
                "/playlist/p1/raid-status"
            )
            assert resp.status_code == 503

    @patch("shuffify.routes.require_auth")
    @patch("shuffify.is_db_available")
    def test_watch_db_unavailable(
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
            resp = client.post(
                "/playlist/p1/raid-watch",
                json={"source_playlist_id": "s1"},
            )
            assert resp.status_code == 503


# =============================================================================
# Success Path Tests
# =============================================================================


class TestRaidSuccessPaths:
    """Basic success path tests."""

    @patch("shuffify.routes.require_auth")
    def test_status_returns_structure(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.get(
            "/playlist/p1/raid-status"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "raid_status" in data
        status = data["raid_status"]
        assert "sources" in status
        assert "has_schedule" in status
        assert "source_count" in status
