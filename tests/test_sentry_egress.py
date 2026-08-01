"""Outbound-payload tests for Sentry secret egress (issue #436).

These tests assert on the bytes that would leave the process, not on
configuration. A scrubber verified only in isolation can pass while the real
SDK still ships secrets from a code path the scrubber never walks — that is
exactly how #436 shipped. Each test therefore boots the real _init_sentry
against a local HTTP sink, drives a real error path, and inspects what the
SDK actually POSTed.
"""

import gzip
import json
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from shuffify import _init_sentry, _strip_pii
from shuffify.spotify.auth import SpotifyAuthManager
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import SpotifyTokenError

# Distinctive values so a substring search over the payload cannot produce a
# false negative through coincidental encoding.
AUTH_CODE = "SECRETMARKER-auth-code-8f14e45fceea"
ACCESS_TOKEN = "SECRETMARKER-access-token-c9f0f895fb98"
REFRESH_TOKEN = "SECRETMARKER-refresh-token-45c48cce2e2d"
CLIENT_SECRET = "SECRETMARKER-client-secret-d3d9446802a4"
ALL_SECRETS = (AUTH_CODE, ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET)


class _SinkHandler(BaseHTTPRequestHandler):
    """Collects envelope bodies, transparently decompressing them."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        encoding = (self.headers.get("Content-Encoding") or "").lower()
        if encoding == "gzip":
            body = gzip.decompress(body)
        elif encoding == "deflate":
            body = zlib.decompress(body)
        self.server.captured.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


@pytest.fixture
def sentry_sink():
    """A local Sentry ingest that records raw envelope bodies."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SinkHandler)
    server.captured = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def sentry_client_teardown():
    """Close the global Sentry client so it cannot leak into other tests."""
    yield
    import sentry_sdk

    client = sentry_sdk.get_client()
    if client is not None:
        client.close(timeout=2.0)
    sentry_sdk.init(dsn="")


def _sink_config(server):
    port = server.server_address[1]

    class SinkConfig:
        SENTRY_DSN = f"http://aaaabbbbccccdddd@127.0.0.1:{port}/1"
        SENTRY_ENVIRONMENT = "test"
        SENTRY_TRACES_SAMPLE_RATE = 0.0
        SENTRY_PROFILES_SAMPLE_RATE = 0.0
        SENTRY_RELEASE = ""

    return SinkConfig


def _trigger_token_exchange_error():
    """Drive the real exchange_code failure path from issue #436.

    A malformed expires_in makes TokenInfo.from_dict raise, which lands in
    the generic handler that logs at ERROR with exc_info=True. At that
    moment code, token_data, and the credentials object are live locals.
    """
    credentials = SpotifyCredentials(
        client_id="test-client-id",
        client_secret=CLIENT_SECRET,
        redirect_uri="http://localhost:8000/callback",
    )
    manager = SpotifyAuthManager(credentials)

    class _MalformedResponse:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "token_type": "Bearer",
                "expires_in": "not-a-number",
            }

    with patch("shuffify.spotify.auth.requests.post", return_value=_MalformedResponse):
        with pytest.raises(SpotifyTokenError):
            manager.exchange_code(AUTH_CODE)


def _flush_and_read(server):
    import sentry_sdk

    sentry_sdk.flush(timeout=10)
    return b"\n".join(server.captured).decode("utf-8", errors="replace")


class TestSentryEgress:
    """What actually leaves the process on a token-path exception."""

    def test_token_exchange_error_sends_no_secrets(
        self, sentry_sink, sentry_client_teardown
    ):
        """The real failure path must not put OAuth material on the wire."""
        assert _init_sentry(_sink_config(sentry_sink)) is True

        _trigger_token_exchange_error()
        payload = _flush_and_read(sentry_sink)

        # The report must still be delivered — scrubbing is not silencing.
        assert payload, "no envelope was sent; the error path stopped reporting"
        assert '"exception"' in payload

        for secret in ALL_SECRETS:
            assert secret not in payload, f"{secret[:24]}... egressed to Sentry"

    def test_scrubber_catches_frame_vars_when_capture_is_forced_on(
        self, sentry_sink, sentry_client_teardown
    ):
        """Second layer holds on its own.

        Frame-local capture is disabled at init, so the before_send walk over
        exception frames would be untestable — and silently rot — without
        forcing capture back on. This pins it as live defense in depth.
        """
        import sentry_sdk

        real_init = sentry_sdk.init

        def _init_with_locals(*args, **kwargs):
            kwargs["include_local_variables"] = True
            return real_init(*args, **kwargs)

        with patch("sentry_sdk.init", side_effect=_init_with_locals):
            assert _init_sentry(_sink_config(sentry_sink)) is True

        _trigger_token_exchange_error()
        payload = _flush_and_read(sentry_sink)

        assert '"vars"' in payload, (
            "frame locals were not captured; this test no longer exercises "
            "the scrubber walk it exists to pin"
        )
        for secret in ALL_SECRETS:
            assert secret not in payload, f"{secret[:24]}... survived scrubbing"


class TestStripPiiExceptionFrames:
    """Unit-level coverage of the exception-frame walk."""

    def _event_with_vars(self, frame_vars):
        return {
            "exception": {
                "values": [
                    {
                        "type": "TypeError",
                        "stacktrace": {"frames": [{"vars": dict(frame_vars)}]},
                    }
                ]
            }
        }

    def test_redacts_bare_code_and_token_dicts(self):
        event = self._event_with_vars(
            {
                "code": AUTH_CODE,
                "token_data": {"access_token": ACCESS_TOKEN},
                "token_info": {"refresh_token": REFRESH_TOKEN},
            }
        )

        scrubbed = _strip_pii(event, None)

        frame = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert frame["vars"]["code"] == "[Filtered]"
        assert frame["vars"]["token_data"] == "[Filtered]"
        assert frame["vars"]["token_info"] == "[Filtered]"
        assert AUTH_CODE not in json.dumps(scrubbed)

    def test_keeps_diagnostic_keys_that_merely_contain_code(self):
        """Over-redaction defeats the point of the error reporter."""
        event = self._event_with_vars({"status_code": 500, "error_code": "E42"})

        frame = _strip_pii(event, None)["exception"]["values"][0]["stacktrace"][
            "frames"
        ][0]

        assert frame["vars"]["status_code"] == 500
        assert frame["vars"]["error_code"] == "E42"

    def test_walks_threads_and_top_level_stacktrace(self):
        event = {
            "stacktrace": {"frames": [{"vars": {"code": AUTH_CODE}}]},
            "threads": {
                "values": [{"stacktrace": {"frames": [{"vars": {"code": AUTH_CODE}}]}}]
            },
        }

        scrubbed = _strip_pii(event, None)

        assert AUTH_CODE not in json.dumps(scrubbed)

    def test_tolerates_frames_without_vars(self):
        event = {
            "exception": {
                "values": [{"stacktrace": {"frames": [{"filename": "a.py"}]}}]
            }
        }

        assert _strip_pii(event, None) == event


class TestCredentialsRepr:
    """The secret must not be renderable, independent of Sentry."""

    def test_repr_omits_client_secret(self):
        credentials = SpotifyCredentials(
            client_id="public-id",
            client_secret=CLIENT_SECRET,
            redirect_uri="http://localhost:8000/callback",
        )

        rendered = repr(credentials)

        assert CLIENT_SECRET not in rendered
        assert "public-id" in rendered
        assert credentials.client_secret == CLIENT_SECRET
