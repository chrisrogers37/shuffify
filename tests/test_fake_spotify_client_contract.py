"""shuffify's real Spotify client, run against the fake Spotify over a real socket.

tests/test_fake_spotify.py holds the fake to Spotify's documentation. This
holds shuffify to the fake: the production SpotifyAuthManager, SpotifyAPI
(through SpotifyHTTPClient, its retries and its paging) and scraper, unmocked,
pointed at a fake listening on a loopback port. A disagreement between what
the client sends or expects and what Spotify documents fails here: the fake
records anything undocumented the client sends, or any call to an endpoint
it does not serve, and every test fails at teardown unless that record is
empty.

Nothing leaves the machine: every Spotify URL shuffify builds
(tests/spotify_urls.py) is repointed at the fake, and guards fail the test
on a lookup of any other host or a connection to anywhere else.
"""

import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from werkzeug.serving import make_server

from harness.fake_spotify import create_app, spotify_id, tokens
from shuffify.routes.playlist_pairs import PLAYLIST_ITEM_FIELDS
from shuffify.services.base import ensure_private
from shuffify.services.playlist_service import PlaylistAccessError, PlaylistService, scrape_playlist_metadata
from shuffify.services.source_resolver import public_scraper_pathway
from shuffify.services.source_resolver.public_scraper_pathway import PublicScraperPathway
from shuffify.spotify import http_client
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import DEFAULT_SCOPES, SpotifyAuthManager, TokenInfo
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyWriteUnverifiedError,
)
from tests import spotify_urls

REDIRECT = "http://127.0.0.1:8000/callback"
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1"}


def pid(key):
    return spotify_id("playlist", key)


@pytest.fixture(scope="module")
def server():
    app = create_app(redirect_uris=[REDIRECT])
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    app.config["ORIGIN"] = f"http://127.0.0.1:{srv.server_port}"
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield app
    srv.shutdown()


@pytest.fixture
def backoffs():
    """Every wait the client asked for, recorded instead of slept. A backoff
    means the client misread a documented response, so any left at teardown
    fail the test; one that arms a fault takes the backoffs it expects."""
    return []


@pytest.fixture
def store(server, monkeypatch, backoffs):
    """Point every Spotify URL the client uses at the fake, reset it, and
    yield its state."""
    origin, violations = server.config["ORIGIN"], server.config["VIOLATIONS"]
    server.config["STORE"].reset()
    server.config["FAULTS"].clear()
    violations.clear()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    spotify_urls.repoint(monkeypatch, origin)

    # Any lookup or connection but the fake's is a URL this suite failed to
    # repoint. Both are guarded: without a proxy, a stray URL fails at the
    # lookup and never reaches connect().
    strays = []
    fake_address = ("127.0.0.1", int(origin.rsplit(":", 1)[1]))
    real_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and tuple(address[:2]) != fake_address:
            strays.append(address)
            raise ConnectionRefusedError(f"the contract tests reach only the fake, not {address!r}")
        return real_connect(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if host not in LOOPBACK_NAMES:
            strays.append(host)
            raise socket.gaierror(f"the contract tests reach only the fake, not {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    # Recorded rather than raised: callers that catch everything would
    # swallow an exception.
    monkeypatch.setattr(http_client.time, "sleep", backoffs.append)
    monkeypatch.setattr(public_scraper_pathway, "_sleep_with_backoff",
                        lambda attempt, retry_after=None: backoffs.append(attempt))
    yield server.config["STORE"]
    assert strays == [], "the client tried to reach something other than the fake"
    assert backoffs == [], f"the client backed off {backoffs}: it treated a documented response as a failure"
    assert list(violations) == [], "the client sent Spotify something it does not document"


@pytest.fixture
def auth(store):
    return SpotifyAuthManager(SpotifyCredentials(
        client_id="harness-fake-client-id",
        client_secret="harness-fake-client-secret",
        redirect_uri=REDIRECT,
    ))


def log_in(auth, user="alice") -> TokenInfo:
    """The browser's part of the OAuth dance, then the client's own exchange."""
    consent = requests.get(auth.get_auth_url(state="st") + f"&fake_user={user}", allow_redirects=False)
    query = parse_qs(urlsplit(consent.headers["Location"]).query)
    assert query["state"] == ["st"]
    return auth.exchange_code(query["code"][0])


@pytest.fixture
def api(auth):
    return SpotifyAPI(log_in(auth), auth)


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


def test_login_exchange_and_refresh(auth):
    token = log_in(auth)
    assert token.refresh_token and not token.is_expired
    assert set(token.scope.split()) == set(DEFAULT_SCOPES)
    refreshed = auth.refresh_token(token)
    assert refreshed.access_token != token.access_token
    # Spotify may omit a new refresh token; the client keeps the old one.
    assert refreshed.refresh_token == token.refresh_token


def test_revoke_is_unsupported_and_tolerated(auth):
    assert auth.revoke_token(log_in(auth).access_token) is False


def test_401_mid_session_refreshes_and_retries(auth):
    """Spotify expired the token before the client's own clock says so."""
    good = log_in(auth)
    stale = TokenInfo(
        access_token=tokens.mint("access", -1, user="alice", scope=""),
        token_type="Bearer", expires_at=time.time() + 3600, refresh_token=good.refresh_token,
    )
    api = SpotifyAPI(stale, auth)
    assert api.get_current_user()["id"] == "alice"
    assert api.token_info.access_token != stale.access_token


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_current_user(api):
    user = api.get_current_user()
    assert user["id"] == "alice" and user["display_name"] == "Alice Harness"


def test_user_playlists_are_the_editable_ones_across_pages(api, store):
    names = {p["name"] for p in api.get_user_playlists()}
    # Owned and collaborative; a playlist she only follows is not editable.
    assert "Road Trip" in names and "Bob's Finds" not in names
    assert names == {p.name for p in store.playlists.values()
                     if p.owner == "alice" or (p.collaborative and "alice" in p.collaborators)}


def test_playlist_tracks_page_through_250_in_order(api, store):
    tracks = api.get_playlist_tracks(pid("alice-long-haul"))
    assert [t["uri"] for t in tracks] == store.playlists[pid("alice-long-haul")].uris
    assert all(t.get("added_at") for t in tracks)


def test_legacy_shape_playlist_reads_the_same(api, store):
    tracks = api.get_playlist_tracks(pid("alice-old-faithful"))
    assert [t["uri"] for t in tracks] == store.playlists[pid("alice-old-faithful")].uris


def test_get_playlist_and_the_metadata_only_view_of_anothers(api):
    mine = api.get_playlist(pid("alice-morning-mix"))
    theirs = api.get_playlist(pid("carol-late-night"))
    assert mine["items"]["total"] == 40
    assert theirs["name"] == "Late Night" and "items" not in theirs


def test_the_playlist_summary_asks_only_for_what_editability_needs(api):
    """Setup's lean editability read: Spotify's field filter, the same answer for a missing playlist."""
    assert api.get_playlist_summary(pid("alice-morning-mix")) == {
        "id": pid("alice-morning-mix"), "name": "Morning Mix", "owner": {"id": "alice"}, "collaborative": False,
    }
    assert api.get_playlist_summary(pid("carol-late-night"))["owner"] == {"id": "carol"}
    with pytest.raises(SpotifyNotFoundError):
        api.get_playlist_summary(pid("no-such-playlist"))
    service = PlaylistService(api)
    assert service.validate_user_can_edit(pid("alice-morning-mix"), "alice", lean=True)["name"] == "Morning Mix"
    with pytest.raises(PlaylistAccessError):
        service.validate_user_can_edit(pid("carol-late-night"), "alice", lean=True)


def test_items_of_someone_elses_playlist_are_refused(api):
    with pytest.raises(SpotifyForbiddenError, match="403"):
        api.get_playlist_tracks(pid("bob-finds"))


def test_someone_elses_playlist_is_a_summary_without_tracks(api):
    """The Workshop's restricted mode: the tracks are withheld, the summary is not."""
    service = PlaylistService(api)
    with pytest.raises(PlaylistAccessError):
        service.get_playlist(pid("bob-finds"), include_features=False)
    summary = service.get_playlist_summary(pid("bob-finds"))
    assert summary.name == "Bob's Finds"
    assert summary.owner_id == "bob"
    assert summary.tracks == []


def test_archive_view_field_filter(api):
    assert api.get_playlist_items_raw(pid("alice-morning-mix"), fields="total", limit=1) == {"total": 40}
    page = api.get_playlist_items_raw(pid("alice-morning-mix"), fields=PLAYLIST_ITEM_FIELDS, limit=25, offset=15)
    assert len(page["items"]) == 25 and set(page["items"][0]) == {"item"}


def test_get_tracks_one_by_one_skipping_ids_spotify_cannot_find(api, store):
    uris = [t.uri for t in store.catalog[:3]]
    found = api.get_tracks([uris[0], "spotify:track:0000000000000000000000", *uris[1:]])
    assert [t["uri"] for t in found] == uris


def test_audio_features_are_refused_to_a_development_mode_app(api, store):
    with pytest.raises(SpotifyAPIError, match="403"):
        api.get_audio_features([store.catalog[0].id])


def test_search(api, store):
    target = store.catalog[7]
    found = api.search_tracks(f'track:"{target.name}" artist:"{target.artists[0].name}"', limit=10)
    assert target.uri in [t["uri"] for t in found]
    playlists = api.search_playlists("late night", limit=10)
    assert [p["name"] for p in playlists] == ["Late Night"]


# ---------------------------------------------------------------------------
# Writes, checked in the fake's own state
# ---------------------------------------------------------------------------


def test_replace_250_tracks_in_three_batches(api, store):
    playlist = store.playlists[pid("alice-long-haul")]
    reversed_uris = list(reversed(playlist.uris))
    assert api.update_playlist_tracks(playlist.id, reversed_uris) is True
    assert playlist.uris == reversed_uris


def test_add_at_a_position_across_batches(api, store):
    playlist = store.playlists[pid("alice-morning-mix")]
    new = [t.uri for t in store.catalog[300:384]] + [t.uri for t in store.catalog[40:100]]  # 144: two batches
    before = list(playlist.uris)
    assert api.playlist_add_items(playlist.id, new, position=10) is True
    assert playlist.uris == before[:10] + new + before[10:]


def test_remove_every_occurrence(api, store):
    playlist = store.playlists[pid("alice-on-repeat")]
    target = playlist.uris[0]
    assert api.playlist_remove_items(playlist.id, [target]) is True
    assert target not in playlist.uris


def test_change_details_is_a_success_with_no_body(api, store):
    """Documented as 200 with no body; the client must not retry it as a network error."""
    assert api.update_playlist_details(pid("alice-morning-mix"), public=True, name="Renamed") is None
    assert store.playlists[pid("alice-morning-mix")].public is True


def test_create_a_private_playlist_without_stalling(api, store):
    """The create-then-force-private sequence shuffify uses for new playlists.

    Its follow-up "force private" PUT is the call above. ensure_private
    catches every exception, so only the fixture's backoff check would notice
    it retrying.
    """
    started = time.monotonic()
    created = api.create_user_playlist("Morning Mix [Archive]", public=False, description="Archived tracks")
    playlist_id = created["id"]
    assert ensure_private(api, playlist_id) is True
    assert time.monotonic() - started < 5
    playlist = store.playlists[playlist_id]
    assert (playlist.name, playlist.owner, playlist.public) == ("Morning Mix [Archive]", "alice", False)


# ---------------------------------------------------------------------------
# The scraper, on the fake's public pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy, key", [("_scrape_embed", "bob-finds"), ("_scrape_public_page", "carol-late-night")])
def test_scraper_strategies(store, strategy, key):
    outcome = getattr(PublicScraperPathway(), strategy)(pid(key))
    assert outcome.confirmed and outcome.uris == store.playlists[pid(key)].uris


def test_scraper_on_a_private_playlist_is_unconfirmed(store):
    outcome = PublicScraperPathway()._scrape_embed(pid("dave-private-stash"))
    assert not outcome.confirmed and outcome.uris == []


@pytest.mark.parametrize("key, name, total", [("bob-finds", "Bob's Finds", 60), ("carol-late-night", "Late Night", 30)])
def test_metadata_scrape(store, key, name, total):
    meta = scrape_playlist_metadata(pid(key))
    assert meta["name"] == name and meta["total_tracks"] == total


def test_metadata_scrape_reads_a_name_that_looks_like_markup(store):
    store.playlists[pid("bob-finds")].name = "</script><b>Finds</b>"
    assert scrape_playlist_metadata(pid("bob-finds"))["name"] == "</script><b>Finds</b>"


# ---------------------------------------------------------------------------
# Faults: the client when Spotify is busy, out of quota, failing or slow.
# Each test arms one on the fake and takes the backoffs it expects.
# ---------------------------------------------------------------------------


def arm(server, **spec):
    server.config["FAULTS"].arm(spec)


def fired(server):
    return [f["fired"] for f in server.config["FAULTS"].state()]


def test_a_rate_limit_waits_out_retry_after(server, api, backoffs):
    arm(server, operation="GET /me", kind="rate_limit", retry_after=3)
    assert api.get_current_user()["id"] == "alice"
    assert backoffs == [3]  # Retry-After, over the first backoff's 2s
    backoffs.clear()


def test_a_rate_limit_without_retry_after_backs_off(server, api, backoffs):
    arm(server, operation="GET /me", kind="rate_limit")
    assert api.get_current_user()["id"] == "alice"
    assert backoffs == [2]
    backoffs.clear()


def test_a_wait_longer_than_the_rate_limit_window_is_not_slept_out(server, api, backoffs):
    arm(server, operation="GET /me", kind="rate_limit", retry_after=http_client.MAX_RETRY_AFTER + 1, times=5)
    with pytest.raises(SpotifyRateLimitError) as raised:
        api.get_current_user()
    assert raised.value.retry_after == http_client.MAX_RETRY_AFTER + 1
    assert backoffs == [] and fired(server) == [1]


def test_a_spent_quota_is_not_retried(server, api, backoffs):
    arm(server, operation="GET /me", kind="quota", times=5)
    with pytest.raises(SpotifyQuotaExceededError):
        api.get_current_user()
    assert backoffs == [] and fired(server) == [1]


def test_server_errors_are_retried_with_backoff(server, api, backoffs):
    arm(server, operation="GET /me", kind="server_error", times=2)
    assert api.get_current_user()["id"] == "alice"
    assert backoffs == [2, 4]
    backoffs.clear()


def test_server_errors_that_outlast_the_retries_fail(server, api, backoffs):
    arm(server, operation="GET /me", kind="server_error", status=502, times=5)
    with pytest.raises(SpotifyAPIError, match="Server error 502 after 5 attempts"):
        api.get_current_user()
    assert backoffs == [2, 4, 8, 16]
    backoffs.clear()


def test_a_slow_answer_times_out_and_is_asked_again(server, api, backoffs, monkeypatch):
    monkeypatch.setattr(http_client, "REQUEST_TIMEOUT", 0.25)
    arm(server, operation="GET /me", kind="slow", delay=2)
    assert api.get_current_user()["id"] == "alice"
    assert backoffs == [2]
    backoffs.clear()


def test_an_error_mid_paging_is_retried_never_cut_short(server, api, store, backoffs):
    long_haul = pid("alice-long-haul")
    arm(server, operation="GET /playlists/{playlist_id}/items", kind="server_error", skip=1, playlist_id=long_haul)
    tracks = api.get_playlist_tracks(long_haul)
    assert [t["uri"] for t in tracks] == store.playlists[long_haul].uris
    assert backoffs == [2] and fired(server) == [1]
    backoffs.clear()


@pytest.mark.parametrize("method, completed_batches", [("PUT", 0), ("POST", 1)])
def test_a_batch_answered_503_is_unverified_and_never_replayed(
    server, api, store, backoffs, method, completed_batches
):
    """This fake faults before mutation, but a real 503 cannot prove that.

    A failed first PUT is unknown; a failed append after a confirmed PUT is
    partial. Neither permits the transport to replay the unconfirmed batch.
    """
    playlist = store.playlists[pid("alice-long-haul")]
    original_uris = list(playlist.uris)
    reversed_uris = list(reversed(original_uris))
    arm(server, operation=method + " /playlists/{playlist_id}/items",
        kind="server_error", playlist_id=playlist.id, times=3)
    with pytest.raises(SpotifyPartialBatchError) as raised:
        api.update_playlist_tracks(playlist.id, reversed_uris)
    assert (raised.value.completed_batches, raised.value.total_batches) == (completed_batches, 3)
    assert isinstance(raised.value.cause, SpotifyWriteUnverifiedError)
    assert raised.value.completed_uris == reversed_uris[:completed_batches * 100]
    assert raised.value.remaining_uris == reversed_uris[completed_batches * 100:]
    assert playlist.uris == (reversed_uris[:100] if completed_batches else original_uris)
    assert backoffs == [] and fired(server) == [1]


def test_a_batch_refused_for_quota_is_a_partial_write(server, api, store, backoffs):
    """What a job's rollback restores: the first 100 written, the rest not."""
    playlist = store.playlists[pid("alice-long-haul")]
    reversed_uris = list(reversed(playlist.uris))
    arm(server, operation="POST /playlists/{playlist_id}/items", kind="quota", playlist_id=playlist.id)
    with pytest.raises(SpotifyPartialBatchError) as raised:
        api.update_playlist_tracks(playlist.id, reversed_uris)
    assert (raised.value.completed_batches, raised.value.total_batches) == (1, 3)
    assert isinstance(raised.value.cause, SpotifyQuotaExceededError)
    assert playlist.uris == reversed_uris[:100]
    assert backoffs == []
