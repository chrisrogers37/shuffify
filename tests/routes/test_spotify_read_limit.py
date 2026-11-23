"""Account-wide read limits through actual Flask views, caches and HTTP dispatch."""

import json
import os
import shutil
import socket
import subprocess
import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import redis
import requests

from shuffify.models.db import JobExecution, PlaylistPair, User, db
from shuffify.services.operations.restore_review import read_metadata
from shuffify.spotify import SpotifyAPI, SpotifyCache, TokenInfo
from shuffify.spotify.http_client import BASE_URL
from tests.services.operations.test_crash_recovery import World, uri
from tests.spotify.test_private_playlist_cache import MemoryRedis

CAPACITY = 200
SHARED_IP = {"REMOTE_ADDR": "192.0.2.10"}
OTHER_IP = {"REMOTE_ADDR": "198.51.100.20"}
SEARCH = "/api/workshop/search"
SETUP = "/api/playlist/target/setup/context"
TRACK_URI = "spotify:track:" + "a" * 22


class FakeSpotify:
    """Respond at the socket adapter boundary, including to follow-up pages and retries."""

    def __init__(self):
        self.calls = []
        self.pages = 1
        self.target_count = 0
        self.fail_search_once = False

    def send(self, request):
        url = urlsplit(request.url)
        path = url.path.removeprefix("/v1")
        self.calls.append(path)
        assert request.method == "GET"
        status = 200
        if path == "/me/playlists":
            page = int(parse_qs(url.query).get("page", ["1"])[0])
            more = f"{BASE_URL}/me/playlists?page={page + 1}" if page < self.pages else None
            body = {"items": [], "next": more}
        elif path == "/me":
            body = {"id": "alice"}
        elif path == "/playlists/source":
            status, body = 404, {}
        elif path == "/embed/playlist/source":
            body = '<meta property="og:title" content="Source">'
        elif path == "/search":
            status = 503 if self.fail_search_once else 200
            self.fail_search_once = False
            body = {"tracks": {"items": []}, "playlists": {"items": []}}
        elif path in ("/playlists/target", "/playlists/archive"):
            body = {"id": path.split("/")[-1], "name": "Target", "owner": {"id": "alice"}, "public": True,
                    "snapshot_id": "v1", "tracks": {"total": 1 if path.endswith("/archive") else self.target_count}}
        elif path in ("/playlists/target/items", "/playlists/archive/items"):
            total = self.target_count if path == "/playlists/target/items" else 1
            query = parse_qs(url.query)
            offset = int(query.get("offset", ["0"])[0])
            end = min(total, offset + int(query.get("limit", ["50"])[0]))
            items = [{"track": {"uri": TRACK_URI}} for _ in range(offset, end)]
            body = {"items": items, "total": total, "offset": offset,
                    "next": f"{BASE_URL}{path}?offset={end}" if end < total else None}
        else:
            raise AssertionError(f"Unexpected Spotify read: {path}")
        response = requests.Response()
        response.status_code = status
        response._content = (body if isinstance(body, str) else json.dumps(body)).encode()
        response.url = request.url
        return response


@pytest.fixture
def fake_transport(monkeypatch):
    fake = FakeSpotify()
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", lambda self, request, **kw: fake.send(request))
    return fake


@pytest.fixture
def spotify(monkeypatch, limiter_app, fake_transport):
    cache = SpotifyCache(MemoryRedis())
    limiter_app.config.update(SPOTIFY_CLIENT_ID="fixture", SPOTIFY_CLIENT_SECRET="fixture")
    monkeypatch.setattr("shuffify.get_spotify_cache", lambda: cache)
    return fake_transport


def cold_searches(client, count):
    for index in range(count):
        assert client.post(SEARCH, json={"query": f"cold-{index}"}).status_code == 200


def assert_limited(response):
    assert response.status_code == 429, (response.request.path, response.get_data(as_text=True))
    assert response.is_json
    if response.request.method != "HEAD":
        assert response.json["success"] is False
        code = response.json.get("error_code") or response.json["operation"]["error_code"]
        assert code == "rate_limited"
    assert 1 <= int(response.headers["Retry-After"]) <= 61


def test_shared_account_capacity_cache_and_reset(spotify, signed_in_limiter_client, sample_token, monkeypatch):
    now = Mock(return_value=int(time.time()))
    monkeypatch.setattr(time, "time", now)
    alice = signed_in_limiter_client("alice")
    second = signed_in_limiter_client("alice")
    bob = signed_in_limiter_client("bob")
    spotify.pages = 2
    assert alice.get("/api/library", environ_base=SHARED_IP).status_code == 200
    for _ in range(99):
        assert second.post(SEARCH, json={"query": "cold"}, environ_base=OTHER_IP).status_code == 200
        assert alice.head(SETUP, environ_base=SHARED_IP).status_code == 200
    assert second.post(SEARCH, json={"query": "last unit"}, environ_base=OTHER_IP).status_code == 200
    admitted = ["/me/playlists"] * 2 + ["/search", "/playlists/target"] * 99 + ["/search"]

    now.return_value += 30
    blocked = second.post(SEARCH, json={"query": "one over"}, environ_base=OTHER_IP)
    assert_limited(blocked)
    assert 30 <= int(blocked.headers["Retry-After"]) <= 31
    assert_limited(alice.head(SETUP))
    for path, body in (
        ("/api/playlist/target/inspect-live", {"kind": "privacy", "desired_public": True}),
        ("/api/playlist/target/live-context", {"kind": "privacy", "public": False}),
        ("/api/playlist/target/routines/review", {"definition": {}, "request_key": "review"}),
        ("/api/workshop/load-external-playlist", {"query": "cold"}),
    ):
        refusal = second.post(path, json=body)
        assert_limited(refusal)
        if path.endswith("/inspect-live"):
            assert refusal.json["inspection_only"] and refusal.json["operation"]["aggregate_status"] == "unknown"
    assert_limited(second.get("/api/library?refresh=1"))
    assert second.get("/api/user-playlists").status_code == 200  # Warm, including an empty list
    assert second.get("/api/activity").status_code == 200
    with alice.application.app_context():
        from shuffify import get_spotify_cache

        get_spotify_cache().invalidate_user_playlists("alice")
    assert_limited(second.get("/api/user-playlists"))  # Same ordinary GET, now cold
    assert spotify.calls == admitted

    # Worker and recovery clients can still read with an app context alone.
    with alice.application.app_context():
        user = User.query.filter_by(spotify_id="alice").one()
        worker = SpotifyAPI(TokenInfo.from_dict(sample_token))
        assert read_metadata(worker, user, "target")["metadata"]["public"] is True
    assert bob.post(SEARCH, json={"query": "cold"}, environ_base=SHARED_IP).status_code == 200
    now.return_value += 31
    assert second.head(SETUP, environ_base=OTHER_IP).status_code == 200
    assert spotify.calls == admitted + ["/playlists/target"] * 2 + ["/search", "/playlists/target"]


@pytest.mark.parametrize("last_read", ["page", "retry"])
def test_last_unit_covers_pagination_or_retry(spotify, signed_in_limiter_client, monkeypatch, last_read):
    alice = signed_in_limiter_client("alice")
    cold_searches(alice, CAPACITY - 1)
    if last_read == "page":
        spotify.pages = 2
        response = alice.get("/api/library")
        expected = "/me/playlists"
    else:
        spotify.fail_search_once = True
        monkeypatch.setattr("shuffify.spotify.http_client.time.sleep", lambda _: None)
        response = alice.post(SEARCH, json={"query": "retry"})
        expected = "/search"
    assert response.status_code == 200
    assert_limited(alice.post(SEARCH, json={"query": "one over"}))
    assert spotify.calls == ["/search"] * (CAPACITY - 1) + [expected] * 2


def test_forced_refresh_get_head_and_read_budget(spotify, signed_in_limiter_client):
    alice = signed_in_limiter_client("alice")
    cold_searches(alice, CAPACITY - 11)
    for _ in range(5):
        assert alice.get("/api/library?refresh=1").status_code == 200
        assert alice.head("/api/user-playlists?refresh=1").status_code == 200
    forced = alice.get("/api/user-playlists?refresh=1")
    assert_limited(forced)
    assert "refresh=1" in forced.json["message"]
    assert_limited(alice.head("/api/library?refresh=1"))
    assert alice.get("/api/library").status_code == 200
    assert alice.post(SEARCH, json={"query": "last unit"}).status_code == 200
    assert_limited(alice.post(SEARCH, json={"query": "one over"}))
    assert spotify.calls == ["/search"] * (CAPACITY - 11) + ["/me/playlists"] * 10 + ["/search"]


def test_embed_fallback_requires_its_own_unit(spotify, signed_in_limiter_client):
    alice = signed_in_limiter_client("alice")
    context = alice.get(SETUP).json["context"]
    cold_searches(alice, CAPACITY - 3)
    impact = alice.post("/api/playlist/target/setup/impact", json={
        "configuration_revision": context["configuration_revision"],
        "changes": [{"op": "add_playlist_source", "playlist_id": "source"}],
    })
    assert_limited(impact)
    assert spotify.calls == ["/playlists/target"] + ["/search"] * (CAPACITY - 3) + [
        "/playlists/target", "/playlists/source",
    ]


@pytest.fixture
def private_redis_url(tmp_path):
    if port := os.environ.get("SHUFFIFY_TEST_REDIS_PORT"):
        yield f"redis://127.0.0.1:{int(port)}/0"
        return
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("Two-app storage test requires redis-server on PATH")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    process = subprocess.Popen(
        [binary, "--bind", "127.0.0.1", "--port", str(port), "--save", "", "--appendonly", "no",
         "--dir", str(tmp_path)],
        stdout=subprocess.DEVNULL,
    )
    url = f"redis://127.0.0.1:{port}/0"
    try:
        with redis.Redis.from_url(url, socket_timeout=1) as store:
            for _ in range(100):
                assert process.poll() is None, "Disposable Redis failed to start"
                try:
                    assert store.info("server")["process_id"] == process.pid
                    break
                except redis.ConnectionError:
                    time.sleep(0.05)
            else:
                pytest.fail("Disposable Redis did not become ready")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.integration
def test_two_app_instances_share_capacity(private_redis_url, fake_transport, sample_token, tmp_path, monkeypatch):
    from config import TestConfig
    from shuffify import create_app
    from shuffify.services import UserService

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TestConfig, "REDIS_URL", private_redis_url)
    monkeypatch.setattr(TestConfig, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{tmp_path}/accounts.db")
    for name in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
        monkeypatch.setenv(name, "fixture")
        monkeypatch.setattr(TestConfig, name, "fixture")
    profile = {"id": "alice", "display_name": "Alice"}
    first, second = (create_app("testing").test_client() for _ in range(2))
    with first.application.app_context():
        UserService.upsert_from_spotify(profile)
    for client in (first, second):
        with client.session_transaction() as session:
            session.update(user_data=profile, spotify_token=sample_token)
    cold_searches(first, CAPACITY - 2)
    assert first.get("/api/user-playlists", environ_base=SHARED_IP).status_code == 200
    assert second.post(SEARCH, json={"query": "last unit"}, environ_base=OTHER_IP).status_code == 200
    for client in (first, second):
        assert_limited(client.post(SEARCH, json={"query": "over limit"}))
        assert client.get("/api/library").status_code == 200
    assert fake_transport.calls == ["/search"] * (CAPACITY - 2) + ["/me/playlists", "/search"]


def test_inspect_only_finalizer_preserves_unverified_outcome(spotify, signed_in_limiter_client):
    alice = signed_in_limiter_client("alice")
    cold_searches(alice, CAPACITY)
    with alice.application.app_context():
        user = User.query.filter_by(spotify_id="alice").one()
        db.session.add(PlaylistPair(user_id=user.id, production_playlist_id="target", archive_playlist_id="archive"))
        db.session.commit()
    inspection = alice.post("/api/playlist/target/pair/archive", json={
        "inspect_only": True, "source_playlist_id": "archive", "target_track_uris": [],
        "track_uris": [TRACK_URI], "baseline_track_uris": [],
    })
    assert_limited(inspection)
    assert inspection.json["aggregate_status"] == "unknown"
    assert inspection.json["archived_count"] is None
    assert spotify.calls == ["/search"] * CAPACITY


def test_multi_playlist_review_stops_before_archive(spotify, signed_in_limiter_client):
    alice = signed_in_limiter_client("alice")
    with alice.application.app_context():
        user = User.query.filter_by(spotify_id="alice").one()
        db.session.add(PlaylistPair(user_id=user.id, production_playlist_id="target", archive_playlist_id="archive"))
        db.session.commit()
    cold_searches(alice, CAPACITY - 2)
    assert_limited(alice.get("/api/workshop/target/source-context?raid=0&archive=1"))
    assert spotify.calls == ["/search"] * (CAPACITY - 2) + [
        "/playlists/target", "/playlists/target/items", "/playlists/target",
    ]


def test_full_playlist_restore_review_uses_two_units(spotify, signed_in_limiter_client):
    alice = signed_in_limiter_client("alice")
    with alice.application.app_context():
        user = User.query.filter_by(spotify_id="alice").one()
        journal = World([uri(1)]).journal()
        journal.begin_step("original")
        journal.freeze_replace("target", [uri(9)])
        journal.resume()
        journal._attention("external_edit")
        parent = JobExecution(user_id=user.id, kind="shuffle", targets=["target"], state="needs_attention",
                              status="failed", journal=journal.doc)
        db.session.add(parent)
        db.session.commit()
        operation_id = parent.id
    spotify.target_count = 10_000
    response = alice.get(f"/api/operations/{operation_id}/restore-context")
    assert response.status_code == 200, response.json
    assert response.json["context"]["resources"][0]["current_count"] == 10_000
    observation = ["/playlists/target"] + ["/playlists/target/items"] * 200 + ["/playlists/target"]
    assert spotify.calls == observation * 2 + ["/playlists/target"]
    cold_searches(alice, CAPACITY - 2)
    assert_limited(alice.post(SEARCH, json={"query": "one over"}))
    assert len(spotify.calls) == 405 + CAPACITY - 2
