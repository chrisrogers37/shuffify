"""
Tests for the /settings routes.

Tests cover rendering the settings page and updating settings.
"""

import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db, User, UserSettings


@pytest.fixture
def test_user(db_app):
    """Create a test user."""
    with db_app.app_context():
        user = User(
            spotify_id="route_test_user",
            display_name="Route Test User",
        )
        db.session.add(user)
        db.session.commit()
        return user


@pytest.fixture
def auth_client(db_app, test_user):
    """Authenticated test client with mocked Spotify auth."""
    with db_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "test_refresh",
            }
            sess["user_data"] = {
                "id": "route_test_user",
                "display_name": "Route Test User",
                "images": [],
            }
        yield client


class TestSettingsGetRoute:
    """Tests for GET /settings."""

    def test_redirects_when_not_authenticated(
        self, db_app
    ):
        """Should redirect unauthenticated users."""
        with db_app.test_client() as client:
            response = client.get("/settings")
            assert response.status_code == 302

    @patch("shuffify.routes.settings.AuthService")
    def test_renders_settings_page(
        self, mock_auth, auth_client, test_user
    ):
        """Should render settings page for authenticated user."""
        mock_client = MagicMock()
        mock_auth.get_authenticated_client.return_value = (
            mock_client
        )
        mock_auth.get_user_data.return_value = {
            "id": "route_test_user",
            "display_name": "Route Test User",
            "images": [],
        }
        mock_auth.validate_session_token.return_value = (
            True
        )

        response = auth_client.get("/settings")
        assert response.status_code == 200
        assert b"Settings" in response.data


class TestSettingsPostRoute:
    """Tests for POST /settings."""

    @patch("shuffify.routes.require_auth")
    def test_update_settings_via_form(
        self, mock_auth, auth_client, db_app, test_user
    ):
        """Should update settings from form submission."""
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            response = auth_client.post(
                "/settings",
                data={
                    "theme": "dark",
                    "auto_snapshot_enabled": "true",
                    "notifications_enabled": "false",
                    "max_snapshots_per_playlist": "15",
                    "dashboard_show_recent_activity": "true",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest"
                },
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True

    def test_update_settings_unauthenticated(
        self, db_app
    ):
        """Should reject unauthenticated settings updates."""
        with db_app.test_client() as client:
            response = client.post(
                "/settings",
                data={"theme": "dark"},
                headers={
                    "X-Requested-With": "XMLHttpRequest"
                },
            )
            assert response.status_code in (401, 302)
