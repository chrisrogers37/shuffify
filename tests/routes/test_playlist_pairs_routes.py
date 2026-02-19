"""
Tests for playlist pair routes.

Tests cover authentication, error handling, and basic CRUD
for the /playlist/<id>/pair endpoints.
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


class TestPairAuthRequired:
    """All pair endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_get_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/playlist/p1/pair")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_create_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pair",
                json={"create_new": True},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_delete_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete("/playlist/p1/pair")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_archive_tracks_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pair/archive",
                json={"track_uris": ["spotify:track:x"]},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_unarchive_tracks_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/pair/unarchive",
                json={"track_uris": ["spotify:track:x"]},
            )
            assert resp.status_code == 401


# =============================================================================
# GET /playlist/<id>/pair
# =============================================================================


class TestGetPair:
    """Tests for GET /playlist/<id>/pair."""

    @patch("shuffify.routes.require_auth")
    def test_no_pair_returns_paired_false(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.get("/playlist/p1/pair")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["paired"] is False


# =============================================================================
# POST /playlist/<id>/pair
# =============================================================================


class TestCreatePairRoute:
    """Tests for POST /playlist/<id>/pair."""

    @patch("shuffify.routes.require_auth")
    def test_invalid_body_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pair",
            json={"create_new": False},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_no_json_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            "/playlist/p1/pair",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400
