"""Behaviour tests: the running harness app, driven over HTTP, against the fake Spotify.

``scripts/harness.sh e2e`` runs these as a harness process (empty environment,
network guard on, only the harness's own ports reachable), with
SHUFFIFY_E2E_APP_URL and SHUFFIFY_E2E_FAKE_URL naming the harness app and
fake. A plain ``pytest tests/`` has neither, so nothing here is collected.

Each test logs in through the fake as a seeded user, acts through the app's
own routes as the browser would, and checks Spotify's side of the outcome in
the fake's own state (``/_fake/playlists/{id}``), not in what the app reports.
"""

import os
import re
import socket
import time
from contextlib import contextmanager
from urllib.parse import urljoin

import pytest
import redis
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from config import HarnessConfig
from harness.fake_spotify.store import spotify_id

APP = os.environ.get("SHUFFIFY_E2E_APP_URL", "")
FAKE = os.environ.get("SHUFFIFY_E2E_FAKE_URL", "")

if not (APP and FAKE):
    collect_ignore_glob = ["test_*.py"]


def pid(key: str) -> str:
    """A seeded playlist's Spotify ID (harness/fake_spotify/seed.json)."""
    return spotify_id("playlist", key)


def greets(html: str, display_name: str) -> bool:
    """The dashboard greets the user by name: "Welcome back, ..." or
    "Welcome to Shuffify, ...", whichever it decides they are."""
    return re.search(rf"Welcome[^<]*{re.escape(display_name)}", html) is not None


class Fake:
    """The fake Spotify's control routes: its state, as the tests check it."""

    def _get(self, path: str):
        resp = requests.get(f"{FAKE}/_fake/{path}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def playlist(self, playlist_id: str) -> dict:
        return self._get(f"playlists/{playlist_id}")

    def playlists(self) -> list:
        return self._get("playlists")

    def uris(self, playlist_id: str) -> list:
        return self.playlist(playlist_id)["uris"]

    def display_name(self, user: str) -> str:
        return next(u["display_name"] for u in self._get("users") if u["id"] == user)

    def violations(self) -> list:
        return self._get("violations")

    def arm(self, **spec) -> None:
        """Arm a fault (harness/fake_spotify/faults.py); the reset clears it."""
        resp = requests.post(f"{FAKE}/_fake/faults", json=spec, timeout=10)
        assert resp.status_code == 201, resp.text

    def release(self) -> None:
        """End every armed slow fault's hold, now and for its later firings (faults.py); the reset clears them."""
        resp = requests.post(f"{FAKE}/_fake/faults/release", timeout=10)
        assert resp.status_code == 200, resp.text

    def faults(self) -> list:
        return self._get("faults")


def reset_harness() -> None:
    """Back to the seed: the app's tables emptied (Alembic's row aside), its
    Spotify cache and rate-limit counters in Redis cleared, then the fake.

    Only in a process the harness network guard is loaded into (as
    scripts/harness.sh e2e starts it), and only through the URLs
    HarnessConfig.assert_safe() has just checked; the database's is handed to
    libpq exactly as that check read it.
    """
    if not getattr(socket, "SHUFFIFY_NETGUARD_ACTIVE", False):
        raise RuntimeError("the harness is reset only with the network guard loaded: run scripts/harness.sh e2e")
    HarnessConfig.assert_safe()

    engine = create_engine(HarnessConfig.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            tables = conn.execute(text(
                "SELECT string_agg(format('%I.%I', schemaname, tablename), ', ') FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )).scalar_one()
            assert tables, "the harness database has no tables to reset: has setup run its migrations?"
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()

    # Every test request comes from 127.0.0.1, so a fast suite would trip
    # per-minute limits meant for one person; the limiter itself stays on.
    cache = redis.Redis.from_url(HarnessConfig.REDIS_URL, socket_timeout=10)
    keys = [key for pattern in ("shuffify:cache:*", "LIMITS:LIMITER/*") for key in cache.scan_iter(pattern)]
    if keys:
        cache.delete(*keys)

    requests.post(f"{FAKE}/_fake/reset", timeout=10).raise_for_status()


class App:
    """A browser session with the harness app, logged in through the fake."""

    def __init__(self, user: str, display_name: str):
        self.http = requests.Session()
        self._log_in(user, display_name)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        """A request to the app (a path) or anywhere the login goes (a URL)."""
        if url.startswith("/"):
            url = f"{APP}{url}"
        return self.http.request(method, url, timeout=120, **kwargs)

    def _log_in(self, user: str, display_name: str) -> None:
        consent = self.request("GET", "/login?legal_consent=true", allow_redirects=False)
        authorize = consent.headers.get("Location", "")
        assert authorize.startswith(f"{FAKE}/authorize?"), f"/login went to {authorize!r}, not the fake"
        callback = self.request("GET", f"{authorize}&fake_user={user}", allow_redirects=False).headers.get("Location")
        assert callback, "the fake's consent did not redirect back to the app"
        landed = self.request("GET", callback, allow_redirects=False)
        home_url = urljoin(callback, landed.headers.get("Location", ""))
        assert home_url == f"{APP}/", f"/callback sent the browser to {home_url!r}, not the dashboard"
        home = self.request("GET", "/").text
        assert greets(home, display_name), "the dashboard does not greet the user who logged in"
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', home)
        assert token, "the logged-in page carries no CSRF token"
        # Flask-WTF reads the token from this header, as the app's own JS sends it.
        self.http.headers["X-CSRFToken"] = token.group(1)

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def ok(self, method: str, path: str, **kwargs) -> dict:
        """A JSON call that must succeed; its body."""
        from tests.paths import api_path

        resp = self.request(method.upper(), api_path(path), **kwargs)
        assert resp.status_code == 200, f"{method.upper()} {path}: {resp.status_code} {resp.text[:500]}"
        body = resp.json()
        assert body.get("success") is not False, f"{method.upper()} {path}: {body}"
        return body

    def wait_operation(self, location: str, operation_id: int, *, timeout=150) -> dict:
        """Poll one accepted receipt through terminal completion without replaying its POST."""
        assert location.endswith(f"/api/operations/{operation_id}"), location
        deadline = time.monotonic() + timeout
        while True:
            response = self.get(location)
            assert response.status_code == 200, f"GET {location}: {response.status_code} {response.text[:500]}"
            body = response.json()
            assert body["success"] is True
            operation = body["operation"]
            assert operation["id"] == operation_id
            if operation["state"] in {"succeeded", "failed", "rolled_back", "needs_attention"}:
                return operation
            assert time.monotonic() < deadline, f"Operation {operation_id} did not finish: {operation}"
            time.sleep(0.05)

    def queued(self, path: str, **kwargs) -> dict:
        """Accept a writer exactly once, then return its authoritative final operation DTO."""
        from tests.paths import api_path

        response = self.post(api_path(path), **kwargs)
        assert response.status_code == 202, f"POST {path}: {response.status_code} {response.text[:500]}"
        body = response.json()
        assert body["success"] is True
        operation_id = body["operation"]["id"]
        assert body["operation_id"] == operation_id
        assert "Location" in response.headers
        return self.wait_operation(response.headers["Location"], operation_id)

    def write(self, path: str, **kwargs) -> dict:
        """Run a successful queued writer and retain the existing rich service result."""
        operation = self.queued(path, **kwargs)
        assert operation["state"] == "succeeded", operation
        result = operation["result"]
        assert isinstance(result, dict) and result.get("success") is not False, operation
        return result


@pytest.fixture
def fake():
    """The fake, with the harness reset to its seed before and after. A test
    fails if the app made any call that departs from Spotify's documentation
    while it ran."""
    f = Fake()
    reset_harness()
    before = len(f.violations())
    yield f
    new = f.violations()[before:]
    try:
        # A test that failed with a write held must not have it answered later, inside the next test's seed.
        f.release()
        reset_harness()
    finally:
        assert new == [], "the app sent Spotify something it does not document"


@pytest.fixture
def alice(fake):
    return App("alice", fake.display_name("alice"))


@contextmanager
def hold_playlist_lock(playlist_id):
    """Hold a playlist's advisory lock on a connection of our own, as a routine run or a Workshop save holds it.

    The key is the one shuffify.services.playlist_lock takes; leaving the block releases it and closes the
    connection."""
    from shuffify.services.playlist_lock import _playlist_lock_key

    HarnessConfig.assert_safe()
    engine = create_engine(HarnessConfig.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    key = _playlist_lock_key(playlist_id)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    finally:
        engine.dispose()
