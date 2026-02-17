"""
Tests for rate limiting on authentication endpoints.

Verifies that /login and /callback are rate-limited to prevent
abuse of the OAuth flow.
"""

import pytest
from unittest.mock import patch


@pytest.fixture
def rate_limited_app():
    """Create a Flask app with rate limiting enabled (in-memory)."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_client_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_client_secret"
    os.environ["SPOTIFY_REDIRECT_URI"] = (
        "http://localhost:5000/callback"
    )
    os.environ["SECRET_KEY"] = "test-secret-key-for-testing"
    os.environ.pop("DATABASE_URL", None)

    from shuffify import create_app

    app = create_app("development")
    app.config["TESTING"] = True
    app.config["SCHEDULER_ENABLED"] = False

    with app.app_context():
        from shuffify.models.db import db

        db.create_all()

    return app


@pytest.fixture
def rate_client(rate_limited_app):
    """Test client for rate limiting tests."""
    return rate_limited_app.test_client()


class TestLoginRateLimit:
    """Tests for rate limiting on the /login endpoint."""

    def test_login_allows_requests_under_limit(self, rate_client):
        """Requests under the limit should not return 429."""
        for _ in range(5):
            response = rate_client.get("/login")
            assert response.status_code != 429

    @patch("shuffify.routes.core.AuthService")
    def test_login_returns_429_when_limit_exceeded(
        self, mock_auth, rate_client
    ):
        """Exceeding the rate limit should return 429."""
        mock_auth.get_auth_url.return_value = (
            "https://accounts.spotify.com/authorize"
        )
        responses = []
        for _ in range(11):
            resp = rate_client.get("/login?legal_consent=true")
            responses.append(resp.status_code)
        assert 429 in responses

    def test_login_429_response_is_json(self, rate_client):
        """The 429 response should use standard JSON error format."""
        for _ in range(11):
            response = rate_client.get("/login")
        response = rate_client.get("/login")
        if response.status_code == 429:
            data = response.get_json()
            assert data is not None
            assert data["success"] is False
            assert "Too many requests" in data["message"]


class TestCallbackRateLimit:
    """Tests for rate limiting on the /callback endpoint."""

    def test_callback_allows_requests_under_limit(self, rate_client):
        """Requests under the limit should not return 429."""
        for _ in range(10):
            response = rate_client.get("/callback")
            assert response.status_code != 429

    def test_callback_returns_429_when_limit_exceeded(
        self, rate_client
    ):
        """Exceeding the rate limit should return 429."""
        responses = []
        for _ in range(21):
            resp = rate_client.get("/callback")
            responses.append(resp.status_code)
        assert 429 in responses

    def test_callback_429_response_is_json(self, rate_client):
        """The 429 response should use standard JSON error format."""
        for _ in range(21):
            response = rate_client.get("/callback")
        response = rate_client.get("/callback")
        if response.status_code == 429:
            data = response.get_json()
            assert data is not None
            assert data["success"] is False
            assert "Too many requests" in data["message"]


class TestRateLimitDoesNotAffectOtherRoutes:
    """Verify rate limits are scoped to auth endpoints only."""

    def test_health_endpoint_not_rate_limited(self, rate_client):
        """The /health endpoint should never return 429."""
        for _ in range(30):
            response = rate_client.get("/health")
            assert response.status_code == 200

    def test_index_not_rate_limited(self, rate_client):
        """The / endpoint should never return 429."""
        for _ in range(30):
            response = rate_client.get("/")
            assert response.status_code != 429


class TestLimiterInitialization:
    """Tests for limiter initialization behavior."""

    def test_limiter_is_available(self, rate_limited_app):
        """The limiter should be initialized in the app."""
        from shuffify import get_limiter

        limiter = get_limiter()
        assert limiter is not None

    def test_rate_limit_header_present(self, rate_client):
        """Rate-limited responses should include Retry-After."""
        for _ in range(11):
            response = rate_client.get("/login")
        response = rate_client.get("/login")
        if response.status_code == 429:
            assert "Retry-After" in response.headers
