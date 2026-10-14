"""The fake Spotify behaves as Spotify documents, in the shapes Spotify publishes.

The fake checks itself: every /v1 request against the parameters and body
Spotify documents, every /v1 response against the documented shape for its
status (shapes.json, from Spotify's OpenAPI document). Anything that departs
is recorded in its violations list, and the ``client`` fixture fails the test
unless that list is empty at the end. The behaviour checks here come from
Spotify's reference: paging, the 403 on another user's playlist items, the
per-request maximums, removal of every occurrence, scopes.

How each behaviour is known, docs-only or checked-live, is labelled in
harness/fake_spotify/behaviours.json, and the scenarios a live replay checks
them with are harness/scenarios/ (tests/test_scenarios.py). shuffify's own
client, run against the fake, is tests/test_fake_spotify_client_contract.py.
"""

import base64
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from config import DEFAULT_SPOTIFY_REDIRECT_URI
from harness.fake_spotify import Store, create_app, spotify_id, tokens
from harness.fake_spotify import app as fake_app
from harness.fake_spotify.render import EMBEDDED_PAGE, Renderer
from harness.fake_spotify.shapes import DEV_MODE_REMOVED, SHAPES, response_problems

REDIRECT = "http://127.0.0.1:8000/callback"
BASIC = "Basic " + base64.b64encode(b"harness-fake-client-id:harness-fake-client-secret").decode()
REPO_ROOT = Path(__file__).resolve().parent.parent
SCOPES = "playlist-read-private playlist-modify-public playlist-modify-private"
UNKNOWN_TRACK = "0000000000000000000000"


def pid(key):
    return spotify_id("playlist", key)


@pytest.fixture
def client():
    app = create_app(redirect_uris=[REDIRECT])
    yield app.test_client()
    assert app.config["VIOLATIONS"] == [], "the fake departed from Spotify's documentation"


@pytest.fixture
def store(client):
    return client.application.config["STORE"]


def violations(client):
    """Take the recorded violations, leaving the list empty for the fixture."""
    recorded = client.application.config["VIOLATIONS"]
    taken = list(recorded)
    recorded.clear()
    return taken


def authorize_url(**extra):
    params = {"client_id": "harness-fake-client-id", "response_type": "code",
              "redirect_uri": REDIRECT, "state": "s1", "scope": SCOPES, **extra}
    return "/authorize?" + urlencode(params)


def code_for(client, user="alice"):
    location = client.get(authorize_url(fake_user=user)).headers["Location"]
    return parse_qs(urlsplit(location).query)["code"][0]


def exchange(client, code, redirect_uri=REDIRECT, auth=BASIC):
    return client.post("/api/token", headers={"Authorization": auth},
                       data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})


def tokens_for(client, user="alice"):
    resp = exchange(client, code_for(client, user))
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def token_with(scope, user="alice"):
    return bearer(tokens.mint("access", 60, user=user, scope=scope))


@pytest.fixture
def alice(client):
    return bearer(tokens_for(client)["access_token"])


def local(url):
    """A link the fake wrote, as a path its test client can request."""
    assert url.startswith("http://localhost/v1/"), url
    return url[len("http://localhost"):]


# ---------------------------------------------------------------------------
# The fake covers exactly the operations shuffify calls
# ---------------------------------------------------------------------------


def test_every_api_route_is_a_documented_operation_and_vice_versa():
    app = create_app()
    routes = {
        f"{method} {re.sub(r'<(\w+)>', r'{\1}', rule.rule[len('/v1'):])}"
        for rule in app.url_map.iter_rules() if rule.rule.startswith("/v1/")
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    assert routes == set(SHAPES["operations"])


def test_shapes_cover_the_operations_the_extractor_lists():
    import importlib.util

    spec = importlib.util.spec_from_file_location("extract", REPO_ROOT / "scripts" / "extract-spotify-shapes.py")
    extract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extract)
    assert {f"{m} {p}" for m, p in extract.OPERATIONS} == set(SHAPES["operations"])


def test_only_audio_features_is_deprecated():
    """Spotify's schema marks the calls it withholds from Development Mode
    apps as deprecated. The one such call shuffify makes, /audio-features,
    is answered with the 403 those apps get."""
    assert [op for op, spec in SHAPES["operations"].items() if spec["deprecated"]] == ["GET /audio-features"]


def test_fake_credentials_are_the_harness_ones():
    from config import HARNESS_FAKE_CLIENT_ID, HARNESS_FAKE_CLIENT_SECRET

    assert (fake_app.CLIENT_ID, fake_app.CLIENT_SECRET) == (HARNESS_FAKE_CLIENT_ID, HARNESS_FAKE_CLIENT_SECRET)


def test_seed_has_more_users_than_the_development_mode_cap():
    assert len(Store().users) > 5


# ---------------------------------------------------------------------------
# The fake's own checks
# ---------------------------------------------------------------------------


class TestResponseCheck:
    """shapes.response_problems, which the fake runs on every /v1 response."""

    def me(self, **changes):
        body = {"id": "alice", "display_name": "Alice", "external_urls": {"spotify": "x"},
                "href": "x", "images": [], "type": "user", "uri": "spotify:user:alice"}
        return {**body, **changes}

    def test_a_valid_body_has_no_problems(self):
        assert response_problems("GET /me", 200, self.me(), has_body=True) == []

    def test_a_missing_required_field(self):
        page = {"href": "x", "items": [], "limit": 20, "next": None, "offset": 0, "previous": None}
        assert response_problems("GET /me/playlists", 200, page, has_body=True) == ["$.total: required, missing"]
        assert response_problems("GET /me/playlists", 200, {**page, "total": 0}, has_body=True) == []

    def test_a_null_where_none_is_allowed(self):
        assert response_problems("GET /me", 200, self.me(href=None), has_body=True) == [
            "$.href: null, but the schema allows no null"
        ]

    def test_a_wrong_type(self):
        assert response_problems("GET /me", 200, self.me(images="none"), has_body=True) == [
            "$.images: expected an array, got str"
        ]

    def test_a_body_where_none_is_documented(self):
        assert response_problems("PUT /playlists/{playlist_id}", 200, {}, has_body=True) == [
            "PUT /playlists/{playlist_id} 200 is documented with no body"
        ]


class TestSelfCheck:
    def test_a_response_field_spotify_removed_is_recorded(self, client, alice, monkeypatch):
        real_me = Renderer.me
        monkeypatch.setattr(Renderer, "me", lambda self, user: {**real_me(self, user), "email": "a@example.com"})
        assert client.get("/v1/me", headers=alice).status_code == 200
        assert violations(client) == [
            "GET /me 200 response: $.email: removed for Development Mode apps"
        ]

    def test_an_undocumented_status_is_recorded(self, client, alice, monkeypatch):
        def teapot():
            raise fake_app.APIError(418, "I'm a teapot")

        monkeypatch.setitem(client.application.view_functions, "me", teapot)
        assert client.get("/v1/me", headers=alice).status_code == 418
        assert violations(client) == ["GET /me 418 response: Spotify documents no 418 for GET /me"]

    def test_an_undocumented_parameter_is_served_and_recorded(self, client, alice):
        assert client.get("/v1/me/playlists?sort=name", headers=alice).status_code == 200
        assert violations(client) == ["GET /me/playlists request: ?sort: not a documented parameter"]

    def test_a_repeated_parameter_is_recorded(self, client, alice):
        assert client.get("/v1/search?q=a&type=track&type=album", headers=alice).status_code == 200
        assert violations(client) == ["GET /search request: ?type given more than once"]

    def test_an_undocumented_body_field_is_served_and_recorded(self, client, store, alice):
        resp = client.put(f"/v1/playlists/{pid('alice-morning-mix')}/items", headers=alice,
                          json={"uris": [store.catalog[0].uri], "shuffle": True})
        assert resp.status_code == 200
        assert violations(client) == [
            "PUT /playlists/{playlist_id}/items request: body.shuffle: not in Spotify's schema"
        ]

    @pytest.mark.parametrize("method, path", [
        # Not served to Development Mode apps.
        ("POST", "/v1/users/alice/playlists"),
        ("GET", f"/v1/tracks?ids={UNKNOWN_TRACK}"),
        ("GET", f"/v1/playlists/{pid('alice-morning-mix')}/tracks"),
        # Not a method the operation takes.
        ("POST", "/v1/me"),
        ("HEAD", "/v1/me"),
    ])
    def test_a_call_the_fake_does_not_serve_is_recorded(self, client, alice, method, path):
        resp = client.open(path, method=method, headers=alice)
        assert resp.status_code == 404
        if method != "HEAD":
            assert resp.get_json()["error"] == {"status": 404, "message": "Service not found"}
        assert violations(client) == [f"{method} {urlsplit(path).path}: not an operation the fake serves"]

    def test_a_body_that_is_not_json_is_refused_and_recorded(self, client, store, alice):
        resp = client.put(f"/v1/playlists/{pid('alice-morning-mix')}", headers=alice, data={"name": "x"})
        assert resp.status_code == 400 and store.playlists[pid("alice-morning-mix")].name == "Morning Mix"
        assert violations(client) == [
            "PUT /playlists/{playlist_id} request: a application/x-www-form-urlencoded body, not application/json"
        ]

    @pytest.mark.parametrize("path", [
        "/v1/search?type=track",  # q is required
        "/v1/search?q=a&type=podcast",  # not one of the documented types
        "/v1/me/playlists?limit=x",
        "/v1/me/playlists?limit=1_0",
        "/v1/me/playlists?offset=-1",
        "/v1/me/playlists?offset=100001",
        f"/v1/playlists/{pid('alice-morning-mix')}/items?offset=-15&limit=25",
    ])
    def test_a_request_breaking_a_documented_constraint_is_a_400(self, client, alice, path):
        resp = client.get(path, headers=alice)
        assert resp.status_code == 400 and resp.get_json()["error"]["status"] == 400

    def test_a_malformed_json_body_is_a_400(self, client, alice):
        resp = client.put(f"/v1/playlists/{pid('alice-morning-mix')}", headers=alice,
                          data="{not json", content_type="application/json")
        assert resp.status_code == 400 and resp.get_json()["error"]["message"] == "Error parsing JSON."

    def test_links_never_point_off_the_machine(self, client, alice):
        resp = client.get("/v1/me", headers=alice, base_url="http://api.spotify.com")
        assert resp.status_code == 400
        served = create_app(origin="http://127.0.0.1:8900").test_client()
        me = served.get("/v1/me", headers=token_with("")).get_json()
        assert me["href"] == "http://127.0.0.1:8900/v1/users/alice"


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


class TestAuthorize:
    def test_login_page_offers_every_seeded_user(self, client, store):
        page = client.get(authorize_url()).get_data(as_text=True)
        for user in store.users.values():
            assert f'data-user="{user.id}"' in page

    def test_login_page_links_stay_on_the_fakes_origin(self):
        served = create_app(redirect_uris=[REDIRECT], origin="http://127.0.0.1:8900").test_client()
        page = served.get(authorize_url(), headers={"Host": "evil.example"}).get_data(as_text=True)
        links = re.findall(r'href="([^"]+)"', page)
        assert links and all(link.startswith("http://127.0.0.1:8900/authorize?") for link in links)

    def test_picking_a_user_redirects_with_a_code_and_the_state(self, client):
        resp = client.get(authorize_url(fake_user="bob"))
        location = urlsplit(resp.headers["Location"])
        assert resp.status_code == 302
        assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT
        assert parse_qs(location.query)["state"] == ["s1"] and parse_qs(location.query)["code"]

    def test_cancel_redirects_with_access_denied(self, client):
        query = parse_qs(urlsplit(client.get(authorize_url(deny="1")).headers["Location"]).query)
        assert query == {"error": ["access_denied"], "state": ["s1"]}

    @pytest.mark.parametrize("override", [{"client_id": "someone-else"}, {"redirect_uri": "http://evil.invalid/cb"}])
    def test_unknown_client_or_redirect_uri_is_refused(self, client, override):
        resp = client.get(authorize_url(**override, fake_user="alice"))
        assert resp.status_code == 400 and "Location" not in resp.headers

    @pytest.mark.parametrize("uri, allowed", [
        # Spotify's rule: HTTPS, or HTTP on a loopback IP literal; never localhost.
        ("http://localhost:8000/callback", False),
        ("https://localhost/callback", False),
        ("http://example.com/callback", False),
        ("https://example.com/callback", True),
        ("http://127.0.0.1:8000/callback", True),
        ("http://[::1]:8000/callback", True),
    ])
    def test_the_redirect_uri_rule_applies_even_when_registered(self, uri, allowed):
        client = create_app(redirect_uris=[uri]).test_client()
        resp = client.get(authorize_url(redirect_uri=uri, fake_user="alice"))
        assert (resp.status_code == 302) is allowed

    def test_the_apps_default_redirect_uri_is_one_spotify_accepts(self):
        """config.py's fallback, for a development setup that sets no
        SPOTIFY_REDIRECT_URI."""
        uri = DEFAULT_SPOTIFY_REDIRECT_URI
        client = create_app(redirect_uris=[uri]).test_client()
        assert client.get(authorize_url(redirect_uri=uri, fake_user="alice")).status_code == 302


class TestToken:
    def test_code_exchange_returns_spotifys_token_fields(self, client):
        body = tokens_for(client)
        assert set(body) == {"access_token", "token_type", "scope", "expires_in", "refresh_token"}
        assert body["token_type"] == "Bearer" and body["expires_in"] == 3600 and body["scope"] == SCOPES

    def test_a_code_works_once(self, client):
        code = code_for(client)
        assert exchange(client, code).status_code == 200
        again = exchange(client, code)
        assert again.status_code == 400 and again.get_json()["error"] == "invalid_grant"

    def test_an_expired_code_is_refused(self, client):
        code = tokens.mint("code", -1, user="alice", scope="", redirect_uri=REDIRECT)
        assert exchange(client, code).get_json()["error"] == "invalid_grant"

    def test_redirect_uri_must_match_the_one_authorized(self, client):
        resp = exchange(client, code_for(client), redirect_uri="http://127.0.0.1:9/cb")
        assert resp.get_json()["error"] == "invalid_grant"

    def test_wrong_client_secret_is_refused(self, client):
        bad = "Basic " + base64.b64encode(b"harness-fake-client-id:wrong").decode()
        resp = exchange(client, code_for(client), auth=bad)
        assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_client"

    def test_refresh_returns_a_new_access_token_and_no_refresh_token(self, client):
        refresh = tokens_for(client)["refresh_token"]
        resp = client.post("/api/token", headers={"Authorization": BASIC},
                           data={"grant_type": "refresh_token", "refresh_token": refresh})
        assert resp.status_code == 200
        assert "access_token" in resp.get_json() and "refresh_token" not in resp.get_json()

    def test_tokens_survive_a_restart_of_the_fake(self, client):
        refresh = tokens_for(client)["refresh_token"]
        fresh = create_app(redirect_uris=[REDIRECT]).test_client()
        resp = fresh.post("/api/token", headers={"Authorization": BASIC},
                          data={"grant_type": "refresh_token", "refresh_token": refresh})
        assert resp.status_code == 200

    def test_expired_access_token_gets_401(self, client):
        resp = client.get("/v1/me", headers=bearer(tokens.mint("access", -1, user="alice", scope="")))
        assert resp.status_code == 401 and resp.get_json()["error"]["message"] == "The access token expired"

    @pytest.mark.parametrize("header", ["Bearer nope.nope", "Basic YTpi", ""])
    def test_a_missing_or_forged_token_gets_401(self, client, header):
        assert client.get("/v1/me", headers={"Authorization": header} if header else {}).status_code == 401

    def test_a_refresh_token_is_not_an_access_token(self, client):
        refresh = tokens_for(client)["refresh_token"]
        assert client.get("/v1/me", headers=bearer(refresh)).status_code == 401


class TestScopes:
    def test_writing_a_private_playlist_needs_playlist_modify_private(self, client, store):
        path = f"/v1/playlists/{pid('alice-morning-mix')}/items"  # private
        body = {"uris": [store.catalog[0].uri]}
        assert client.post(path, headers=token_with("playlist-modify-public"), json=body).status_code == 403
        assert client.post(path, headers=token_with("playlist-modify-private"), json=body).status_code == 201

    def test_writing_a_public_playlist_needs_playlist_modify_public(self, client):
        path = f"/v1/playlists/{pid('alice-long-haul')}"  # public
        assert client.put(path, headers=token_with("playlist-modify-private"), json={"name": "x"}).status_code == 403
        assert client.put(path, headers=token_with("playlist-modify-public"), json={"name": "x"}).status_code == 200

    def test_creating_a_private_playlist_needs_playlist_modify_private(self, client):
        body = {"name": "Archive", "public": False}
        assert client.post("/v1/me/playlists", headers=token_with("playlist-modify-public"),
                           json=body).status_code == 403

    def test_without_playlist_read_private_the_library_is_public_playlists(self, client, store):
        body = client.get("/v1/me/playlists?limit=50", headers=token_with("")).get_json()
        assert body["items"] and all(store.playlists[p["id"]].public for p in body["items"])


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_me_carries_no_field_removed_for_development_mode(client, alice):
    body = client.get("/v1/me", headers=alice).get_json()
    assert body["id"] == "alice" and not set(body) & DEV_MODE_REMOVED["PrivateUserObject"]


class TestMyPlaylists:
    def test_library_is_owned_collaborated_and_followed_playlists(self, client, store, alice):
        body = client.get("/v1/me/playlists?limit=50", headers=alice).get_json()
        ids = {p["id"] for p in body["items"]}
        assert ids == {p.id for p in store.playlists.values()
                       if p.owner == "alice" or "alice" in p.collaborators | p.followers}
        assert pid("bob-road-trip") in ids and pid("bob-finds") in ids and pid("carol-late-night") not in ids

    def test_paging_follows_next_to_the_end(self, client, alice):
        url, seen = "/v1/me/playlists?limit=2", []
        while url:
            body = client.get(url, headers=alice).get_json()
            seen += body["items"]
            url = body["next"] and local(body["next"])
        assert len(seen) == body["total"] and len({p["id"] for p in seen}) == len(seen)

    def test_the_last_page_has_no_next(self, client, alice):
        total = client.get("/v1/me/playlists?limit=1", headers=alice).get_json()["total"]
        last = client.get(f"/v1/me/playlists?limit=2&offset={total - 2}", headers=alice).get_json()
        assert len(last["items"]) == 2 and last["next"] is None and last["previous"]

    @pytest.mark.parametrize("limit", ["0", "51"])
    def test_limit_outside_1_to_50_is_a_400(self, client, alice, limit):
        assert client.get(f"/v1/me/playlists?limit={limit}", headers=alice).status_code == 400


class TestGetPlaylist:
    def test_owner_gets_metadata_and_a_first_page_whose_next_link_works(self, client, store, alice):
        body = client.get(f"/v1/playlists/{pid('alice-long-haul')}", headers=alice).get_json()
        page = body["items"]
        assert page["total"] == 250 and len(page["items"]) == EMBEDDED_PAGE
        uris = [e["item"]["uri"] for e in page["items"]]
        while page["next"]:
            page = client.get(local(page["next"]), headers=alice).get_json()
            uris += [e["item"]["uri"] for e in page["items"]]
        assert uris == store.playlists[pid("alice-long-haul")].uris

    def test_someone_elses_public_playlist_is_metadata_only(self, client, alice):
        body = client.get(f"/v1/playlists/{pid('carol-late-night')}", headers=alice).get_json()
        assert body["name"] == "Late Night" and "items" not in body and "tracks" not in body

    def test_someone_elses_private_playlist_is_not_found(self, client, alice):
        assert client.get(f"/v1/playlists/{pid('dave-private-stash')}", headers=alice).status_code == 404

    def test_legacy_shape_playlist_uses_tracks_and_track(self, client, alice):
        body = client.get(f"/v1/playlists/{pid('alice-old-faithful')}", headers=alice).get_json()
        assert "tracks" in body and "items" not in body
        assert "track" in body["tracks"]["items"][0] and "item" not in body["tracks"]["items"][0]


class TestPlaylistItems:
    def test_default_page_is_20_and_max_is_50(self, client, alice):
        path = f"/v1/playlists/{pid('alice-long-haul')}/items"
        assert len(client.get(path, headers=alice).get_json()["items"]) == 20
        assert client.get(path + "?limit=51", headers=alice).status_code == 400

    def test_items_of_a_playlist_you_neither_own_nor_edit_are_forbidden(self, client, alice):
        assert client.get(f"/v1/playlists/{pid('bob-finds')}/items", headers=alice).status_code == 403

    def test_collaborator_can_read_items(self, client, alice):
        resp = client.get(f"/v1/playlists/{pid('bob-road-trip')}/items", headers=alice)
        assert resp.status_code == 200 and resp.get_json()["total"] == 30

    def test_fields_filter_selects_nested_keys(self, client, alice):
        path = f"/v1/playlists/{pid('alice-morning-mix')}/items"
        assert client.get(f"{path}?fields=total&limit=1", headers=alice).get_json() == {"total": 40}
        full = client.get(f"{path}?limit=2", headers=alice).get_json()["items"]
        body = client.get(f"{path}?limit=2&fields=items(added_by(id),item(name,album(name)))",
                          headers=alice).get_json()
        assert body == {"items": [
            {"added_by": {"id": "alice"},
             "item": {"name": e["item"]["name"], "album": {"name": e["item"]["album"]["name"]}}}
            for e in full
        ]}

    @pytest.mark.parametrize("fields", [
        "items(item(name",
        "items()",
        "a(" * 20 + "b" + ")" * 20,
        # Documented, but shuffify sends neither, so the fake refuses them.
        "items.added_by.id",
        "!items",
    ])
    def test_a_fields_filter_the_fake_cannot_apply_is_a_400(self, client, alice, fields):
        resp = client.get(f"/v1/playlists/{pid('alice-morning-mix')}/items?{urlencode({'fields': fields})}",
                          headers=alice)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


class TestWrites:
    def uris(self, store, start, end):
        return [t.uri for t in store.catalog[start:end]]

    def test_add_at_a_position_and_snapshot_changes(self, client, store, alice):
        playlist = store.playlists[pid("alice-morning-mix")]
        before = playlist.snapshot_id
        new = self.uris(store, 330, 332)
        resp = client.post(f"/v1/playlists/{playlist.id}/items", headers=alice, json={"uris": new, "position": 1})
        assert resp.status_code == 201 and resp.get_json()["snapshot_id"] != before
        assert playlist.uris[1:3] == new and len(playlist.uris) == 42

    def test_more_than_100_uris_per_request_is_a_400(self, client, store, alice):
        resp = client.post(f"/v1/playlists/{pid('alice-morning-mix')}/items", headers=alice,
                           json={"uris": self.uris(store, 0, 101)})
        assert resp.status_code == 400

    @pytest.mark.parametrize("body", [
        {"uris": [f"spotify:track:{UNKNOWN_TRACK}"]},
        {"uris": ["spotify:episode:0000000000000000000000"]},
        {"uris": "spotify:track:x"},  # a string, not an array
        {"uris": [], "position": 41},  # past the end
        {},
    ])
    def test_a_bad_add_is_a_400(self, client, store, alice, body):
        resp = client.post(f"/v1/playlists/{pid('alice-morning-mix')}/items", headers=alice, json=body)
        assert resp.status_code == 400 and len(store.playlists[pid("alice-morning-mix")].uris) == 40

    def test_replace(self, client, store, alice):
        new = self.uris(store, 5, 8)
        resp = client.put(f"/v1/playlists/{pid('alice-morning-mix')}/items", headers=alice, json={"uris": new})
        assert resp.status_code == 200 and store.playlists[pid("alice-morning-mix")].uris == new

    def test_reordering_is_not_modelled_and_says_so(self, client, alice):
        resp = client.put(f"/v1/playlists/{pid('alice-morning-mix')}/items", headers=alice,
                          json={"range_start": 0, "insert_before": 40})
        assert resp.status_code == 400 and "reordering" in resp.get_json()["error"]["message"]

    def test_remove_drops_every_occurrence(self, client, store, alice):
        playlist = store.playlists[pid("alice-on-repeat")]
        target = playlist.uris[0]
        assert playlist.uris.count(target) == 2
        resp = client.delete(f"/v1/playlists/{playlist.id}/items", headers=alice, json={"items": [{"uri": target}]})
        assert resp.status_code == 200 and target not in playlist.uris and len(playlist.uris) == 8

    def test_remove_without_a_body_is_a_400(self, client, alice):
        assert client.delete(f"/v1/playlists/{pid('alice-on-repeat')}/items", headers=alice).status_code == 400

    def test_collaborator_can_edit_but_a_follower_cannot(self, client, store, alice):
        new = self.uris(store, 0, 1)
        ok = client.post(f"/v1/playlists/{pid('bob-road-trip')}/items", headers=alice, json={"uris": new})
        refused = client.post(f"/v1/playlists/{pid('bob-finds')}/items", headers=alice, json={"uris": new})
        assert ok.status_code == 201 and refused.status_code == 403

    def test_change_details_is_owner_only_and_200_with_no_body(self, client, store, alice):
        mine = client.put(f"/v1/playlists/{pid('alice-morning-mix')}", headers=alice,
                          json={"public": True, "name": "Renamed"})
        collab = client.put(f"/v1/playlists/{pid('bob-road-trip')}", headers=alice, json={"name": "Mine now"})
        assert mine.status_code == 200 and mine.data == b"" and collab.status_code == 403
        playlist = store.playlists[pid("alice-morning-mix")]
        assert playlist.public is True and playlist.name == "Renamed"

    def test_collaborative_is_not_modelled_and_says_so(self, client, alice):
        resp = client.put(f"/v1/playlists/{pid('alice-morning-mix')}", headers=alice, json={"collaborative": True})
        assert resp.status_code == 400 and "collaborative" in resp.get_json()["error"]["message"]

    def test_create_playlist_for_the_current_user(self, client, store, alice):
        made = client.post("/v1/me/playlists", headers=alice, json={"name": "Archive [Shuffify]", "public": False})
        assert made.status_code == 201
        playlist = store.playlists[made.get_json()["id"]]
        assert playlist.owner == "alice" and playlist.public is False

    def test_create_playlist_without_a_name_is_a_400(self, client, store, alice):
        before = len(store.playlists)
        assert client.post("/v1/me/playlists", headers=alice, json={"public": False}).status_code == 400
        assert len(store.playlists) == before


# ---------------------------------------------------------------------------
# Catalogue and search
# ---------------------------------------------------------------------------


class TestTrack:
    def test_a_known_track(self, client, store, alice):
        track = store.catalog[0]
        body = client.get(f"/v1/tracks/{track.id}", headers=alice).get_json()
        assert body["uri"] == track.uri and body["name"] == track.name

    def test_an_unknown_id_is_not_found(self, client, alice):
        assert client.get(f"/v1/tracks/{UNKNOWN_TRACK}", headers=alice).status_code == 404

    def test_a_malformed_id_is_a_400(self, client, alice):
        assert client.get("/v1/tracks/not-an-id", headers=alice).status_code == 400


def test_audio_features_are_forbidden_to_a_development_mode_app(client, store, alice):
    assert client.get(f"/v1/audio-features?ids={store.catalog[0].id}", headers=alice).status_code == 403


class TestSearch:
    def test_default_limit_5_and_max_10(self, client, alice):
        body = client.get("/v1/search?q=a&type=track", headers=alice).get_json()
        assert body["tracks"]["limit"] == 5 and len(body["tracks"]["items"]) == 5
        assert client.get("/v1/search?q=a&type=track&limit=11", headers=alice).status_code == 400

    def test_field_filter_narrows_to_an_artist(self, client, store, alice):
        artist = store.catalog[0].artists[0].name
        query = urlencode({"q": f'artist:"{artist}"', "type": "track", "limit": 10})
        body = client.get(f"/v1/search?{query}", headers=alice).get_json()
        assert body["tracks"]["items"]
        assert all(t["artists"][0]["name"] == artist for t in body["tracks"]["items"])

    def test_playlist_search_finds_public_playlists_only(self, client, alice):
        public = client.get("/v1/search?q=late&type=playlist", headers=alice).get_json()
        private = client.get("/v1/search?q=stash&type=playlist", headers=alice).get_json()
        assert [p["name"] for p in public["playlists"]["items"]] == ["Late Night"]
        assert private["playlists"]["items"] == []

    @pytest.mark.parametrize("kind", ["album", "track,playlist"])
    def test_types_shuffify_never_searches_for_are_not_modelled(self, client, alice, kind):
        resp = client.get(f"/v1/search?q=a&type={kind}", headers=alice)
        assert resp.status_code == 400 and "does not model" in resp.get_json()["error"]["message"]


# ---------------------------------------------------------------------------
# The public pages shuffify's scraper reads
# ---------------------------------------------------------------------------


def _next_data(html):
    blob = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S).group(1)
    return json.loads(blob)["props"]["pageProps"]["state"]["data"]["entity"]


class TestPublicPages:
    def test_embed_page_lists_the_playlist_in_order(self, client, store):
        entity = _next_data(client.get(f"/embed/playlist/{pid('bob-finds')}").get_data(as_text=True))
        assert entity["name"] == "Bob's Finds"
        assert [row["uri"] for row in entity["trackList"]] == store.playlists[pid("bob-finds")].uris

    def test_private_playlist_pages_are_not_found(self, client):
        assert client.get(f"/embed/playlist/{pid('dave-private-stash')}").status_code == 404
        assert client.get(f"/playlist/{pid('dave-private-stash')}").status_code == 404

    def test_names_cannot_break_out_of_the_page(self, client, store):
        playlist = store.playlists[pid("bob-finds")]
        playlist.name = "</script><script>alert(1)</script>"
        html = client.get(f"/embed/playlist/{playlist.id}").get_data(as_text=True)
        assert "<script>alert(1)" not in html
        assert _next_data(html)["name"] == playlist.name


# ---------------------------------------------------------------------------
# Control routes
# ---------------------------------------------------------------------------


def test_reset_restores_the_seed(client, store):
    store.replace(store.playlists[pid("alice-morning-mix")], [], "alice")
    client.post("/_fake/reset")
    assert len(client.get(f"/_fake/playlists/{pid('alice-morning-mix')}").get_json()["uris"]) == 40


def test_violations_are_readable_over_http(client, alice):
    assert client.get("/_fake/violations").get_json() == []
    client.get("/v1/me?locale=en", headers=alice)
    assert client.get("/_fake/violations").get_json() == ["GET /me request: ?locale: not a documented parameter"]
    violations(client)


# ---------------------------------------------------------------------------
# Faults: Spotify's documented failure answers, on demand (faults.py)
# ---------------------------------------------------------------------------


def arm(client, **spec):
    resp = client.post("/_fake/faults", json=spec)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def fired(client):
    """How often each armed fault has answered a call, in the order they were armed."""
    return [fault["fired"] for fault in client.get("/_fake/faults").get_json()]


def wait_until(what, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.005)


def call_in_thread(client, path, headers):
    """GET ``path`` from a thread of its own, so the test can act while the fake holds the call: the thread,
    and a list that gains the status the call is answered with."""
    statuses = []
    thread = threading.Thread(
        target=lambda: statuses.append(client.application.test_client().get(path, headers=headers).status_code),
        daemon=True,  # a test that fails with the call held must not make the run wait out the delay
    )
    thread.start()
    return thread, statuses


class TestFaults:
    def test_a_rate_limit_is_a_429_with_retry_after_then_service_resumes(self, client, alice):
        arm(client, operation="GET /me", kind="rate_limit", retry_after=7)
        limited = client.get("/v1/me", headers=alice)
        assert limited.status_code == 429 and limited.headers["Retry-After"] == "7"
        assert limited.get_json() == {"error": {"status": 429, "message": "API rate limit exceeded"}}
        assert client.get("/v1/me", headers=alice).status_code == 200

    def test_a_rate_limit_can_leave_out_retry_after(self, client, alice):
        # Spotify: the 429 "will normally include" it.
        arm(client, operation="GET /me", kind="rate_limit")
        assert "Retry-After" not in client.get("/v1/me", headers=alice).headers

    def test_a_spent_quota_is_the_july_2026_body_and_matches_the_corrected_schema(self, client, alice):
        arm(client, operation="GET /me", kind="quota")
        resp = client.get("/v1/me", headers=alice)
        assert resp.status_code == 429 and "Retry-After" not in resp.headers
        assert resp.get_json() == {"error": {"status": 429, "message": "Too many requests", "reason": "QUOTA_EXCEEDED"}}
        # The client fixture fails the test unless the fake recorded nothing,
        # so the corrected 429 schema accepts the reason ...
        assert response_problems("GET /me", 429, resp.get_json(), has_body=True) == []
        # ... on a 429 only.
        unauthorized = {"error": {"status": 401, "message": "x", "reason": "QUOTA_EXCEEDED"}}
        assert response_problems("GET /me", 401, unauthorized, has_body=True) != []

    @pytest.mark.parametrize("status, message", [(500, "Internal Server Error"), (502, "Bad Gateway"),
                                                 (503, "Service Unavailable")])
    def test_a_server_error_is_answered_before_anything_is_applied(self, client, store, alice, status, message):
        playlist = store.playlists[pid("alice-morning-mix")]
        before = list(playlist.uris)
        arm(client, operation="PUT /playlists/{playlist_id}/items", kind="server_error", status=status)
        resp = client.put(f"/v1/playlists/{playlist.id}/items", headers=alice, json={"uris": before[::-1]})
        assert resp.status_code == status
        assert resp.get_json() == {"error": {"status": status, "message": message}}
        assert playlist.uris == before

    def test_skip_lets_calls_through_first_and_times_bounds_the_answers(self, client, alice):
        arm(client, operation="GET /me", kind="server_error", skip=1, times=2)
        assert [client.get("/v1/me", headers=alice).status_code for _ in range(4)] == [200, 503, 503, 200]
        assert client.get("/_fake/faults").get_json()[0] == {
            "operation": "GET /me", "kind": "server_error", "times": 2, "skip": 1, "playlist_id": None,
            "retry_after": None, "status": 503, "delay": 0.0, "seen": 3, "fired": 2,
        }

    def test_a_fault_for_one_playlist_leaves_the_others_alone(self, client, alice):
        arm(client, operation="GET /playlists/{playlist_id}/items", kind="server_error",
            playlist_id=pid("alice-long-haul"))
        assert client.get(f"/v1/playlists/{pid('alice-morning-mix')}/items", headers=alice).status_code == 200
        assert client.get(f"/v1/playlists/{pid('alice-long-haul')}/items", headers=alice).status_code == 503

    def test_a_slow_answer_is_late_but_served(self, client, alice, monkeypatch):
        held = []
        monkeypatch.setattr(fake_app, "_hold", lambda fault: held.append(fault.delay))
        arm(client, operation="GET /me", kind="slow", delay=2.5)
        assert client.get("/v1/me", headers=alice).status_code == 200
        assert held == [2.5]

    def test_release_answers_a_held_call_at_once_and_the_fault_still_counts_as_fired(self, client, alice):
        arm(client, operation="GET /me", kind="slow", delay=30)
        thread, statuses = call_in_thread(client, "/v1/me", alice)
        wait_until("the call to be held", lambda: fired(client) == [1])
        thread.join(0.2)
        assert thread.is_alive() and statuses == []  # still waiting out its delay
        released = client.post("/_fake/faults/release")
        assert released.status_code == 200 and released.get_json() == {"status": "released"}
        thread.join(10)  # well short of the 30 seconds it was to wait
        assert not thread.is_alive() and statuses == [200]
        assert fired(client) == [1]

    def test_a_released_fault_does_not_hold_its_later_firings(self, client, alice):
        arm(client, operation="GET /me", kind="slow", delay=30, times=2)
        client.post("/_fake/faults/release")
        started = time.monotonic()
        assert [client.get("/v1/me", headers=alice).status_code for _ in range(2)] == [200, 200]
        assert time.monotonic() - started < 10  # not 30 seconds each
        assert fired(client) == [2]

    def test_one_release_frees_every_held_call(self, client, alice):
        arm(client, operation="GET /me", kind="slow", delay=30)
        arm(client, operation="GET /me/playlists", kind="slow", delay=30)
        calls = [call_in_thread(client, path, alice) for path in ("/v1/me", "/v1/me/playlists")]
        wait_until("both calls to be held", lambda: fired(client) == [1, 1])
        client.post("/_fake/faults/release")
        for thread, statuses in calls:
            thread.join(10)
            assert not thread.is_alive() and statuses == [200]

    def test_a_slow_fault_armed_after_a_release_holds_again(self, client, alice):
        arm(client, operation="GET /me", kind="slow", delay=30)
        client.post("/_fake/faults/release")
        arm(client, operation="GET /me/playlists", kind="slow", delay=30)
        thread, statuses = call_in_thread(client, "/v1/me/playlists", alice)
        wait_until("the call to be held", lambda: fired(client) == [0, 1])
        thread.join(0.2)
        assert thread.is_alive() and statuses == []
        client.post("/_fake/faults/release")
        thread.join(10)
        assert not thread.is_alive() and statuses == [200]

    def test_a_request_spotify_would_refuse_gets_its_400_not_the_fault(self, client, alice):
        """The fault comes after the request checks, so it hides no contract problem."""
        arm(client, operation="GET /me/playlists", kind="server_error")
        assert client.get("/v1/me/playlists?limit=0", headers=alice).status_code == 400
        assert client.get("/v1/me/playlists", headers=alice).status_code == 503

    @pytest.mark.parametrize("spec, problem", [
        ({"operation": "GET /tracks", "kind": "quota"}, "not one the fake serves"),
        ({"operation": "GET /me", "kind": "outage"}, "kind must be one of"),
        ({"operation": "GET /me", "kind": "quota", "retry_after": 3}, "retry_after applies only to a rate_limit"),
        ({"operation": "GET /me", "kind": "server_error", "status": 504}, "status must be one of"),
        ({"operation": "GET /me", "kind": "slow"}, "needs a delay"),
        ({"operation": "GET /me", "kind": "slow", "delay": 600}, "at most 60"),
        ({"operation": "GET /me", "kind": "quota", "times": 0}, "times must be a whole number of at least 1"),
        ({"operation": "GET /me", "kind": "quota", "playlist_id": "x"}, "playlist_id applies only"),
        ({"operation": "GET /me", "kind": "quota", "tiems": 2}, "unknown fault fields: tiems"),
    ])
    def test_a_mistaken_fault_is_refused_with_why(self, client, spec, problem):
        resp = client.post("/_fake/faults", json=spec)
        assert resp.status_code == 400 and problem in resp.get_json()["error"]
        assert client.get("/_fake/faults").get_json() == []

    def test_reset_and_delete_clear_faults(self, client, alice):
        arm(client, operation="GET /me", kind="quota", times=5)
        client.post("/_fake/reset")
        assert client.get("/_fake/faults").get_json() == []
        arm(client, operation="GET /me", kind="quota", times=5)
        client.delete("/_fake/faults")
        assert client.get("/v1/me", headers=alice).status_code == 200
