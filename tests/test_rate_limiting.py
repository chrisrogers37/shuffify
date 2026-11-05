"""
Tests for rate limiting.

Verifies that /login and /callback are rate-limited per address to
prevent abuse of the OAuth flow, and that the other limits key by the
signed-in user, or by the address when no one is signed in.
"""

import time
from unittest.mock import patch

import pytest


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


class TestRateLimitLog:
    """A rate-limited request is logged without the client's address."""

    def test_a_429_logs_no_client_address(self, rate_client, shuffify_logs):
        statuses = [
            rate_client.get("/callback", environ_base={"REMOTE_ADDR": "203.0.113.77"}).status_code
            for _ in range(21)
        ]
        assert 429 in statuses
        assert "Rate limit exceeded" in shuffify_logs.text
        assert "203.0.113.77" not in shuffify_logs.text


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

    def test_session_context_not_rate_limited(self, rate_client):
        """Session reads remain independent of OAuth endpoint limits."""
        for _ in range(30):
            response = rate_client.get("/api/session")
            assert response.status_code != 429


class TestLimiterInitialization:
    """Tests for limiter initialization behavior."""

    def test_rate_limit_header_present(self, rate_client):
        """Rate-limited responses should include Retry-After."""
        for _ in range(11):
            response = rate_client.get("/login")
        response = rate_client.get("/login")
        if response.status_code == 429:
            assert "Retry-After" in response.headers


# Documentation addresses (RFC 5737), never a real client's.
SHARED_ADDRESS = "192.0.2.10"
OTHER_ADDRESS = "198.51.100.20"
SHUFFLE = "/api/shuffle/examplePlaylist"
SIGNED_IN_LIMITS = [
    SHUFFLE,
    "/api/workshop/examplePlaylist/commit",
    "/api/schedules/1/run",
    "/api/playlist/examplePlaylist/setup/spotify-playlists",
]


def _signed_in(app, user_id, display_name=None):
    """A client signed in as ``user_id``, shown as ``display_name`` (by default the id)."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["spotify_token"] = {
            "access_token": "test_token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "expires_at": time.time() + 3600,
            "refresh_token": "test_refresh",
        }
        sess["user_data"] = {"id": user_id, "display_name": display_name or user_id}
    return client


def _status(client, method, path, address, headers=None):
    return client.open(path, method=method, headers=headers, environ_base={"REMOTE_ADDR": address}).status_code


def _allowed_before_429(client, method, path, address, headers=None, cap=50):
    """Send requests until the first 429 and return how many got through."""
    for sent in range(cap):
        if _status(client, method, path, address, headers) == 429:
            return sent
    raise AssertionError(f"{path} never answered 429 in {cap} requests")


class TestRateLimitKeys:
    """Which bucket a request lands in.

    Signed-in limits key by the session's user; /login, /callback and
    signed-out requests key by address.
    """

    @pytest.mark.parametrize("path", SIGNED_IN_LIMITS)
    def test_two_signed_in_users_behind_one_address_do_not_share_a_bucket(self, app, path):
        alice, bob = _signed_in(app, "alice"), _signed_in(app, "bob")
        allowed = _allowed_before_429(alice, "POST", path, SHARED_ADDRESS)
        assert allowed > 0
        assert _allowed_before_429(bob, "POST", path, SHARED_ADDRESS) == allowed

    def test_one_user_who_changes_address_still_shares_one_bucket(self, app):
        alice = _signed_in(app, "alice")
        _allowed_before_429(alice, "POST", SHUFFLE, SHARED_ADDRESS)
        assert _status(alice, "POST", SHUFFLE, OTHER_ADDRESS) == 429

    @pytest.mark.parametrize("path", ["/login", "/callback"])
    def test_sign_in_routes_stay_per_address(self, app, path):
        alice, bob = _signed_in(app, "alice"), _signed_in(app, "bob")
        _allowed_before_429(alice, "GET", path, SHARED_ADDRESS)
        assert _status(bob, "GET", path, SHARED_ADDRESS) == 429
        assert _status(bob, "GET", path, OTHER_ADDRESS) != 429

    def test_a_signed_out_request_falls_back_to_the_address(self, app):
        first, second = app.test_client(), app.test_client()
        allowed = _allowed_before_429(first, "POST", SHUFFLE, SHARED_ADDRESS)
        assert _status(second, "POST", SHUFFLE, SHARED_ADDRESS) == 429
        assert _allowed_before_429(second, "POST", SHUFFLE, OTHER_ADDRESS) == allowed

    @pytest.mark.parametrize("forwarded", ["user:alice", "alice"])
    def test_a_signed_out_request_never_counts_against_a_signed_in_user(self, app, forwarded):
        alice, stranger = _signed_in(app, "alice"), app.test_client()
        _allowed_before_429(stranger, "POST", SHUFFLE, SHARED_ADDRESS, headers={"X-Forwarded-For": forwarded})
        assert _status(alice, "POST", SHUFFLE, OTHER_ADDRESS) != 429

    def test_users_who_share_a_display_name_do_not_share_a_bucket(self, app):
        alice, bob = _signed_in(app, "alice", "Sam"), _signed_in(app, "bob", "Sam")
        _allowed_before_429(alice, "POST", SHUFFLE, SHARED_ADDRESS)
        assert _status(bob, "POST", SHUFFLE, SHARED_ADDRESS) != 429
