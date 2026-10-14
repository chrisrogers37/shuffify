"""The harness network guard lets processes reach this machine and nothing else.

harness/netguard/sitecustomize.py makes sure a test run never reaches real
Spotify. Each test starts a fresh interpreter the
way scripts/harness.sh does -- guard directory on PYTHONPATH, SHUFFIFY_NETGUARD=1
-- because a sitecustomize only takes effect at interpreter start, and
installing it into the pytest process would leave sockets patched for every
test after it.
"""

import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_DIR = REPO_ROOT / "harness" / "netguard"


@pytest.fixture
def listener():
    """A listening loopback TCP port. The kernel completes the handshake from
    the backlog, so a connect succeeds without anything calling accept()."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    yield server.getsockname()[1]
    server.close()


def run_guarded(code, tmp_path, allow_ports=(), extra_env=None, guard="1"):
    env = {
        "PATH": os.environ.get("PATH", ""),
        # As harness processes run: config.py skips load_dotenv() for it.
        "APP_CONFIG": "harness",
        "PYTHONPATH": str(GUARD_DIR),
        "SHUFFIFY_NETGUARD": guard,
        "SHUFFIFY_NETGUARD_ALLOW_PORTS": ",".join(str(p) for p in allow_ports),
        "SHUFFIFY_NETGUARD_LOG": str(tmp_path / "violations.log"),
    }
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def violations(tmp_path):
    log = tmp_path / "violations.log"
    return log.read_text().splitlines() if log.exists() else []


def test_guard_is_active_only_when_enabled(tmp_path):
    probe = "import socket; print(getattr(socket, 'SHUFFIFY_NETGUARD_ACTIVE', False))"
    assert run_guarded(probe, tmp_path).stdout.strip() == "True"
    assert run_guarded(probe, tmp_path, guard="0").stdout.strip() == "False"


def test_misconfigured_guard_refuses_to_start(tmp_path):
    """Python's site module skips a failing sitecustomize; the guard must not be skippable."""
    result = run_guarded("print('ran unguarded')", tmp_path, extra_env={"SHUFFIFY_NETGUARD_ALLOW_PORTS": "8900x"})
    assert result.returncode != 0
    assert "ran unguarded" not in result.stdout
    assert "refusing to start without the network guard" in result.stderr


def test_allowed_loopback_port_connects(tmp_path, listener):
    result = run_guarded(
        f"import socket; socket.create_connection(('127.0.0.1', {listener}), timeout=5).close(); print('ok')",
        tmp_path,
        allow_ports=[listener],
    )
    assert result.stdout.strip() == "ok", result.stderr
    assert violations(tmp_path) == []


def test_unlisted_loopback_port_is_refused(tmp_path, listener):
    """Loopback alone is not a pass: a local HTTP proxy may also listen on 127.0.0.1."""
    result = run_guarded(
        f"""
        import socket
        try:
            socket.create_connection(('127.0.0.1', {listener}), timeout=5)
        except Exception as e:
            print(type(e).__name__)
        """,
        tmp_path,
    )
    assert result.stdout.strip() == "NetguardViolation"
    assert len(violations(tmp_path)) == 1


@pytest.mark.parametrize("resolver", ["getaddrinfo('api.spotify.com', 443)", "gethostbyname_ex('api.spotify.com')"])
def test_resolving_spotify_is_refused(tmp_path, resolver):
    result = run_guarded(
        f"""
        import socket
        try:
            socket.{resolver}
        except Exception as e:
            print(type(e).__name__)
        print(len(socket.getaddrinfo('localhost', 80)) > 0)
        """,
        tmp_path,
    )
    assert result.stdout.split() == ["NetguardViolation", "True"], result.stderr
    assert "api.spotify.com" in violations(tmp_path)[0]


def test_request_through_loopback_proxy_is_refused(tmp_path, listener):
    """With HTTPS_PROXY on loopback, requests connects to the proxy port -- blocked."""
    result = run_guarded(
        """
        import requests
        try:
            requests.get('https://api.spotify.com/v1/me', timeout=5)
        except Exception as e:
            print(type(e).__name__)
        """,
        tmp_path,
        extra_env={"HTTPS_PROXY": f"http://127.0.0.1:{listener}", "NO_PROXY": ""},
    )
    assert result.stdout.strip() == "NetguardViolation", result.stderr
    assert f"127.0.0.1:{listener}" in violations(tmp_path)[0]


def test_spotify_client_fails_fast_without_retrying(tmp_path):
    """The violation is not an OSError, so SpotifyHTTPClient does not back off and retry.

    The proxy points at a closed local port so that, even with the guard
    broken, this test can never reach api.spotify.com.
    """
    result = run_guarded(
        """
        import time
        from shuffify.spotify.http_client import SpotifyHTTPClient
        start = time.monotonic()
        try:
            SpotifyHTTPClient('not-a-real-token').get('/me')
        except Exception as e:
            print(type(e).__name__, time.monotonic() - start < 5)
        """,
        tmp_path,
        extra_env={"HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""},
    )
    assert result.stdout.split() == ["NetguardViolation", "True"], result.stderr
    assert len(violations(tmp_path)) == 1


@pytest.mark.parametrize("send", ['s.sendto(b"x", ("192.0.2.1", 53))', 's.sendmsg([b"x"], [], 0, ("192.0.2.1", 53))'])
def test_udp_to_a_remote_host_is_refused(tmp_path, send):
    """Unconnected datagrams name their destination per call, outside connect()."""
    result = run_guarded(
        f"""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            {send}
        except Exception as e:
            print(type(e).__name__)
        """,
        tmp_path,
    )
    assert result.stdout.strip() == "NetguardViolation", result.stderr
    assert "192.0.2.1:53" in violations(tmp_path)[0]


def test_unix_sockets_are_allowed(tmp_path):
    path = tmp_path / "s.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    try:
        result = run_guarded(
            f"import socket; s = socket.socket(socket.AF_UNIX); s.connect({str(path)!r}); print('ok')",
            tmp_path,
        )
    finally:
        server.close()
    assert result.stdout.strip() == "ok", result.stderr
