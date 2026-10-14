"""The unit suite's network guard (tests/network_guard.py): loopback only.

The hosts here are reserved ones (``.invalid``, TEST-NET-3), so a broken guard
cannot make these tests reach anything real.
"""

import os
import socket

import pytest
import requests

from shuffify.spotify import http_client
from tests.network_guard import PROXY_VARIABLES, LeftThisMachine, on_this_machine


def test_a_lookup_off_this_machine_is_refused_and_recorded(network_guard):
    with pytest.raises(LeftThisMachine):
        socket.getaddrinfo("api.example.invalid", 443)
    assert network_guard == ["api.example.invalid"]
    network_guard.clear()


def test_a_connection_off_this_machine_is_refused_and_recorded(network_guard):
    with socket.socket() as sock, pytest.raises(LeftThisMachine):
        sock.settimeout(1)
        sock.connect(("203.0.113.7", 443))  # TEST-NET-3: documentation only
    assert network_guard == ["203.0.113.7"]
    network_guard.clear()


def test_a_refused_spotify_call_is_not_retried(network_guard, monkeypatch):
    """Not an OSError, so SpotifyHTTPClient's retry loop does not back off on
    it: a stray call fails at once."""
    slept = []
    monkeypatch.setattr(http_client.time, "sleep", slept.append)
    monkeypatch.setattr(http_client, "BASE_URL", "https://api.example.invalid/v1")
    with pytest.raises(LeftThisMachine):
        http_client.SpotifyHTTPClient("token").get("/me")
    assert slept == []
    assert network_guard == ["api.example.invalid"]
    network_guard.clear()


def test_a_swallowed_refusal_still_fails_the_test(pytester):
    """The teardown check catches a refusal that the code under test swallowed
    in an ``except Exception``."""
    pytester.makepyfile("""
        import socket

        def test_swallows_a_refusal():
            try:
                socket.getaddrinfo("leak.example.invalid", 443)
            except Exception:
                pass
    """)
    result = pytester.runpytest("-p", "tests.network_guard")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*reached off this machine*leak.example.invalid*"])


def test_this_machine_is_allowed(network_guard):
    assert socket.getaddrinfo("127.0.0.1", 9)
    assert socket.getaddrinfo("localhost", 9)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        with socket.socket() as client:
            client.connect(server.getsockname())
    assert network_guard == []


def test_proxies_are_off():
    assert not any(name in os.environ for name in PROXY_VARIABLES)
    # Including the system proxy settings requests falls back to on macOS.
    assert requests.utils.get_environ_proxies("https://api.example.invalid/") == {}


@pytest.mark.parametrize("host, local", [
    ("localhost", True), ("127.0.0.1", True), ("127.8.9.10", True), ("::1", True),
    (None, True), ("", True), ("0.0.0.0", True), ("::", True),
    ("api.example.invalid", False), ("127.attacker.example", False), ("10.0.0.1", False),
])
def test_what_counts_as_this_machine(host, local):
    assert on_this_machine(host) is local
