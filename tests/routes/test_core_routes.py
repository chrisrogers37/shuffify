"""
Tests for core routes.

Tests cover /, /login, /callback, /logout, /terms, /privacy.
Health endpoint (/health) is tested in tests/test_health_db.py.
"""

import time
from unittest.mock import patch, MagicMock

class TestIndexRoute:
    """Tests for GET /."""

    def test_unauthenticated_shows_login_page(self, db_app):
        """Unauthenticated users see the landing page."""
        with db_app.test_client() as client:
            resp = client.get("/")
            assert resp.status_code == 200

    @patch("shuffify.routes.core.DashboardService")
    @patch("shuffify.routes.core.ShuffleService")
    @patch("shuffify.routes.core.PlaylistService")
    @patch("shuffify.routes.core.AuthService")
    @patch("shuffify.routes.core.is_authenticated")
    def test_authenticated_shows_dashboard(
        self,
        mock_is_auth,
        mock_auth_svc,
        mock_ps_class,
        mock_shuffle_svc,
        mock_dash_svc,
        auth_client,
    ):
        mock_is_auth.return_value = True
        mock_client = MagicMock()
        mock_auth_svc.get_authenticated_client.return_value = (
            mock_client
        )
        mock_auth_svc.get_user_data.return_value = {
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        }

        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = []
        mock_ps_class.return_value = mock_ps

        mock_shuffle_svc.list_algorithms.return_value = []
        mock_dash_svc.get_dashboard_data.return_value = {}

        resp = auth_client.get("/")
        assert resp.status_code == 200

    @patch("shuffify.routes.core.AuthService")
    @patch("shuffify.routes.core.is_authenticated")
    def test_expired_session_clears_and_shows_login(
        self,
        mock_is_auth,
        mock_auth_svc,
        auth_client,
    ):
        from shuffify.services import AuthenticationError

        mock_is_auth.return_value = True
        mock_auth_svc.get_authenticated_client.side_effect = (
            AuthenticationError("expired")
        )

        resp = auth_client.get("/")
        assert resp.status_code == 200

    def test_unauthenticated_landing_has_shuffle_demo(self, db_app):
        """Landing page contains the animated shuffle demo component."""
        with db_app.test_client() as client:
            resp = client.get("/")
            html = resp.data.decode()
            assert resp.status_code == 200
            assert 'id="shuffle-demo"' in html
            assert 'id="shuffle-track-list"' in html

    def test_unauthenticated_landing_has_two_column_hero(self, db_app):
        """Landing page hero uses two-column flex layout."""
        with db_app.test_client() as client:
            resp = client.get("/")
            html = resp.data.decode()
            assert "lg:flex-row" in html
            assert "shuffle-demo-card" in html


class TestLoginRoute:
    """Tests for GET /login."""

    def test_missing_legal_consent_redirects(self, db_app):
        """Login without legal_consent flashes error and redirects."""
        with db_app.test_client() as client:
            resp = client.get("/login")
            assert resp.status_code == 302

    @patch("shuffify.routes.core.AuthService")
    def test_with_legal_consent_redirects_to_spotify(
        self, mock_auth_svc, db_app
    ):
        mock_auth_svc.get_auth_url.return_value = (
            "https://accounts.spotify.com/authorize?test=1"
        )
        with db_app.test_client() as client:
            resp = client.get("/login?legal_consent=true")
            assert resp.status_code == 302
            assert (
                "accounts.spotify.com"
                in resp.headers["Location"]
            )

    @patch("shuffify.routes.core.AuthService")
    def test_auth_error_during_login_redirects(
        self, mock_auth_svc, db_app
    ):
        from shuffify.services import AuthenticationError

        mock_auth_svc.get_auth_url.side_effect = (
            AuthenticationError("config error")
        )
        with db_app.test_client() as client:
            resp = client.get("/login?legal_consent=true")
            assert resp.status_code == 302


class TestCallbackRoute:
    """Tests for GET /callback."""

    def test_oauth_error_redirects(self, db_app):
        """OAuth error parameter should redirect to index."""
        with db_app.test_client() as client:
            resp = client.get(
                "/callback?error=access_denied"
                "&error_description=User+denied+access"
            )
            assert resp.status_code == 302

    def test_missing_code_redirects(self, db_app):
        """No authorization code should redirect to index."""
        with db_app.test_client() as client:
            resp = client.get("/callback")
            assert resp.status_code == 302

    @patch("shuffify.routes.core.LoginHistoryService")
    @patch("shuffify.routes.core.UserService")
    @patch("shuffify.routes.core.AuthService")
    def test_successful_callback(
        self,
        mock_auth_svc,
        mock_user_svc,
        mock_login_hist,
        db_app,
    ):
        mock_auth_svc.exchange_code_for_token.return_value = {
            "access_token": "new_token",
            "token_type": "Bearer",
            "expires_at": time.time() + 3600,
            "refresh_token": "new_refresh",
        }
        mock_client = MagicMock()
        mock_auth_svc.authenticate_and_get_user.return_value = (
            mock_client,
            {
                "id": "user123",
                "display_name": "Test User",
                "images": [],
            },
        )
        mock_upsert_result = MagicMock()
        mock_upsert_result.is_new = False
        mock_user_svc.upsert_from_spotify.return_value = (
            mock_upsert_result
        )
        mock_user_svc.get_by_spotify_id.return_value = None

        with db_app.test_client() as client:
            resp = client.get(
                "/callback?code=test_auth_code"
            )
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/")

    @patch("shuffify.routes.core.AuthService")
    def test_auth_failure_during_callback(
        self, mock_auth_svc, db_app
    ):
        from shuffify.services import AuthenticationError

        mock_auth_svc.exchange_code_for_token.side_effect = (
            AuthenticationError("invalid code")
        )

        with db_app.test_client() as client:
            resp = client.get(
                "/callback?code=bad_code"
            )
            assert resp.status_code == 302


class TestLogoutRoute:
    """Tests for GET /logout."""

    def test_logout_clears_session_and_redirects(
        self, auth_client
    ):
        """Logout should clear session and redirect to index."""
        resp = auth_client.get("/logout")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")

    def test_logout_when_not_logged_in(self, db_app):
        """Logout without session should still work."""
        with db_app.test_client() as client:
            resp = client.get("/logout")
            assert resp.status_code == 302


class TestTermsRoute:
    """Tests for GET /terms."""

    def test_terms_page_returns_200(self, db_app):
        with db_app.test_client() as client:
            resp = client.get("/terms")
            assert resp.status_code == 200


class TestPrivacyRoute:
    """Tests for GET /privacy."""

    def test_privacy_page_returns_200(self, db_app):
        with db_app.test_client() as client:
            resp = client.get("/privacy")
            assert resp.status_code == 200


class TestDashboardDisplayNameFallback:
    """Tests for dashboard rendering when display_name is None."""

    @patch("shuffify.routes.core.DashboardService")
    @patch("shuffify.routes.core.ShuffleService")
    @patch("shuffify.routes.core.PlaylistService")
    @patch("shuffify.routes.core.AuthService")
    @patch("shuffify.routes.core.is_authenticated")
    def test_dashboard_renders_without_display_name(
        self,
        mock_is_auth,
        mock_auth_svc,
        mock_ps_class,
        mock_shuffle_svc,
        mock_dash_svc,
        auth_client,
    ):
        """Dashboard shows 'Welcome!' when display_name is None."""
        mock_is_auth.return_value = True
        mock_client = MagicMock()
        mock_auth_svc.get_authenticated_client.return_value = (
            mock_client
        )
        mock_auth_svc.get_user_data.return_value = {
            "id": "user_no_name",
            "display_name": None,
            "images": [],
        }

        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = []
        mock_ps_class.return_value = mock_ps

        mock_shuffle_svc.list_algorithms.return_value = []
        mock_dash_svc.get_dashboard_data.return_value = {}

        resp = auth_client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Welcome!" in html
        assert "Welcome, None!" not in html

    @patch("shuffify.routes.core.DashboardService")
    @patch("shuffify.routes.core.ShuffleService")
    @patch("shuffify.routes.core.PlaylistService")
    @patch("shuffify.routes.core.AuthService")
    @patch("shuffify.routes.core.is_authenticated")
    def test_dashboard_returning_user_without_display_name(
        self,
        mock_is_auth,
        mock_auth_svc,
        mock_ps_class,
        mock_shuffle_svc,
        mock_dash_svc,
        auth_client,
    ):
        """Returning user sees 'Welcome back!' not 'Welcome back, None!'."""
        mock_is_auth.return_value = True
        mock_client = MagicMock()
        mock_auth_svc.get_authenticated_client.return_value = (
            mock_client
        )
        mock_auth_svc.get_user_data.return_value = {
            "id": "user_no_name_returning",
            "display_name": None,
            "images": [],
        }

        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = []
        mock_ps_class.return_value = mock_ps

        mock_shuffle_svc.list_algorithms.return_value = []
        mock_dash_svc.get_dashboard_data.return_value = {
            "is_returning_user": True,
            "recent_activity": [],
            "activity_since_last_login": [],
            "activity_since_last_login_count": 0,
            "quick_stats": {
                "total_shuffles": 0,
                "total_scheduled_runs": 0,
                "total_snapshots": 0,
                "active_schedule_count": 0,
            },
            "active_schedules": [],
            "recent_job_executions": [],
        }

        resp = auth_client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Welcome back!" in html
        assert "Welcome back, None!" not in html


# NOTE: /health endpoint tests are in tests/test_health_db.py (7 tests).
# No duplicate tests here.
