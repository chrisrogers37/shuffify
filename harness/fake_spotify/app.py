"""The fake Spotify: every call shuffify makes, served from local state.

Which calls come from shuffify's client (shuffify/spotify/, the scraper);
how each behaves comes from Spotify's documentation, never from what
shuffify expects. One origin stands in for all three Spotify hosts, which is
what SPOTIFY_BASE_URL does on the app side:

  accounts.spotify.com  /authorize, /api/token
  api.spotify.com       /v1/...
  open.spotify.com      /embed/playlist/<id>, /playlist/<id>

and /_fake/... lets tests and the harness read and reset its state, and
arm faults: a 429, a spent quota, a 5xx or a slow answer on demand
(faults.py).

Every /v1 request is checked against Spotify's documented parameters and
body (shapes.json) before it is served, and every /v1 response against the
documented response shapes after. A request that breaks a documented
constraint gets the 400 Spotify would give. Anything undocumented on either
side -- including a call to an endpoint the fake does not serve, such as one
Spotify has removed -- is recorded in ``violations`` (GET /_fake/violations):
the tests and ``scripts/harness.sh`` fail on a non-empty list.
"""

import ipaddress
import json
import re
from dataclasses import asdict
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Flask, Response, current_app, g, jsonify, redirect, render_template_string, request
from markupsafe import escape

from . import fields as fields_filter
from . import shapes, tokens
from .faults import SERVER_ERRORS, Faults
from .render import Renderer
from .store import Store

# The app's harness credentials (config.py). Real Spotify never issued these,
# so no token minted here can open anything real.
CLIENT_ID = "harness-fake-client-id"
CLIENT_SECRET = "harness-fake-client-secret"
TOKEN_LIFETIME = 3600
CODE_LIFETIME = 600

# Documented in the endpoints' reference text rather than the schema.
MAX_WRITE_ITEMS = 100  # POST/PUT/DELETE /playlists/{id}/items
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def create_app(redirect_uris: Iterable[str] = (), origin: Optional[str] = None) -> Flask:
    """``origin`` is where links point (python -m harness.fake_spotify sets it).
    Left unset -- the tests' clients -- links use the request's host, which
    must then be a loopback one."""
    app = Flask(__name__)
    app.config.update(STORE=Store(), REDIRECT_URIS=frozenset(redirect_uris), ORIGIN=origin, VIOLATIONS=[],
                      FAULTS=Faults())
    app.register_error_handler(APIError, _api_error)
    app.register_error_handler(OAuthError, lambda e: (jsonify(error=e.code, error_description=e.description), 400))
    app.before_request(_check_request)
    app.after_request(_check_response)
    _register_accounts(app)
    _register_api(app)
    _register_web(app)
    _register_control(app)
    return app


class APIError(Exception):
    """A Web API error, answered in Spotify's {"error": {status, message}} shape,
    with the quota 429's ``reason`` when there is one."""

    def __init__(self, status: int, message: str, reason: Optional[str] = None, headers: Optional[dict] = None):
        self.status, self.message, self.reason, self.headers = status, message, reason, headers or {}


def _api_error(e: APIError):
    error = {"status": e.status, "message": e.message}
    if e.reason:
        error["reason"] = e.reason
    return jsonify(error=error), e.status, e.headers


class OAuthError(Exception):
    def __init__(self, code: str, description: str):
        self.code, self.description = code, description


def _store() -> Store:
    return current_app.config["STORE"]


def _origin() -> str:
    """Where every link the fake writes points: its fixed origin, or else the
    request's host when that is a loopback one."""
    origin = current_app.config["ORIGIN"]
    if origin is None:
        if (urlsplit(request.host_url).hostname or "") not in _LOOPBACK_HOSTS:
            raise APIError(400, "the fake Spotify serves loopback hosts only")
        origin = request.host_url
    return origin.rstrip("/")


def _renderer() -> Renderer:
    return Renderer(_origin(), _store())


def _record(problem: str) -> None:
    current_app.logger.warning("fake Spotify contract: %s", problem)
    current_app.config["VIOLATIONS"].append(problem)


def _operation() -> Optional[str]:
    """``"GET /playlists/{playlist_id}"`` for an operation the fake serves,
    else None (not /v1, or a path or method it does not serve)."""
    rule = request.url_rule
    if rule is None or not rule.rule.startswith("/v1/"):
        return None
    path = rule.rule[3:]
    for name in rule.arguments:
        path = path.replace(f"<{name}>", f"{{{name}}}")
    operation = f"{request.method} {path}"
    return operation if operation in shapes.SHAPES["operations"] else None


# ---------------------------------------------------------------------------
# Checking every request and response against Spotify's documentation
# ---------------------------------------------------------------------------


def _check_request():
    if not request.path.startswith("/v1/"):
        return None
    operation = _operation()
    if operation is None:
        # Not a call the fake serves -- an endpoint Spotify removed, a typo,
        # or a method the operation does not take. Recorded, so a client
        # that swallows the error still fails the run.
        _record(f"{request.method} {request.path}: not an operation the fake serves")
        raise APIError(404, "Service not found")
    g.user, g.scopes = _authenticated_user()
    body = None
    raw = request.get_data()
    if raw:
        if request.mimetype != "application/json":
            _record(f"{operation} request: a {request.mimetype or 'untyped'} body, not application/json")
            raise APIError(400, "Request body must be JSON")
        try:
            body = json.loads(raw)
        except ValueError:
            raise APIError(400, "Error parsing JSON.")
    for name in request.args:
        if len(request.args.getlist(name)) > 1:
            _record(f"{operation} request: ?{name} given more than once")
    invalid, undocumented = shapes.request_problems(operation, request.args.to_dict(), body)
    for problem in undocumented:
        _record(f"{operation} request: {problem}")
    if invalid:
        raise APIError(400, "; ".join(invalid))
    g.body = body if isinstance(body, dict) else {}
    fault = current_app.config["FAULTS"].take(operation, request.view_args.get("playlist_id"))
    if fault is not None:
        _answer_with(fault)
    return None


def _hold(fault) -> None:
    """Answer late: after the fault's delay, or at once when it is released
    (POST /_fake/faults/release). Waits on the fault's Event, not time.sleep,
    which the client tests replace to record the client's own backoffs."""
    fault.released.wait(fault.delay)


def _answer_with(fault) -> None:
    """Answer the call as an armed fault says (faults.py)."""
    if fault.kind == "slow":
        _hold(fault)  # then served as usual
        return
    if fault.kind == "rate_limit":
        headers = {} if fault.retry_after is None else {"Retry-After": str(fault.retry_after)}
        raise APIError(429, "API rate limit exceeded", headers=headers)
    if fault.kind == "quota":
        # Spotify's documented quota-exceeded body, word for word.
        raise APIError(429, "Too many requests", reason="QUOTA_EXCEEDED")
    raise APIError(fault.status, SERVER_ERRORS[fault.status])


def _check_response(response: Response) -> Response:
    operation = _operation()
    if operation is not None:
        body = response.get_json(silent=True) if response.data else None
        for problem in shapes.response_problems(operation, response.status_code, body,
                                                has_body=bool(response.data),
                                                partial="fields" in request.args):
            _record(f"{operation} {response.status_code} response: {problem}")
    return response


def _authenticated_user():
    """The token's user and the scopes it was granted."""
    auth = request.authorization
    if auth is None or auth.type != "bearer":
        raise APIError(401, "No token provided")
    claims = tokens.read(auth.token, "access")
    if not claims or claims.get("user") not in _store().users:
        raise APIError(401, "Invalid access token")
    if tokens.expired(claims):
        raise APIError(401, "The access token expired")
    return _store().users[claims["user"]], set(claims["scope"].split())


def _granted(scope: str) -> bool:
    """Whether the token holds ``scope``, which must be one Spotify lists for
    this operation (shapes.json) -- the fake applies no scope of its own."""
    assert scope in shapes.SHAPES["operations"][_operation()]["scopes"], scope
    return scope in g.scopes


def _require_modify_scope(public: bool) -> None:
    # The scope descriptions: playlist-modify-public "Manage your public
    # playlists", playlist-modify-private "Manage your private playlists".
    # Create Playlist: "To be able to create private playlists, the user must
    # have granted the playlist-modify-private scope."
    if not _granted("playlist-modify-public" if public else "playlist-modify-private"):
        raise APIError(403, "Insufficient client scope")


# ---------------------------------------------------------------------------
# accounts.spotify.com
# ---------------------------------------------------------------------------

_AUTHORIZE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Fake Spotify: log in</title>
<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem}
a.user{display:block;padding:.75rem 1rem;margin:.5rem 0;border:1px solid #ccc;border-radius:.5rem;text-decoration:none}
</style></head><body>
<h1>Fake Spotify</h1>
<p>The local test harness's stand-in for Spotify's login page. Pick a seeded user;
nothing here is a real account.</p>
{% for user in users %}<a class="user" data-user="{{ user.id }}" href="{{ approve(user.id) }}">{{ user.display_name }}
<small>({{ user.id }})</small></a>{% endfor %}
<p><a href="{{ deny }}">Cancel</a></p>
</body></html>"""


def _with_query(url: str, **params) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True) + list(params.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


def _register_accounts(app: Flask) -> None:
    @app.get("/authorize")
    def authorize():
        args = request.args
        if args.get("client_id") != CLIENT_ID:
            return "INVALID_CLIENT: Invalid client", 400
        redirect_uri = args.get("redirect_uri", "")
        if redirect_uri not in app.config["REDIRECT_URIS"] or not _allowed_redirect_uri(redirect_uri):
            return "INVALID_CLIENT: Invalid redirect URI", 400
        if args.get("response_type") != "code":
            return "unsupported_response_type: response_type must be code", 400
        state = {"state": args["state"]} if "state" in args else {}

        if args.get("deny"):
            return redirect(_with_query(redirect_uri, error="access_denied", **state))
        user_id = args.get("fake_user")
        if user_id is None:
            here = _origin() + request.full_path
            return render_template_string(
                _AUTHORIZE_PAGE,
                users=_store().users.values(),
                approve=lambda uid: _with_query(here, fake_user=uid),
                deny=_with_query(here, deny="1"),
            )
        if user_id not in _store().users:
            return f"Unknown fake user {escape(user_id)}", 400
        code = tokens.mint("code", CODE_LIFETIME, user=user_id, scope=args.get("scope", ""), redirect_uri=redirect_uri)
        return redirect(_with_query(redirect_uri, code=code, **state))

    @app.post("/api/token")
    def token():
        # Spotify also takes the credentials in the form; shuffify sends Basic.
        auth = request.authorization
        if auth is None or auth.type != "basic" or (auth.username, auth.password) != (CLIENT_ID, CLIENT_SECRET):
            raise OAuthError("invalid_client", "Invalid client")
        grant = request.form.get("grant_type")

        if grant == "authorization_code":
            code = request.form.get("code", "")
            claims = tokens.read(code, "code")
            store = _store()
            with store.lock:
                if not claims or tokens.expired(claims) or code in store.used_codes:
                    raise OAuthError("invalid_grant", "Invalid authorization code")
                if claims["redirect_uri"] != request.form.get("redirect_uri"):
                    raise OAuthError("invalid_grant", "Invalid redirect URI")
                store.used_codes.add(code)
            return jsonify(**_access_token(claims), refresh_token=tokens.mint("refresh", user=claims["user"],
                                                                               scope=claims["scope"]))

        if grant == "refresh_token":
            claims = tokens.read(request.form.get("refresh_token", ""), "refresh")
            if not claims:
                raise OAuthError("invalid_grant", "Invalid refresh token")
            # Spotify "might" return a new refresh token; this fake does not,
            # so the client's keep-the-old-one path is the one exercised.
            return jsonify(**_access_token(claims))

        raise OAuthError("unsupported_grant_type", f"grant_type {grant!r} is not supported")

    # No /api/revoke: Spotify does not implement token revocation, so the
    # app's best-effort call gets a 404 here as it does there.


def _allowed_redirect_uri(uri: str) -> bool:
    """Spotify's rule: HTTPS, or HTTP on a loopback IP literal. ``localhost``
    is refused either way."""
    parts = urlsplit(uri)
    host = parts.hostname or ""
    if host == "localhost":
        return False
    if parts.scheme == "https":
        return True
    try:
        return parts.scheme == "http" and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _access_token(claims: dict) -> dict:
    return {
        "access_token": tokens.mint("access", TOKEN_LIFETIME, user=claims["user"], scope=claims["scope"]),
        "token_type": "Bearer",
        "scope": claims["scope"],
        "expires_in": TOKEN_LIFETIME,
    }


# ---------------------------------------------------------------------------
# api.spotify.com/v1
# ---------------------------------------------------------------------------


def _query_int(name: str) -> int:
    """A documented integer parameter, already validated, or its default."""
    rule = shapes.SHAPES["operations"][_operation()]["query"][name]
    return int(request.args.get(name, rule.get("default", 0)))


def _filtered(payload: dict):
    expr = request.args.get("fields")
    if not expr:
        return jsonify(payload)
    try:
        return jsonify(fields_filter.apply(payload, fields_filter.parse(expr)))
    except fields_filter.FieldsError as e:
        raise APIError(400, str(e))


def _playlist(playlist_id: str, allowed: Optional[Callable] = None, write: bool = False):
    """The playlist, or the error Spotify gives this user for it. ``allowed``
    is a further permission (a Store predicate); ``write`` needs the modify
    scope for the playlist's visibility."""
    store = _store()
    playlist = store.playlists.get(playlist_id)
    if playlist is None or not store.can_see(playlist, g.user.id):
        raise APIError(404, "Resource not found")
    if allowed is not None and not allowed(playlist, g.user.id):
        raise APIError(403, "Forbidden")
    if write:
        _require_modify_scope(playlist.public)
    return playlist


def _is_owner(playlist, user_id: str) -> bool:
    return playlist.owner == user_id


def _check_uris(uris) -> None:
    if len(uris) > MAX_WRITE_ITEMS:
        raise APIError(400, f"You can add a maximum of {MAX_WRITE_ITEMS} tracks per request.")
    for uri in uris:
        if not uri.startswith("spotify:track:"):
            raise APIError(400, f"Unsupported URL / URI: {uri}")
        if _store().track(uri) is None:
            raise APIError(400, "Payload contains a non-existing ID")


def _refuse_collaborative() -> None:
    # Documented, but shuffify never sends it, so the fake does not model it.
    if "collaborative" in g.body:
        raise APIError(400, "the fake does not model collaborative")


_BASE62_ID = re.compile(r"[0-9A-Za-z]{22}")


def _register_api(app: Flask) -> None:
    @app.get("/v1/me")
    def me():
        return jsonify(_renderer().me(g.user))

    @app.get("/v1/me/playlists")
    def my_playlists():
        limit, offset = _query_int("limit"), _query_int("offset")
        r = _renderer()
        library = _store().playlists_for(g.user.id)
        # "Authorization scopes: playlist-read-private -- Access your private playlists."
        if not _granted("playlist-read-private"):
            library = [p for p in library if p.public]
        items = [r.simplified_playlist(p) for p in library[offset:offset + limit]]
        return jsonify(r.page(f"{r.api}/me/playlists", items, offset, limit, len(library)))

    @app.post("/v1/me/playlists")
    def create_playlist():
        _refuse_collaborative()
        public = g.body.get("public", True)
        _require_modify_scope(public)
        playlist = _store().create_playlist(
            owner=g.user.id, name=g.body["name"], public=public, description=g.body.get("description", ""))
        return jsonify(_renderer().playlist(playlist, with_items=True)), 201

    @app.get("/v1/playlists/<playlist_id>")
    def get_playlist(playlist_id):
        playlist = _playlist(playlist_id)
        return _filtered(_renderer().playlist(playlist, _store().can_read_items(playlist, g.user.id)))

    @app.put("/v1/playlists/<playlist_id>")
    def change_details(playlist_id):
        playlist = _playlist(playlist_id, _is_owner, write=True)
        _refuse_collaborative()
        with _store().lock:
            for key in ("name", "description", "public"):
                if key in g.body:
                    setattr(playlist, key, g.body[key])
        # Documented as 200 "Playlist updated" with no body.
        return Response(status=200)

    @app.get("/v1/playlists/<playlist_id>/items")
    def get_items(playlist_id):
        playlist = _playlist(playlist_id, Store.can_read_items)
        return _filtered(_renderer().items_page(playlist, _query_int("offset"), _query_int("limit")))

    @app.post("/v1/playlists/<playlist_id>/items")
    def add_items(playlist_id):
        playlist = _playlist(playlist_id, Store.can_edit, write=True)
        uris = g.body.get("uris")
        if uris is None:
            raise APIError(400, "No uris provided")
        _check_uris(uris)
        position = g.body.get("position")
        if position is not None and not 0 <= position <= len(playlist.entries):
            raise APIError(400, "Index out of bounds")
        _store().add(playlist, uris, position, g.user.id)
        return jsonify(snapshot_id=playlist.snapshot_id), 201

    @app.put("/v1/playlists/<playlist_id>/items")
    def replace_items(playlist_id):
        playlist = _playlist(playlist_id, Store.can_edit, write=True)
        uris = g.body.get("uris")
        if uris is None:
            # Reordering (range_start/insert_before) is documented, but shuffify
            # never sends it, so the fake does not model it.
            raise APIError(400, "uris is required (the fake does not model reordering)")
        _check_uris(uris)
        _store().replace(playlist, uris, g.user.id)
        return jsonify(snapshot_id=playlist.snapshot_id)

    @app.delete("/v1/playlists/<playlist_id>/items")
    def remove_items(playlist_id):
        playlist = _playlist(playlist_id, Store.can_edit, write=True)
        items = g.body["items"]
        if len(items) > MAX_WRITE_ITEMS:
            raise APIError(400, f"You can remove a maximum of {MAX_WRITE_ITEMS} tracks per request.")
        if not all(isinstance(i.get("uri"), str) for i in items):
            raise APIError(400, "Every item needs a uri")
        _store().remove(playlist, [i["uri"] for i in items])
        return jsonify(snapshot_id=playlist.snapshot_id)

    @app.get("/v1/tracks/<id>")  # named as in Spotify's schema, as every route parameter is
    def track(id):
        if not _BASE62_ID.fullmatch(id):
            raise APIError(400, "Invalid base62 id")
        found = _store().track(f"spotify:track:{id}")
        if found is None:
            raise APIError(404, "Resource not found")
        return jsonify(_renderer().track(found))

    @app.get("/v1/audio-features")
    def audio_features():
        # Spotify restricts this endpoint for apps in Development Mode and
        # answers them 403, as the fake does here.
        raise APIError(403, "Forbidden")

    @app.get("/v1/search")
    def search():
        # One type per request, track or playlist: all shuffify sends.
        kind = request.args["type"]
        if kind not in ("track", "playlist"):
            raise APIError(400, f"the fake does not model searching for {kind}")
        words = _search_words(request.args["q"])
        limit, offset = _query_int("limit"), _query_int("offset")
        r = _renderer()
        if kind == "track":
            found, render = _search_tracks(words), r.track
        else:
            found, render = _search_playlists(words), r.simplified_playlist
        return jsonify({f"{kind}s": r.page(f"{r.api}/search", [render(m) for m in found[offset:offset + limit]],
                                           offset, limit, len(found), query={"q": request.args["q"], "type": kind})})


def _search_words(query: str):
    """Lower-cased words, with quotes and field filters (artist:...) dropped."""
    words = (word.rpartition(":")[2] for word in query.replace('"', " ").lower().split())
    return [w for w in words if w]


def _search_tracks(words):
    def text(t):
        return " ".join([t.name, t.album.name, *(a.name for a in t.artists)]).lower()

    return [t for t in _store().catalog if all(w in text(t) for w in words)]


def _search_playlists(words):
    return [p for p in _store().playlists.values() if p.public and all(w in p.name.lower() for w in words)]


# ---------------------------------------------------------------------------
# open.spotify.com: the public pages shuffify's scraper reads
# ---------------------------------------------------------------------------
# Undocumented by Spotify. The embed page carries the structure shuffify's
# scraper documents (a __NEXT_DATA__ script with entity.trackList); the
# live integration tests check the real pages separately.


def _public_playlist(playlist_id: str):
    playlist = _store().playlists.get(playlist_id)
    if playlist is None or not playlist.public:
        raise APIError(404, "Not found")
    return playlist


def _register_web(app: Flask) -> None:
    @app.get("/embed/playlist/<playlist_id>")
    def embed(playlist_id):
        playlist = _public_playlist(playlist_id)
        store = _store()
        rows = []
        for i, entry in enumerate(playlist.entries):
            track = store.track(entry.uri)
            rows.append({"uri": entry.uri, "uid": f"{i:016x}", "title": track.name,
                         "subtitle": track.artists[0].name, "duration": track.duration_ms})
        entity = {
            "type": "playlist",
            "id": playlist.id,
            "uri": f"spotify:playlist:{playlist.id}",
            "name": playlist.name,
            "description": playlist.description,
            "subtitle": store.users[playlist.owner].display_name,
            "trackList": rows,
        }
        # No "<" at all inside the script element: JSON's \\u003c escape keeps
        # a name from closing it or tripping the parser's escaped-script states.
        blob = json.dumps({"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}).replace("<", "\\u003c")
        return _html(playlist.name, f'<script id="__NEXT_DATA__" type="application/json">{blob}</script>')

    @app.get("/playlist/<playlist_id>")
    def public_page(playlist_id):
        playlist = _public_playlist(playlist_id)
        rows = "".join(
            f'<li><a href="/track/{escape(e.uri.rsplit(":", 1)[1])}">{escape(_store().track(e.uri).name)}</a></li>'
            for e in playlist.entries
        )
        return _html(playlist.name, f"<h1>{escape(playlist.name)}</h1><ol>{rows}</ol>")


def _html(title: str, body: str) -> Response:
    return Response(
        f'<!doctype html><html><head><meta property="og:title" content="{escape(title)}">'
        f"<title>{escape(title)}</title></head><body>{body}</body></html>",
        mimetype="text/html",
    )


# ---------------------------------------------------------------------------
# /_fake: for tests and the harness, not part of Spotify
# ---------------------------------------------------------------------------


def _register_control(app: Flask) -> None:
    @app.get("/_fake/health")
    def health():
        return jsonify(status="ok")

    @app.post("/_fake/reset")
    def reset():
        _store().reset()
        current_app.config["FAULTS"].clear()
        return jsonify(status="reset")

    @app.post("/_fake/faults")
    def arm_fault():
        try:
            fault = current_app.config["FAULTS"].arm(request.get_json(silent=True))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(asdict(fault)), 201

    @app.get("/_fake/faults")
    def faults():
        return jsonify(current_app.config["FAULTS"].state())

    @app.delete("/_fake/faults")
    def clear_faults():
        current_app.config["FAULTS"].clear()
        return jsonify(status="cleared")

    @app.post("/_fake/faults/release")
    def release_faults():
        current_app.config["FAULTS"].release()
        return jsonify(status="released")

    @app.get("/_fake/violations")
    def violations():
        return jsonify(current_app.config["VIOLATIONS"])

    @app.get("/_fake/users")
    def users():
        return jsonify([{"id": u.id, "display_name": u.display_name} for u in _store().users.values()])

    @app.get("/_fake/playlists")
    def playlists():
        return jsonify([_playlist_state(p) for p in _store().playlists.values()])

    @app.get("/_fake/playlists/<playlist_id>")
    def playlist_state(playlist_id):
        playlist = _store().playlists.get(playlist_id)
        if playlist is None:
            return jsonify(error="no such playlist"), 404
        return jsonify(_playlist_state(playlist))


def _playlist_state(playlist) -> dict:
    return {
        "id": playlist.id,
        "name": playlist.name,
        "owner": playlist.owner,
        "public": playlist.public,
        "collaborative": playlist.collaborative,
        "collaborators": sorted(playlist.collaborators),
        "description": playlist.description,
        "snapshot_id": playlist.snapshot_id,
        "uris": playlist.uris,
    }
