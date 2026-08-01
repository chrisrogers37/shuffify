"""
Tests for error page rendering and route exception handling.

Covers the global 500 handler's HTML vs JSON branching
and the broadened exception handling in schedules, settings,
and refresh routes.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import abort

from shuffify.services import (
    ScheduleError,
    UserSettingsError,
)


@pytest.fixture
def error_app(db_app):
    """App configured to use error handlers instead of propagating."""
    db_app.config["PROPAGATE_EXCEPTIONS"] = False
    db_app.testing = False
    return db_app


class TestErrorContentNegotiation:
    """SR-040: every HTTP error handler negotiates on the same predicate.

    Before this, `handle_csrf_error` / 400 / 401 always answered JSON, so a
    browser whose form POST failed CSRF validation was shown a raw JSON
    body, and there was no 404 template at all so browser 404s fell through
    to the Werkzeug default page.
    """

    def test_404_browser_navigation_returns_html_page(self, error_app):
        with error_app.test_client() as client:
            resp = client.get(
                "/no-such-page",
                headers={"Accept": "text/html"},
            )
            assert resp.status_code == 404
            assert resp.content_type.startswith("text/html")
            assert b"Page not found" in resp.data

    def test_404_unopinionated_client_still_gets_a_page(self, error_app):
        # `*/*` expresses no preference. Answering it with a page is the
        # behaviour the 404/500 handlers already had; only a positive JSON
        # signal switches to the envelope.
        with error_app.test_client() as client:
            resp = client.get("/no-such-page")
            assert resp.status_code == 404
            assert resp.get_json(silent=True) is None

    def test_404_explicit_json_accept_returns_json(self, error_app):
        with error_app.test_client() as client:
            resp = client.get(
                "/no-such-page",
                headers={"Accept": "application/json"},
            )
            assert resp.status_code == 404
            assert resp.get_json()["success"] is False

    def test_404_api_route_returns_json(self, error_app):
        with error_app.test_client() as client:
            resp = client.get("/api/no-such-thing")
            assert resp.status_code == 404
            assert resp.get_json()["success"] is False

    def test_csrf_failure_on_form_post_returns_html_page(
        self, error_app
    ):
        """The headline SR-040 case: a browser form POST, not a fetch()."""
        from flask_wtf.csrf import CSRFError

        @error_app.route("/test-csrf", methods=["POST"])
        def trigger_csrf():
            raise CSRFError("The CSRF token is missing.")

        with error_app.test_client() as client:
            resp = client.post(
                "/test-csrf",
                headers={"Accept": "text/html"},
            )
            assert resp.status_code == 400
            assert resp.content_type.startswith("text/html")
            assert b"could not be verified" in resp.data

    def test_csrf_failure_on_ajax_still_returns_json(
        self, error_app
    ):
        """The JSON callers must keep the envelope they parse."""
        from flask_wtf.csrf import CSRFError

        @error_app.route("/test-csrf-ajax", methods=["POST"])
        def trigger_csrf_ajax():
            raise CSRFError("The CSRF token is missing.")

        with error_app.test_client() as client:
            resp = client.post(
                "/test-csrf-ajax",
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["success"] is False
            assert "could not be verified" in data["message"]


    # handle_bad_request and handle_unauthorized were the two siblings found
    # while fixing SR-040 -- neither was named in the issue, and neither had
    # a test. Reverting either one to its old always-JSON form left the whole
    # 73-test error-handling suite green, so nothing guarded them.
    #
    # These assert on text that appears ONLY on the rendered page. The JSON
    # body for a 400 contains the string "Bad request" too, so asserting that
    # would pass against a reverted handler and guard nothing.

    def test_400_browser_navigation_returns_html_page(self, error_app):
        @error_app.route("/test-400-page")
        def trigger_400_page():
            abort(400)

        with error_app.test_client() as client:
            resp = client.get(
                "/test-400-page",
                headers={"Accept": "text/html"},
            )
            assert resp.status_code == 400
            assert resp.content_type.startswith("text/html")
            assert resp.get_json(silent=True) is None
            assert b"could not make sense of that request" in resp.data

    def test_400_json_client_still_returns_envelope(self, error_app):
        @error_app.route("/test-400-json")
        def trigger_400_json():
            abort(400)

        with error_app.test_client() as client:
            resp = client.get(
                "/test-400-json",
                headers={"Accept": "application/json"},
            )
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["success"] is False
            assert data["message"] == "Bad request."

    def test_401_browser_navigation_returns_html_page(self, error_app):
        @error_app.route("/test-401-page")
        def trigger_401_page():
            abort(401)

        with error_app.test_client() as client:
            resp = client.get(
                "/test-401-page",
                headers={"Accept": "text/html"},
            )
            assert resp.status_code == 401
            assert resp.content_type.startswith("text/html")
            assert resp.get_json(silent=True) is None
            assert b"need to be signed in" in resp.data

    def test_401_json_client_still_returns_envelope(self, error_app):
        @error_app.route("/test-401-json")
        def trigger_401_json():
            abort(401)

        with error_app.test_client() as client:
            resp = client.get(
                "/test-401-json",
                headers={"Accept": "application/json"},
            )
            assert resp.status_code == 401
            data = resp.get_json()
            assert data["success"] is False
            assert data["message"] == "Please log in first."


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

    @patch("shuffify.routes.AuthService")
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

    @patch("shuffify.routes.AuthService")
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

    @patch("shuffify.routes.AuthService")
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

    @patch("shuffify.routes.AuthService")
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
