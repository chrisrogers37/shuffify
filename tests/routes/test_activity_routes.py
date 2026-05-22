"""
Tests for the /activity route.

Covers authentication gating and rendering with mocked service layer.
"""

import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db, User


@pytest.fixture
def test_user(db_app):
    """Create a test user."""
    with db_app.app_context():
        user = User(
            spotify_id="activity_test_user",
            display_name="Activity Test User",
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
                "id": "activity_test_user",
                "display_name": "Activity Test User",
                "images": [],
            }
        yield client


class TestActivityRoute:
    """Tests for GET /activity."""

    def test_redirects_when_not_authenticated(self, db_app):
        """Unauthenticated users get 302 or 401."""
        with db_app.test_client() as client:
            response = client.get("/activity")
            assert response.status_code in (302, 401)

    @patch("shuffify.routes.activity.DashboardService")
    @patch("shuffify.routes.activity.ActivityLogService")
    @patch("shuffify.routes.core.AuthService")
    def test_renders_activity_page(
        self,
        mock_auth,
        mock_activity_svc,
        mock_dash_svc,
        auth_client,
        test_user,
    ):
        """Authenticated user sees the activity page."""
        mock_client = MagicMock()
        mock_auth.get_authenticated_client.return_value = mock_client
        mock_auth.get_user_data.return_value = {
            "id": "activity_test_user",
            "display_name": "Activity Test User",
            "images": [],
        }
        mock_auth.validate_session_token.return_value = True

        mock_dash_svc.get_quick_stats.return_value = {
            "total_shuffles": 0,
            "total_raids": 0,
        }
        mock_activity_svc.get_recent.return_value = []
        mock_dash_svc.get_recent_executions.return_value = []

        response = auth_client.get("/activity")
        assert response.status_code == 200

    @patch("shuffify.routes.activity.DashboardService")
    @patch("shuffify.routes.activity.ActivityLogService")
    @patch("shuffify.routes.core.AuthService")
    def test_passes_stats_and_activities_to_template(
        self,
        mock_auth,
        mock_activity_svc,
        mock_dash_svc,
        auth_client,
        test_user,
    ):
        """Route passes stats, activities, and executions to template."""
        mock_client = MagicMock()
        mock_auth.get_authenticated_client.return_value = mock_client
        mock_auth.get_user_data.return_value = {
            "id": "activity_test_user",
            "display_name": "Activity Test User",
            "images": [],
        }
        mock_auth.validate_session_token.return_value = True

        fake_stats = {"total_shuffles": 42, "total_raids": 7}
        fake_activities = [
            {"activity_type": "shuffle", "description": "Shuffled playlist"},
        ]
        fake_executions = [
            {"id": 1, "status": "success"},
        ]

        mock_dash_svc.get_quick_stats.return_value = fake_stats
        mock_activity_svc.get_recent.return_value = fake_activities
        mock_dash_svc.get_recent_executions.return_value = fake_executions

        response = auth_client.get("/activity")
        assert response.status_code == 200

        mock_activity_svc.get_recent.assert_called_once()
        mock_dash_svc.get_quick_stats.assert_called_once()
        mock_dash_svc.get_recent_executions.assert_called_once()
