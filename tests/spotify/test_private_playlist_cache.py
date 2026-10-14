"""Account isolation through the real API/HTTP client and synthetic Redis."""

import copy
import fnmatch
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from flask import Flask

from shuffify.error_handlers import register_error_handlers
from shuffify.routes import main
from shuffify.spotify import (
    SpotifyAPI,
    SpotifyAPIError,
    SpotifyCache,
    SpotifyNotFoundError,
    TokenInfo,
)


class MemoryRedis:
    """The Redis commands exercised here, with normal per-entry expiration."""

    def __init__(self):
        self.data = {}
        self.expires = {}
        self.reads = []

    def get(self, key):
        self.reads.append(key)
        if self.expires.get(key, float("inf")) <= time.time():
            self.delete(key)
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value
        self.expires[key] = time.time() + ttl

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)
            self.expires.pop(key, None)

    def scan(self, cursor, match, count):
        assert count == 100
        return 0, [key for key in self.data if fnmatch.fnmatchcase(key, match)]


class PrivatePlaylistFixture:
    """A private, two-page Spotify playlist; account B initially has no access."""

    playlist_id = "private-playlist"

    def __init__(self):
        self.store = MemoryRedis()
        self.cache = SpotifyCache(self.store)
        self.allowed = {"account-A"}
        self.calls = []
        self.name = "Owner private playlist"
        self.uris = ["spotify:track:first", "spotify:track:second"]

    def api(self, account):
        return SpotifyAPI(
            TokenInfo(f"dummy-token-{account}", "Bearer", time.time() + 3600),
            cache=self.cache,
        )

    def request(self, session, method, url, **kwargs):
        account = session.headers["Authorization"].removeprefix("Bearer dummy-token-")
        path = urlsplit(url).path.removeprefix("/v1")
        self.calls.append((account, method, path, urlsplit(url).query))
        status = 200
        if path == "/me":
            body = {"id": account}
        elif account not in self.allowed:
            status, body = 404, {"error": {"message": "Private playlist denied"}}
        elif method == "PUT":
            payload = kwargs["json"]
            if path.endswith("/items"):
                self.uris = payload["uris"]
            else:
                self.name = payload["name"]
            body = {"snapshot_id": "changed"}
        elif method == "POST":
            self.uris.extend(kwargs["json"]["uris"])
            body = {"snapshot_id": "changed"}
        elif method == "DELETE":
            removed = {item["uri"] for item in kwargs["json"]["items"]}
            self.uris = [uri for uri in self.uris if uri not in removed]
            body = {"snapshot_id": "changed"}
        elif path.endswith("/items"):
            offset = int(parse_qs(urlsplit(url).query).get("offset", [0])[0])
            uri = self.uris[offset : offset + 1]
            body = {
                "items": [
                    {
                        "item": {"id": item.split(":")[-1], "uri": item, "name": "Private selection", "artists": []},
                        "added_at": "2026-01-01T00:00:00Z",
                    }
                    for item in uri
                ],
                "next": (
                    f"https://api.spotify.com/v1/playlists/{self.playlist_id}/items?offset={offset + 1}"
                    if offset + 1 < len(self.uris)
                    else None
                ),
            }
        else:
            body = {
                "id": self.playlist_id,
                "name": self.name,
                "owner": {"id": "account-A"},
                "public": False,
                "tracks": {"total": len(self.uris)},
            }
        response = Mock(status_code=status, ok=status < 400)
        response.json.return_value = copy.deepcopy(body)
        return response


@pytest.fixture
def private_playlist(monkeypatch):
    fixture = PrivatePlaylistFixture()

    def request(session, method, url, **kwargs):
        return fixture.request(session, method, url, **kwargs)

    # No real HTTP request or Spotify credential is used by any of these tests.
    monkeypatch.setattr(requests.Session, "request", request)
    return fixture


@pytest.mark.parametrize("operation", ["get_playlist", "get_playlist_tracks"])
def test_other_account_cannot_read_owner_cache(private_playlist, operation):
    fixture = private_playlist
    owner = fixture.api("account-A")
    other = fixture.api("account-B")
    getattr(owner, operation)(fixture.playlist_id)

    with pytest.raises(SpotifyNotFoundError):
        getattr(other, operation)(fixture.playlist_id)

    assert ("account-B", "GET", "/me", "") in fixture.calls
    assert any(call[0] == "account-B" and "/playlists/" in call[2] for call in fixture.calls)
    assert all("dummy-token" not in key for key in fixture.store.data)


def test_private_playlist_route_honors_upstream_denial(private_playlist, monkeypatch):
    fixture = private_playlist
    owner = fixture.api("account-A")
    owner.get_playlist(fixture.playlist_id)
    owner.get_playlist_tracks(fixture.playlist_id)
    other = fixture.api("account-B")
    app = Flask("private-cache-regression")
    app.config.update(TESTING=True, SECRET_KEY="dummy")
    app.register_blueprint(main)
    register_error_handlers(app)
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: other)
    monkeypatch.setattr("shuffify.is_db_available", lambda: True)
    monkeypatch.setattr("shuffify.routes.get_db_user", lambda: SimpleNamespace(id=2))

    response = app.test_client().get(f"/playlist/{fixture.playlist_id}")

    assert response.status_code == 404
    assert "Owner private playlist" not in response.get_data(as_text=True)
    assert "Private selection" not in response.get_data(as_text=True)


def test_same_account_reuses_scoped_paginated_tracks(private_playlist):
    fixture = private_playlist
    first = fixture.api("account-A")
    metadata = first.get_playlist(fixture.playlist_id)
    tracks = first.get_playlist_tracks(fixture.playlist_id)
    before = len(fixture.calls)
    second = fixture.api("account-A")

    assert second.get_playlist(fixture.playlist_id) == metadata
    assert second.get_playlist_tracks(fixture.playlist_id) == tracks
    assert [track["uri"] for track in tracks] == fixture.uris
    assert all(track["added_at"] == "2026-01-01T00:00:00Z" for track in tracks)
    assert fixture.calls[before:] == [("account-A", "GET", "/me", "")]


@pytest.mark.parametrize(
    "operation,namespace",
    [
        ("get_playlist", "playlist"),
        ("get_playlist_tracks", "tracks"),
    ],
)
def test_legacy_unscoped_entry_is_never_read(private_playlist, operation, namespace):
    fixture = private_playlist
    legacy_key = f"shuffify:cache:{namespace}:{fixture.playlist_id}"
    fixture.store.setex(legacy_key, 60, json.dumps({"secret": "legacy"}).encode())

    with pytest.raises(SpotifyNotFoundError):
        getattr(fixture.api("account-B"), operation)(fixture.playlist_id)

    assert legacy_key not in fixture.store.reads


@pytest.mark.parametrize("operation", ["get_playlist", "get_playlist_tracks"])
def test_skip_cache_fetches_fresh_data(private_playlist, operation):
    fixture = private_playlist
    api = fixture.api("account-A")
    getattr(api, operation)(fixture.playlist_id)
    reads = len(fixture.store.reads)
    fixture.allowed.clear()

    with pytest.raises(SpotifyNotFoundError):
        getattr(api, operation)(fixture.playlist_id, skip_cache=True)

    assert len(fixture.store.reads) == reads


@pytest.mark.parametrize("operation", ["get_playlist", "get_playlist_tracks"])
def test_identity_failure_does_not_use_any_cache(private_playlist, monkeypatch, operation):
    fixture = private_playlist
    getattr(fixture.api("account-A"), operation)(fixture.playlist_id)
    other = fixture.api("account-B")
    monkeypatch.setattr(other._http, "get", Mock(side_effect=SpotifyNotFoundError("No identity")))
    reads = len(fixture.store.reads)

    with pytest.raises(SpotifyNotFoundError):
        getattr(other, operation)(fixture.playlist_id)

    assert len(fixture.store.reads) == reads


@pytest.mark.parametrize("identity", [None, "", 123])
def test_invalid_identity_cannot_form_a_cache_scope(private_playlist, monkeypatch, identity):
    fixture = private_playlist
    api = fixture.api("account-B")
    monkeypatch.setattr(api, "get_current_user", lambda: {"id": identity})

    with pytest.raises(SpotifyAPIError, match="authenticated account ID"):
        api.get_playlist(fixture.playlist_id)

    assert fixture.store.reads == []
    assert fixture.calls == []


@pytest.mark.parametrize("operation", ["get_playlist", "get_playlist_tracks"])
def test_cache_error_still_obeys_upstream_access(private_playlist, monkeypatch, operation):
    import redis

    fixture = private_playlist
    getattr(fixture.api("account-A"), operation)(fixture.playlist_id)
    monkeypatch.setattr(fixture.store, "get", Mock(side_effect=redis.RedisError("Unavailable")))

    with pytest.raises(SpotifyNotFoundError):
        getattr(fixture.api("account-B"), operation)(fixture.playlist_id)


def test_expired_scoped_entry_fetches_upstream(private_playlist):
    fixture = private_playlist
    api = fixture.api("account-A")
    api.get_playlist(fixture.playlist_id)
    for key in fixture.store.data:
        fixture.store.expires[key] = 0
    before = len(fixture.calls)
    fixture.name = "Fresh after expiry"

    assert api.get_playlist(fixture.playlist_id)["name"] == fixture.name
    assert len(fixture.calls) == before + 1


def test_invalidation_preserves_other_playlists_and_cache_types(private_playlist):
    cache = private_playlist.cache
    cache.set_playlist("another", {"id": "another"}, user_id="account-A")
    cache.set_playlist_tracks("another", [{"uri": "another"}], user_id="account-B")
    cache.set_user("account-A", {"id": "account-A"})
    cache.set_playlist("changed", {"id": "changed"}, user_id="account-A")
    cache.set_playlist("changed", {"id": "changed"}, user_id="account-B")

    assert cache.invalidate_playlist("changed") is True

    assert cache.get_playlist("changed", user_id="account-A") is None
    assert cache.get_playlist("changed", user_id="account-B") is None
    assert cache.get_playlist("another", user_id="account-A") == {"id": "another"}
    assert cache.get_playlist_tracks("another", user_id="account-B") == [{"uri": "another"}]
    assert cache.get_user("account-A") == {"id": "account-A"}


@pytest.mark.parametrize("mutation", ["replace", "clear", "add", "remove", "details"])
def test_mutation_invalidates_every_account_copy(private_playlist, mutation):
    fixture = private_playlist
    fixture.allowed.add("account-B")
    owner, collaborator = fixture.api("account-A"), fixture.api("account-B")
    for api in (owner, collaborator):
        api.get_playlist(fixture.playlist_id)
        api.get_playlist_tracks(fixture.playlist_id)
    if mutation == "replace":
        owner.update_playlist_tracks(fixture.playlist_id, ["spotify:track:new"])
    elif mutation == "clear":
        owner.update_playlist_tracks(fixture.playlist_id, [])
    elif mutation == "add":
        owner.playlist_add_items(fixture.playlist_id, ["spotify:track:new"])
    elif mutation == "remove":
        owner.playlist_remove_items(fixture.playlist_id, ["spotify:track:first"])
    else:
        owner.update_playlist_details(fixture.playlist_id, name="Changed")
    before = len(fixture.calls)

    assert collaborator.get_playlist(fixture.playlist_id)["name"] == fixture.name
    assert [track["uri"] for track in collaborator.get_playlist_tracks(fixture.playlist_id)] == fixture.uris
    assert any(call[2] == f"/playlists/{fixture.playlist_id}" for call in fixture.calls[before:])
