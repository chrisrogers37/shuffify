"""
Tests for error page rendering and route exception handling.

Covers the global 500 handler's HTML vs JSON branching
and the broadened exception handling in schedules, settings,
and refresh routes.
"""

import time
import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db, User
from shuffify.services import (
    ScheduleError,
    UserSettingsError,
)


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
        user = User(
            spotify_id="user123",
            display_name="Test User",
        )
        db.session.add(user)
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def error_app(db_app):
    """App configured to use error handlers instead of propagating."""
    db_app.config["PROPAGATE_EXCEPTIONS"] = False
    db_app.testing = False
    return db_app


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
                "images": [],
            }
        yield client


class TestGlobal500Handler:
    """Tests for the global 500 error handler HTML vs JSON."""

    def test_500_page_route_returns_html(self, error_app):
        """Page route 500 returns HTML error page."""

        @error_app.route("/test-500-page")
        def trigger_500_page():
            raise RuntimeError("Test page error")

        with error_app.test_client() as client:
            resp = client.get("/test-500-page")
            assert resp.status_code == 500
            assert resp.content_type.startswith("text/html")
            assert b"Something went wrong" in resp.data

    def test_500_api_route_returns_json(self, error_app):
        """API route 500 returns JSON error response."""

        @error_app.route("/api/test-500")
        def trigger_500_api():
            raise RuntimeError("Test API error")

        with error_app.test_client() as client:
            resp = client.get("/api/test-500")
            assert resp.status_code == 500
            data = resp.get_json()
            assert data["success"] is False
            assert "unexpected error" in data["message"].lower()

    def test_500_ajax_request_returns_json(self, error_app):
        """AJAX request to page route returns JSON."""

        @error_app.route("/test-500-ajax")
        def trigger_500_ajax():
            raise RuntimeError("Test AJAX error")

        with error_app.test_client() as client:
            resp = client.get(
                "/test-500-ajax",
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            assert resp.status_code == 500
            data = resp.get_json()
            assert data["success"] is False

    def test_500_json_content_type_returns_json(
        self, error_app
    ):
        """Request with JSON content type returns JSON."""

        @error_app.route("/test-500-json")
        def trigger_500_json():
            raise RuntimeError("Test JSON error")

        with error_app.test_client() as client:
            resp = client.get(
                "/test-500-json",
                content_type="application/json",
            )
            assert resp.status_code == 500
            data = resp.get_json()
            assert data["success"] is False

    def test_500_handler_logs_exception_type(
        self, error_app, caplog
    ):
        """500 handler logs the exception type name."""

        @error_app.route("/test-500-log")
        def trigger_500_log():
            raise ValueError("Test log error")

        import logging

        with caplog.at_level(logging.ERROR):
            with error_app.test_client() as client:
                client.get("/test-500-log")

        # Flask wraps the original in InternalServerError
        assert any(
            "InternalServerError" in record.getMessage()
            for record in caplog.records
            if record.name == "shuffify.error_handlers"
        )


class TestSchedulesErrorHandling:
    """Tests for broadened schedules route exception handling."""

    @patch("shuffify.routes.schedules.AuthService")
    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    def test_schedule_error_flashes_and_redirects(
        self, mock_scheduler, mock_auth, auth_client
    ):
        """ScheduleError redirects with flash message."""
        mock_auth.get_authenticated_client.return_value = (
            MagicMock()
        )
        mock_auth.get_user_data.return_value = {
            "id": "user123",
            "display_name": "Test User",
        }
        mock_scheduler.get_user_schedules.side_effect = (
            ScheduleError("DB query failed")
        )

        resp = auth_client.get("/schedules")
        assert resp.status_code == 302
        assert resp.location.endswith("/")

    @patch("shuffify.routes.schedules.AuthService")
    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    def test_unexpected_error_flashes_and_redirects(
        self, mock_scheduler, mock_auth, auth_client
    ):
        """RuntimeError redirects with flash message."""
        mock_auth.get_authenticated_client.return_value = (
            MagicMock()
        )
        mock_auth.get_user_data.return_value = {
            "id": "user123",
            "display_name": "Test User",
        }
        mock_scheduler.get_user_schedules.side_effect = (
            RuntimeError("Unexpected failure")
        )

        resp = auth_client.get("/schedules")
        assert resp.status_code == 302
        assert resp.location.endswith("/")


class TestSettingsErrorHandling:
    """Tests for broadened settings route exception handling."""

    @patch("shuffify.routes.settings.AuthService")
    @patch(
        "shuffify.routes.settings.UserSettingsService"
    )
    def test_settings_error_flashes_and_redirects(
        self, mock_settings_svc, mock_auth, auth_client
    ):
        """UserSettingsError redirects with flash message."""
        mock_auth.get_authenticated_client.return_value = (
            MagicMock()
        )
        mock_auth.get_user_data.return_value = {
            "id": "user123",
            "display_name": "Test User",
        }
        mock_settings_svc.get_or_create.side_effect = (
            UserSettingsError("Settings DB error")
        )

        resp = auth_client.get("/settings")
        assert resp.status_code == 302
        assert resp.location.endswith("/")

    @patch("shuffify.routes.settings.AuthService")
    @patch(
        "shuffify.routes.settings.UserSettingsService"
    )
    def test_unexpected_error_flashes_and_redirects(
        self, mock_settings_svc, mock_auth, auth_client
    ):
        """RuntimeError redirects with flash message."""
        mock_auth.get_authenticated_client.return_value = (
            MagicMock()
        )
        mock_auth.get_user_data.return_value = {
            "id": "user123",
            "display_name": "Test User",
        }
        mock_settings_svc.get_or_create.side_effect = (
            RuntimeError("Unexpected")
        )

        resp = auth_client.get("/settings")
        assert resp.status_code == 302
        assert resp.location.endswith("/")


class TestRefreshErrorHandling:
    """Tests for refresh endpoint general exception fallback."""

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_unexpected_error_returns_json(
        self,
        mock_require_auth,
        mock_get_db_user,
        mock_playlist_svc,
        db_app,
    ):
        """RuntimeError returns JSON with success: false."""
        mock_require_auth.return_value = MagicMock()
        mock_db_user = MagicMock()
        mock_db_user.id = 1
        mock_get_db_user.return_value = mock_db_user

        mock_instance = MagicMock()
        mock_instance.get_user_playlists.side_effect = (
            RuntimeError("Unexpected")
        )
        mock_playlist_svc.return_value = mock_instance

        with db_app.test_client() as client:
            resp = client.post(
                "/refresh-playlists",
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            assert resp.status_code == 500
            data = resp.get_json()
            assert data["success"] is False
            assert (
                "unexpected error" in data["message"].lower()
            )
