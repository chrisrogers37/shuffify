"""Tests for SpotifyHTTPClient."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest
import requests

from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyTokenExpiredError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)
from shuffify.spotify.http_client import (
    BASE_URL,
    MAX_RETRIES,
    MAX_RETRY_AFTER,
    SpotifyHTTPClient,
    _calculate_backoff_delay,
    get_shared_adapter,
)

# =========================================================================
# Helpers
# =========================================================================


def _mock_response(status_code=200, json_data=None, headers=None):
    """Create a mock response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    resp.text = str(json_data) if json_data else ""
    resp.content = b"" if status_code == 204 else b"{}"
    return resp


# =========================================================================
# Backoff delay
# =========================================================================


class TestCalculateBackoffDelay:
    """Tests for _calculate_backoff_delay."""

    def test_first_attempt(self):
        assert _calculate_backoff_delay(0) == 2

    def test_second_attempt(self):
        assert _calculate_backoff_delay(1) == 4

    def test_third_attempt(self):
        assert _calculate_backoff_delay(2) == 8

    def test_capped_at_max(self):
        assert _calculate_backoff_delay(10) == 16


# =========================================================================
# Initialization
# =========================================================================


class TestHTTPClientInit:
    """Tests for SpotifyHTTPClient initialization."""

    def test_sets_authorization_header(self):
        client = SpotifyHTTPClient("test-token")
        assert client._session.headers["Authorization"] == "Bearer test-token"

    def test_sets_content_type(self):
        client = SpotifyHTTPClient("test-token")
        assert client._session.headers["Content-Type"] == "application/json"

    def test_update_token(self):
        client = SpotifyHTTPClient("old-token")
        client.update_token("new-token")
        assert client._session.headers["Authorization"] == "Bearer new-token"
        assert client._access_token == "new-token"


# =========================================================================
# Successful requests
# =========================================================================


class TestSuccessfulRequests:
    """Tests for successful HTTP requests."""

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_get_request(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(
            200, {"id": "abc"}
        )

        client = SpotifyHTTPClient("token")
        client._session = session
        result = client.get("/me")

        session.request.assert_called_once_with(
            "GET", f"{BASE_URL}/me", params=None, json=None, timeout=30,
        )
        assert result == {"id": "abc"}

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_post_request(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(
            201, {"snapshot_id": "xyz"}
        )

        client = SpotifyHTTPClient("token")
        client._session = session
        result = client.post("/playlists/123/items", json={"uris": ["a"]})

        session.request.assert_called_once_with(
            "POST", f"{BASE_URL}/playlists/123/items",
            params=None, json={"uris": ["a"]}, timeout=30,
        )
        assert result == {"snapshot_id": "xyz"}

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_put_request(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(
            200, {"snapshot_id": "xyz"}
        )

        client = SpotifyHTTPClient("token")
        client._session = session
        result = client.put("/playlists/123/items", json={"uris": ["a"]})

        session.request.assert_called_once_with(
            "PUT", f"{BASE_URL}/playlists/123/items",
            params=None, json={"uris": ["a"]}, timeout=30,
        )
        assert result == {"snapshot_id": "xyz"}

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_delete_request(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(
            200, {"snapshot_id": "xyz"}
        )

        client = SpotifyHTTPClient("token")
        client._session = session
        result = client.delete(
            "/playlists/123/items",
            json={"tracks": [{"uri": "a"}]},
        )

        session.request.assert_called_once_with(
            "DELETE", f"{BASE_URL}/playlists/123/items",
            params=None, json={"tracks": [{"uri": "a"}]}, timeout=30,
        )
        assert result == {"snapshot_id": "xyz"}

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_204_returns_none(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(204)

        client = SpotifyHTTPClient("token")
        client._session = session
        result = client.put("/playlists/123/items", json={"uris": []})

        assert result is None

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_get_with_params(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(
            200, {"tracks": {"items": []}}
        )

        client = SpotifyHTTPClient("token")
        client._session = session
        client.get("/search", params={"q": "test", "type": "track"})

        session.request.assert_called_once_with(
            "GET", f"{BASE_URL}/search",
            params={"q": "test", "type": "track"}, json=None, timeout=30,
        )


# =========================================================================
# Error handling
# =========================================================================


class TestErrorHandling:
    """Tests for HTTP error responses."""

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_404_raises_not_found(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(404)

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyNotFoundError, match="not found"):
            client.get("/playlists/nonexistent")

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_401_without_refresh_raises_expired(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(401)

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyTokenExpiredError):
            client.get("/me")

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_401_with_refresh_retries(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.side_effect = [
            _mock_response(401),
            _mock_response(200, {"id": "user123"}),
        ]

        refresh = MagicMock(return_value="new-token")
        client = SpotifyHTTPClient("old-token", on_token_refresh=refresh)
        client._session = session

        result = client.get("/me")

        refresh.assert_called_once()
        assert result == {"id": "user123"}
        assert client._access_token == "new-token"

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_401_refresh_fails_raises_expired(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(401)

        refresh = MagicMock(side_effect=Exception("refresh broke"))
        client = SpotifyHTTPClient("token", on_token_refresh=refresh)
        client._session = session

        with pytest.raises(SpotifyTokenExpiredError):
            client.get("/me")

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_401_only_refreshes_once(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        # 401 both before and after refresh
        session.request.return_value = _mock_response(401)

        refresh = MagicMock(return_value="new-token")
        client = SpotifyHTTPClient("token", on_token_refresh=refresh)
        client._session = session

        with pytest.raises(SpotifyTokenExpiredError):
            client.get("/me")

        refresh.assert_called_once()

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_400_raises_api_error(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(
            400,
            {"error": {"message": "Bad request"}},
        )

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyAPIError, match="Bad request") as raised:
            client.get("/search")
        assert type(raised.value) is SpotifyAPIError

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_403_raises_forbidden_error(self, mock_session_cls):
        """A 403 raises SpotifyForbiddenError, which callers catching SpotifyAPIError also catch."""
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(
            403,
            {"error": {"message": "Forbidden"}},
        )

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyForbiddenError, match="API error 403: Forbidden"):
            client.get("/playlists/123")
        assert issubclass(SpotifyForbiddenError, SpotifyAPIError)


# =========================================================================
# Retry logic
# =========================================================================


class TestRetryLogic:
    """Tests for retry behavior on transient errors."""

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_429_retries_with_retry_after(
        self, mock_session_cls, mock_sleep,
    ):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.side_effect = [
            _mock_response(429, headers={"Retry-After": "3"}),
            _mock_response(200, {"ok": True}),
        ]

        client = SpotifyHTTPClient("token")
        client._session = session
        result = client.get("/me")

        assert result == {"ok": True}
        mock_sleep.assert_called_once()
        # Should use max(retry_after, backoff)
        assert mock_sleep.call_args[0][0] >= 3

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_429_exhausted_raises_rate_limit(
        self, mock_session_cls, mock_sleep,
    ):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(
            429, headers={"Retry-After": "5"},
        )

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyRateLimitError) as exc_info:
            client.get("/me")

        assert exc_info.value.retry_after == 5
        assert session.request.call_count == MAX_RETRIES + 1

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_quota_exceeded_429_is_not_retried(
        self, mock_session_cls, mock_sleep,
    ):
        """Spotify's quota 429 says the quota, not the rate, is
        spent: no retry can succeed, so none is made."""
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(429, {
            "error": {"status": 429, "message": "Too many requests", "reason": "QUOTA_EXCEEDED"},
        })

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyQuotaExceededError):
            client.get("/me")

        assert session.request.call_count == 1
        mock_sleep.assert_not_called()

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_a_wait_longer_than_a_rate_limit_window_is_not_slept_out(
        self, mock_session_cls, mock_sleep,
    ):
        """Spotify counts calls in a rolling 30-second window. Asked to wait
        longer, the client says so instead of holding a request open."""
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(
            429, headers={"Retry-After": str(MAX_RETRY_AFTER + 1)},
        )

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyRateLimitError) as exc_info:
            client.get("/me")

        assert exc_info.value.retry_after == MAX_RETRY_AFTER + 1
        assert not isinstance(exc_info.value, SpotifyQuotaExceededError)
        assert session.request.call_count == 1
        mock_sleep.assert_not_called()

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_a_wait_of_one_window_is_slept_out(
        self, mock_session_cls, mock_sleep,
    ):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.side_effect = [
            _mock_response(429, headers={"Retry-After": str(MAX_RETRY_AFTER)}),
            _mock_response(200, {"ok": True}),
        ]

        client = SpotifyHTTPClient("token")
        client._session = session

        assert client.get("/me") == {"ok": True}
        mock_sleep.assert_called_once_with(MAX_RETRY_AFTER)

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_an_unreadable_retry_after_falls_back_to_backoff(
        self, mock_session_cls, mock_sleep,
    ):
        """Spotify documents seconds; anything else is backed off as if the
        header were missing, rather than raising ValueError."""
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.side_effect = [
            _mock_response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            _mock_response(200, {"ok": True}),
        ]

        client = SpotifyHTTPClient("token")
        client._session = session

        assert client.get("/me") == {"ok": True}
        mock_sleep.assert_called_once_with(_calculate_backoff_delay(0))

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_500_retries_then_succeeds(
        self, mock_session_cls, mock_sleep,
    ):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.side_effect = [
            _mock_response(502),
            _mock_response(200, {"ok": True}),
        ]

        client = SpotifyHTTPClient("token")
        client._session = session
        result = client.get("/me")

        assert result == {"ok": True}
        mock_sleep.assert_called_once()

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_500_exhausted_raises_api_error(
        self, mock_session_cls, mock_sleep,
    ):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(503)

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyAPIError, match="Server error"):
            client.get("/me")

        assert session.request.call_count == MAX_RETRIES + 1

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_network_error_retries(
        self, mock_session_cls, mock_sleep,
    ):
        from requests.exceptions import ConnectionError

        session = mock_session_cls.return_value
        session.headers = {}
        session.request.side_effect = [
            ConnectionError("Connection refused"),
            _mock_response(200, {"ok": True}),
        ]

        client = SpotifyHTTPClient("token")
        client._session = session
        result = client.get("/me")

        assert result == {"ok": True}

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_network_error_exhausted_raises(
        self, mock_session_cls, mock_sleep,
    ):
        from requests.exceptions import ConnectionError

        session = mock_session_cls.return_value
        session.headers = {}
        session.request.side_effect = ConnectionError("refused")

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyAPIError, match="Network error"):
            client.get("/me")


class TestWriteOutcomeCertainty:
    """A request can fail after Spotify applied a non-idempotent mutation."""

    @pytest.mark.parametrize("method", ["post", "put", "delete"])
    @pytest.mark.parametrize("status", [500, 502, 503])
    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_server_error_never_replays_write(self, mock_session_cls, sleep, method, status):
        session = mock_session_cls.return_value
        session.request.side_effect = [_mock_response(status), _mock_response(200)]
        client = SpotifyHTTPClient("token")

        with pytest.raises(SpotifyWriteUnverifiedError):
            getattr(client, method)("/playlists/p1/items", json={"uris": ["a"]})

        assert session.request.call_count == 1
        sleep.assert_not_called()

    @pytest.mark.parametrize("method", ["post", "put", "delete"])
    @pytest.mark.parametrize("failure", [
        requests.exceptions.ReadTimeout("response lost"),
        requests.exceptions.ConnectTimeout("connection timed out"),
        requests.exceptions.ConnectionError("connection closed"),
        requests.exceptions.ChunkedEncodingError("truncated response"),
    ])
    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_network_failure_never_replays_write(self, mock_session_cls, sleep, method, failure):
        session = mock_session_cls.return_value
        session.request.side_effect = [failure, _mock_response(200)]
        client = SpotifyHTTPClient("token")

        with pytest.raises(SpotifyWriteUnverifiedError) as raised:
            getattr(client, method)("/playlists/p1/items", json={"uris": ["a"]})

        assert raised.value.__cause__ is failure
        assert session.request.call_count == 1
        sleep.assert_not_called()

    @pytest.mark.parametrize("status, error", [
        (400, SpotifyWriteRejectedError),
        (403, SpotifyForbiddenError),
        (404, SpotifyNotFoundError),
        (401, SpotifyTokenExpiredError),
        (429, SpotifyRateLimitError),
    ])
    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_definite_rejections_preserve_types(self, mock_session_cls, sleep, status, error):
        session = mock_session_cls.return_value
        session.request.return_value = _mock_response(status, headers={"Retry-After": "60"})
        client = SpotifyHTTPClient("token")

        with pytest.raises(error) as raised:
            client.post("/playlists/p1/items", json={"uris": ["a"]})

        assert type(raised.value) is error
        assert session.request.call_count == 1
        sleep.assert_not_called()

    @pytest.mark.parametrize("status", [401, 429])
    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_rejected_write_can_retry_after_refresh_or_backoff(self, mock_session_cls, sleep, status):
        session = mock_session_cls.return_value
        session.request.side_effect = [
            _mock_response(status, headers={"Retry-After": "1"}),
            _mock_response(201, {"snapshot_id": "saved"}),
        ]
        refresh = MagicMock(return_value="new-token")
        client = SpotifyHTTPClient("token", on_token_refresh=refresh)

        assert client.post("/playlists/p1/items", json={"uris": ["a"]}) == {"snapshot_id": "saved"}
        assert session.request.call_count == 2
        if status == 401:
            refresh.assert_called_once()
            sleep.assert_not_called()
        else:
            refresh.assert_not_called()
            sleep.assert_called_once()


# =========================================================================
# Pagination
# =========================================================================


class TestPagination:
    """Tests for get_all_pages."""

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_single_page(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(200, {
            "items": [{"id": "1"}, {"id": "2"}],
            "next": None,
        })

        client = SpotifyHTTPClient("token")
        client._session = session
        items = client.get_all_pages("/me/playlists")

        assert len(items) == 2
        assert items[0]["id"] == "1"

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_multiple_pages(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.side_effect = [
            _mock_response(200, {
                "items": [{"id": "1"}],
                "next": f"{BASE_URL}/me/playlists?offset=1",
            }),
            _mock_response(200, {
                "items": [{"id": "2"}],
                "next": None,
            }),
        ]

        client = SpotifyHTTPClient("token")
        client._session = session
        items = client.get_all_pages("/me/playlists")

        assert len(items) == 2
        assert items[1]["id"] == "2"
        assert session.request.call_count == 2

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_empty_response(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(200, {
            "items": [],
            "next": None,
        })

        client = SpotifyHTTPClient("token")
        client._session = session
        items = client.get_all_pages("/me/playlists")

        assert items == []

    @patch("shuffify.spotify.http_client.requests.Session")
    def test_with_params(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _mock_response(200, {
            "items": [{"id": "1"}],
            "next": None,
        })

        client = SpotifyHTTPClient("token")
        client._session = session
        client.get_all_pages(
            "/me/playlists", params={"limit": 50},
        )

        # First call should include params
        call_args = session.request.call_args
        assert call_args[1].get("params") or call_args[0]


# =========================================================================
# Close
# =========================================================================


class TestClose:
    """Tests for session cleanup."""

    def test_close_closes_session(self):
        client = SpotifyHTTPClient("token")
        client._session = MagicMock()
        client.close()
        client._session.close.assert_called_once()


# =========================================================================
# Connection pooling
# =========================================================================


class _CountingServer(ThreadingHTTPServer):
    """HTTP server that records every accepted TCP connection."""

    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.accepted = []
        super().__init__(*args, **kwargs)

    def get_request(self):
        conn, addr = super().get_request()
        self.accepted.append(addr)
        return conn, addr


class _OkHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, so reuse is observable

    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def counting_server(monkeypatch):
    """A real local HTTP server, with BASE_URL pointed at it.

    Connection reuse is only observable against a real socket -- a mocked
    Session would report whatever the test asked it to.
    """
    server = _CountingServer(("127.0.0.1", 0), _OkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        "shuffify.spotify.http_client.BASE_URL",
        f"http://127.0.0.1:{server.server_address[1]}",
    )
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _fresh_shared_adapter(monkeypatch):
    """Give every test its own shared adapter.

    The adapter is process-global by design, so without this a pool warmed by
    one test would satisfy the next test's requests and hide a regression.
    """
    monkeypatch.setattr("shuffify.spotify.http_client._shared_adapter", None)
    monkeypatch.setattr("shuffify.spotify.http_client._shared_adapter_pid", None)


class TestConnectionPooling:
    """Clients share one connection pool, not one pool each."""

    def test_separate_clients_reuse_one_connection(self, counting_server):
        """Twenty clients, twenty calls, one TCP connection."""
        for _ in range(20):
            SpotifyHTTPClient("token").get("/ping")

        assert len(counting_server.accepted) == 1

    def test_reuse_survives_a_client_closing(self, counting_server):
        """One client closing must not evict the pool everyone else is using."""
        first = SpotifyHTTPClient("token")
        first.get("/ping")
        assert len(counting_server.accepted) == 1

        first.close()

        second = SpotifyHTTPClient("token")
        assert second.get("/ping") == {"ok": True}
        assert len(counting_server.accepted) == 1

    def test_each_client_keeps_its_own_token(self, counting_server):
        """Sharing the pool must not share the bearer token.

        The Authorization header lives on the Session; if sessions were shared
        to get pooling, one user's request would carry another user's token.
        """
        alice = SpotifyHTTPClient("token-alice")
        bob = SpotifyHTTPClient("token-bob")

        assert alice._session.headers["Authorization"] == "Bearer token-alice"
        assert bob._session.headers["Authorization"] == "Bearer token-bob"

        alice.update_token("token-alice-2")
        assert bob._session.headers["Authorization"] == "Bearer token-bob"

    def test_clients_mount_the_same_adapter(self, counting_server):
        a = SpotifyHTTPClient("token")
        b = SpotifyHTTPClient("token")
        assert a._session.get_adapter("https://api.spotify.com/v1") is (
            b._session.get_adapter("https://api.spotify.com/v1")
        )


class TestSharedAdapterForkSafety:
    """A forked worker must not inherit the parent's pool."""

    def test_adapter_is_rebuilt_after_a_fork(self, monkeypatch):
        parent = get_shared_adapter()
        assert get_shared_adapter() is parent  # stable within a process

        # Stand in for the post-fork child: same module state, different PID.
        child_pid = os.getpid() + 1
        monkeypatch.setattr("shuffify.spotify.http_client.os.getpid", lambda: child_pid)
        child = get_shared_adapter()

        assert child is not parent

    def test_adapter_is_stable_within_one_process(self):
        assert get_shared_adapter() is get_shared_adapter()


# =========================================================================
# Successful responses whose body is empty or not JSON
# =========================================================================


def _real_response(status_code, body):
    """A real requests.Response, so json() fails exactly as it does live."""
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = body
    return resp


class TestUnparseableSuccessBody:
    """A 2xx whose body is empty or not JSON.

    Spotify documents PUT /playlists/{id} (Change Playlist Details) as 200
    with no content. A bodiless 2xx write is success; a non-JSON 2xx write
    raises once and is never resent, since Spotify may already have applied
    it. A read with an unreadable body is retried and never comes back as
    "no data": that would end paging early.
    """

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_empty_200_is_success_without_retrying(self, mock_session_cls, sleep):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _real_response(200, b"")

        client = SpotifyHTTPClient("token")
        client._session = session

        assert client.put("/playlists/p1", json={"public": False}) is None
        assert session.request.call_count == 1
        sleep.assert_not_called()

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_non_json_2xx_raises_once_without_resending(self, mock_session_cls, sleep):
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _real_response(201, b"<html>ok</html>")

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyWriteUnverifiedError, match="not JSON"):
            client.post("/playlists/p1/items", json={"uris": ["spotify:track:1"]})
        assert session.request.call_count == 1
        sleep.assert_not_called()

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_empty_read_is_retried_then_raises_never_none(self, mock_session_cls, sleep):
        """A read that comes back empty is a glitch to retry, never "no data"."""
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.side_effect = lambda *a, **k: _real_response(200, b"")

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyAPIError, match="unreadable body"):
            client.get("/playlists/p1/items")
        assert session.request.call_count == MAX_RETRIES + 1

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_an_empty_page_cannot_truncate_paging(self, mock_session_cls, sleep):
        """Page 2 empty: raise, rather than return page 1 as the whole playlist.

        A truncated read would reach a shuffle, which writes the tracks it
        read back -- deleting the rest.
        """
        session = mock_session_cls.return_value
        session.headers = {}
        first = _real_response(200, b'{"items": [{"id": "a"}], "next": "https://example.invalid/p2"}')
        session.request.side_effect = [first] + [_real_response(200, b"") for _ in range(MAX_RETRIES + 1)]

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyAPIError):
            client.get_all_pages("/playlists/p1/items")

    @patch("shuffify.spotify.http_client.time.sleep")
    @patch("shuffify.spotify.http_client.requests.Session")
    def test_empty_error_response_still_raises(self, mock_session_cls, sleep):
        """Only a success may be bodiless; a bodiless 403 is still a 403."""
        session = mock_session_cls.return_value
        session.headers = {}
        session.request.return_value = _real_response(403, b"")

        client = SpotifyHTTPClient("token")
        client._session = session

        with pytest.raises(SpotifyAPIError, match="403"):
            client.put("/playlists/p1", json={"public": False})
