"""OAuth consent, callback/session renewal, safe notices, and native logout."""

import logging
import time
from unittest.mock import MagicMock, patch

import pytest

from tests.paths import api_path


class TestLoginRoute:
    """Tests for GET /login."""

    def test_missing_legal_consent_redirects(self, db_app):
        """Login without legal consent redirects with a one-time authored notice."""
        with db_app.test_client() as client:
            resp = client.get("/login")
            assert resp.status_code == 302

    @patch("shuffify.routes.core.AuthService")
    def test_with_legal_consent_redirects_to_spotify(self, mock_auth_svc, db_app):
        mock_auth_svc.get_auth_url.return_value = (
            "https://accounts.spotify.com/authorize?test=1"
        )
        with db_app.test_client() as client:
            resp = client.get("/login?legal_consent=true")
            assert resp.status_code == 302
            assert "accounts.spotify.com" in resp.headers["Location"]

    @patch("shuffify.routes.core.AuthService")
    def test_login_stores_oauth_state_in_session(self, mock_auth_svc, db_app):
        """Login should generate a CSRF state token matching session value."""
        mock_auth_svc.get_auth_url.return_value = (
            "https://accounts.spotify.com/authorize?test=1"
        )
        with db_app.test_client() as client:
            resp = client.get("/login?legal_consent=true")
            assert resp.status_code == 302
            call_kwargs = mock_auth_svc.get_auth_url.call_args
            state_value = call_kwargs.kwargs.get("state")
            assert state_value is not None
            assert len(state_value) > 0
            with client.session_transaction() as sess:
                assert sess.get("oauth_state") == state_value

    @patch("shuffify.routes.core.AuthService")
    def test_auth_error_during_login_redirects(self, mock_auth_svc, db_app):
        from shuffify.services import AuthenticationError

        mock_auth_svc.get_auth_url.side_effect = AuthenticationError("config error")
        with db_app.test_client() as client:
            resp = client.get("/login?legal_consent=true")
            assert resp.status_code == 302


class TestCallbackRoute:
    """Tests for GET /callback."""

    def test_oauth_error_redirects(self, db_app):
        """OAuth error parameter should redirect to index."""
        with db_app.test_client() as client:
            resp = client.get(
                "/callback?error=access_denied&error_description=User+denied+access"
            )
            assert resp.status_code == 302

    @pytest.mark.parametrize(
        "query, marker",
        [
            (
                {"error": "access_denied", "error_description": "Visit example.invalid to restore access"},
                "Visit example.invalid to restore access",
            ),
            ({"error": "not_a_spotify_error_code"}, "not_a_spotify_error_code"),
        ],
        ids=["error-description", "unknown-error-code"],
    )
    def test_oauth_error_notice_is_authored_and_consumed_once(self, db_app, query, marker):
        """The signed-out session reads one fixed notice without echoing callback query text."""
        with db_app.test_client() as client:
            client.get("/callback", query_string=query)
            first = client.get(api_path("/session")).get_json()
            second = client.get(api_path("/session")).get_json()
        assert first["signed_in"] is False
        assert first["notice"]["code"] == ("access_denied" if query["error"] == "access_denied" else "oauth_failed")
        assert first["notice"]["message"]
        assert marker not in first["notice"]["message"]
        assert second["notice"] is None

    def test_missing_code_redirects(self, db_app):
        """No authorization code should redirect to index."""
        with db_app.test_client() as client:
            resp = client.get("/callback")
            assert resp.status_code == 302

    def test_missing_state_redirects(self, db_app):
        """Callback without state parameter should reject."""
        with db_app.test_client() as client:
            resp = client.get("/callback?code=test_auth_code")
            assert resp.status_code == 302

    def test_mismatched_state_redirects(self, db_app):
        """Callback with wrong state should reject."""
        with db_app.test_client() as client:
            with client.session_transaction() as sess:
                sess["oauth_state"] = "correct_state"
            resp = client.get("/callback?code=test_auth_code&state=wrong_state")
            assert resp.status_code == 302

    def test_state_without_session_redirects(self, db_app):
        """Callback with state but no session state should reject."""
        with db_app.test_client() as client:
            resp = client.get("/callback?code=test_auth_code&state=some_state")
            assert resp.status_code == 302

    def test_session_state_but_no_url_state_redirects(self, db_app):
        """Session has state but URL omits state parameter — should reject."""
        with db_app.test_client() as client:
            with client.session_transaction() as sess:
                sess["oauth_state"] = "stored_state"
            resp = client.get("/callback?code=test_auth_code")
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
        mock_upsert_result.previous_login_at = None
        mock_user_svc.upsert_from_spotify.return_value = mock_upsert_result
        mock_user_svc.get_by_spotify_id.return_value = None

        with db_app.test_client() as client:
            with client.session_transaction() as sess:
                sess["oauth_state"] = "valid_state"
            resp = client.get("/callback?code=test_auth_code&state=valid_state")
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/")

    @patch("shuffify.routes.core.LoginHistoryService")
    @patch("shuffify.routes.core.UserService")
    @patch("shuffify.routes.core.AuthService")
    def test_state_consumed_after_callback(
        self,
        mock_auth_svc,
        mock_user_svc,
        mock_login_hist,
        db_app,
    ):
        """State token should be removed from session after use."""
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
        mock_upsert_result.previous_login_at = None
        mock_user_svc.upsert_from_spotify.return_value = mock_upsert_result
        mock_user_svc.get_by_spotify_id.return_value = None

        with db_app.test_client() as client:
            with client.session_transaction() as sess:
                sess["oauth_state"] = "one_time_state"
            client.get("/callback?code=test_auth_code&state=one_time_state")
            with client.session_transaction() as sess:
                assert "oauth_state" not in sess

    @patch("shuffify.routes.core.LoginHistoryService")
    @patch("shuffify.routes.core.UserService")
    @patch("shuffify.routes.core.AuthService")
    def test_session_regenerated_after_successful_oauth(
        self,
        mock_auth_svc,
        mock_user_svc,
        mock_login_hist,
        db_app,
    ):
        """Session ID must change after OAuth to prevent session fixation."""
        mock_auth_svc.exchange_code_for_token.return_value = {
            "access_token": "new_token",
            "token_type": "Bearer",
            "expires_at": time.time() + 3600,
            "refresh_token": "new_refresh",
        }
        mock_client = MagicMock()
        mock_auth_svc.authenticate_and_get_user.return_value = (
            mock_client,
            {"id": "user123", "display_name": "Test User", "images": []},
        )
        mock_upsert_result = MagicMock()
        mock_upsert_result.is_new = False
        mock_upsert_result.previous_login_at = None
        mock_user_svc.upsert_from_spotify.return_value = mock_upsert_result
        mock_user_svc.get_by_spotify_id.return_value = None

        with db_app.test_client() as client:
            # Plant a pre-auth marker and capture session ID
            with client.session_transaction() as sess:
                sess["oauth_state"] = "valid_state"
                sess["pre_auth_marker"] = "should_be_gone"
                pre_auth_sid = getattr(sess, "sid", None)

            resp = client.get("/callback?code=test_auth_code&state=valid_state")
            assert resp.status_code == 302

            with client.session_transaction() as sess:
                # Auth data present in regenerated session
                assert "spotify_token" in sess
                assert "user_data" in sess
                # Pre-auth data wiped by session.clear()
                assert "pre_auth_marker" not in sess
                # Session ID changed (regenerated)
                post_auth_sid = getattr(sess, "sid", None)
                if pre_auth_sid is not None:
                    assert post_auth_sid != pre_auth_sid

    @patch("shuffify.routes.core.AuthService")
    def test_auth_failure_during_callback(self, mock_auth_svc, db_app):
        from shuffify.services import AuthenticationError

        mock_auth_svc.exchange_code_for_token.side_effect = AuthenticationError(
            "invalid code"
        )

        with db_app.test_client() as client:
            with client.session_transaction() as sess:
                sess["oauth_state"] = "valid_state"
            resp = client.get("/callback?code=bad_code&state=valid_state")
            assert resp.status_code == 302

    def test_callback_does_not_log_auth_code(self, db_app, caplog):
        """Authorization code must not appear in log output."""
        secret_code = "AQDx7_SENSITIVE_AUTH_CODE_12345"
        with caplog.at_level(logging.DEBUG, logger="shuffify.routes.core"):
            with db_app.test_client() as client:
                client.get(f"/callback?code={secret_code}")
        assert secret_code not in caplog.text


class TestLogoutRoute:
    """Tests for POST /logout (CSRF-guarded; GET rejected)."""

    @patch("shuffify.routes.core.AuthService", autospec=True)
    def test_logout_revokes_the_token_clears_the_session_and_redirects(self, mock_auth_svc, auth_client):
        """AuthService is mocked so no token revoke is sent to
        accounts.spotify.com."""
        resp = auth_client.post("/logout")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")
        mock_auth_svc.revoke_access_token.assert_called_once_with("test_token")
        with auth_client.session_transaction() as sess:
            assert "spotify_token" not in sess

    def test_logout_when_not_logged_in(self, db_app):
        """Logout without session should still work."""
        with db_app.test_client() as client:
            resp = client.post("/logout")
            assert resp.status_code == 302

    @patch("shuffify.routes.core.AuthService")
    def test_logout_continues_when_revocation_fails(
        self,
        mock_auth_svc,
        auth_client,
    ):
        """Revocation failure must not block logout."""
        mock_auth_svc.revoke_access_token.side_effect = Exception("boom")
        resp = auth_client.post("/logout")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")

    def test_logout_get_is_rejected(self, auth_client):
        """GET /logout must be rejected (405). Logout is a state-changing
        operation, so it is POST-only and CSRF-guarded — a cross-site
        `<img src=".../logout">` or link cannot force-logout users."""
        resp = auth_client.get("/logout")
        assert resp.status_code == 405


class TestPreviousLogin:
    """The dashboard's "welcome back" and "since your last visit" count from
    the login before this one, which the callback keeps in the session: by the
    time the dashboard renders, the user row's last_login_at is this login."""

    @patch("shuffify.routes.core.AuthService", autospec=True)
    def test_callback_keeps_the_login_before_this_one(self, mock_auth_svc, db_app):
        from shuffify.services.user_service import UserService

        mock_auth_svc.exchange_code_for_token.return_value = {
            "access_token": "new_token",
            "token_type": "Bearer",
            "expires_at": time.time() + 3600,
            "refresh_token": "new_refresh",
        }
        mock_auth_svc.authenticate_and_get_user.return_value = (
            MagicMock(),
            {"id": "first_timer", "display_name": "First", "images": []},
        )

        def log_in(client):
            with client.session_transaction() as sess:
                sess["oauth_state"] = "valid_state"
            client.get("/callback?code=test_auth_code&state=valid_state")
            with client.session_transaction() as sess:
                return sess["previous_login_at"]

        with db_app.test_client() as client:
            assert log_in(client) is None
            with db_app.app_context():
                first_login = UserService.get_by_spotify_id("first_timer").last_login_at
            assert log_in(client) == first_login.isoformat()


# NOTE: /health endpoint tests are in tests/test_health_db.py.
# No duplicate tests here.


@pytest.mark.parametrize("stored", [None, "2026-01-02T03:04:05+00:00"])
def test_library_counts_from_the_login_before_this_one(auth_client, stored):
    from datetime import datetime

    from shuffify.models.db import User
    from shuffify.services.dashboard_service import DashboardService

    user = User.query.filter_by(spotify_id="user123").one()
    dashboard = DashboardService.get_dashboard_data(user.id)
    with auth_client.session_transaction() as session:
        session["previous_login_at"] = stored
    with patch("shuffify.routes.require_auth", autospec=True, return_value=object()), patch(
        "shuffify.routes.contexts.PlaylistService", autospec=True
    ) as playlists, patch(
        "shuffify.routes.contexts.DashboardService.get_dashboard_data", autospec=True, return_value=dashboard
    ) as read_dashboard:
        playlists.return_value.get_user_playlists.return_value = []
        assert auth_client.get("/api/library").status_code == 200
    assert read_dashboard.call_args.kwargs["last_login_at"] == (datetime.fromisoformat(stored) if stored else None)
