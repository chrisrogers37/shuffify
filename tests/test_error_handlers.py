"""
Tests for global Flask error handlers.

Verifies that each service-layer exception is caught and converted
to the correct JSON error response with appropriate HTTP status code.
"""

import pytest
from flask import Flask

from shuffify.error_handlers import register_error_handlers
from shuffify.services import (
    AuthenticationError,
    TokenValidationError,
    PlaylistError,
    PlaylistNotFoundError,
    PlaylistUpdateError,
    ShuffleError,
    InvalidAlgorithmError,
    ParameterValidationError,
    ShuffleExecutionError,
    StateError,
    NoHistoryError,
    AlreadyAtOriginalError,
)


@pytest.fixture
def app():
    """Create a test Flask app with error handlers registered."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_error_handlers(app)

    # Register routes that raise each exception type for testing
    @app.route("/raise/auth-error")
    def raise_auth_error():
        raise AuthenticationError("Test auth error")

    @app.route("/raise/token-validation-error")
    def raise_token_validation_error():
        raise TokenValidationError("Test token error")

    @app.route("/raise/playlist-not-found")
    def raise_playlist_not_found():
        raise PlaylistNotFoundError("Test not found")

    @app.route("/raise/playlist-update-error")
    def raise_playlist_update_error():
        raise PlaylistUpdateError("Test update error")

    @app.route("/raise/playlist-error")
    def raise_playlist_error():
        raise PlaylistError("Test playlist error")

    @app.route("/raise/invalid-algorithm")
    def raise_invalid_algorithm():
        raise InvalidAlgorithmError("Test invalid algorithm")

    @app.route("/raise/parameter-validation-error")
    def raise_parameter_validation_error():
        raise ParameterValidationError("Test param error")

    @app.route("/raise/shuffle-execution-error")
    def raise_shuffle_execution_error():
        raise ShuffleExecutionError("Test execution error")

    @app.route("/raise/shuffle-error")
    def raise_shuffle_error():
        raise ShuffleError("Test shuffle error")

    @app.route("/raise/no-history")
    def raise_no_history():
        raise NoHistoryError("Test no history")

    @app.route("/raise/already-at-original")
    def raise_already_at_original():
        raise AlreadyAtOriginalError("Test already original")

    @app.route("/raise/state-error")
    def raise_state_error():
        raise StateError("Test state error")

    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


class TestErrorHandlerResponseFormat:
    """Verify all error responses have the standard JSON structure."""

    def _assert_json_error(self, response, expected_status, expected_category="error"):
        """Helper to verify standard error response format."""
        assert response.status_code == expected_status
        data = response.get_json()
        assert data is not None, "Response should be JSON"
        assert "success" in data
        assert data["success"] is False
        assert "message" in data
        assert "category" in data
        assert data["category"] == expected_category

    def test_authentication_error_returns_401(self, client):
        response = client.get("/raise/auth-error")
        self._assert_json_error(response, 401)
        assert "log in" in response.get_json()["message"].lower()

    def test_token_validation_error_returns_401(self, client):
        response = client.get("/raise/token-validation-error")
        self._assert_json_error(response, 401)
        assert "session" in response.get_json()["message"].lower()

    def test_playlist_not_found_returns_404(self, client):
        response = client.get("/raise/playlist-not-found")
        self._assert_json_error(response, 404)

    def test_playlist_update_error_returns_500(self, client):
        response = client.get("/raise/playlist-update-error")
        self._assert_json_error(response, 500)

    def test_playlist_error_returns_400(self, client):
        response = client.get("/raise/playlist-error")
        self._assert_json_error(response, 400)

    def test_invalid_algorithm_returns_400(self, client):
        response = client.get("/raise/invalid-algorithm")
        self._assert_json_error(response, 400)

    def test_parameter_validation_error_returns_400(self, client):
        response = client.get("/raise/parameter-validation-error")
        self._assert_json_error(response, 400)

    def test_shuffle_execution_error_returns_500(self, client):
        response = client.get("/raise/shuffle-execution-error")
        self._assert_json_error(response, 500)

    def test_shuffle_error_returns_500(self, client):
        response = client.get("/raise/shuffle-error")
        self._assert_json_error(response, 500)

    def test_no_history_error_returns_404(self, client):
        response = client.get("/raise/no-history")
        self._assert_json_error(response, 404)

    def test_already_at_original_returns_400_with_info_category(self, client):
        response = client.get("/raise/already-at-original")
        self._assert_json_error(response, 400, expected_category="info")

    def test_state_error_returns_500(self, client):
        response = client.get("/raise/state-error")
        self._assert_json_error(response, 500)


class TestHTTPErrorHandlers:
    """Test standard HTTP error handlers."""

    def test_404_for_api_route_returns_json(self, client):
        response = client.get("/api/nonexistent")
        assert response.status_code == 404
        data = response.get_json()
        assert data is not None
        assert data["success"] is False

    def test_404_for_html_route_returns_default(self, client):
        response = client.get("/nonexistent-page")
        # Non-API routes may return HTML 404 or JSON depending on implementation
        assert response.status_code == 404
