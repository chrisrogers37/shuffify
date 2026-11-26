"""The session never holds a Spotify refresh token: the users row is its only home.

The session keeps the access token and its expiry, a cache. A request
without a usable one gets one from the stored refresh token, and a person
without a stored token is signed out.
"""

import json

import pytest
import requests
from flask import session
from sqlalchemy.exc import OperationalError

from shuffify.models.db import User, db
from shuffify.routes import require_auth
from shuffify.services import credential_refresher
from shuffify.services.token_service import TokenService
from tests.paths import api_path

REFRESH_KEYS = {"refresh_token", "encrypted_refresh_token"}
EXPIRED = {"access_token": "expired-5d41", "token_type": "Bearer", "expires_at": 0}


@pytest.fixture
def spotify_app(db_app):
    """The app with Spotify credentials configured, so a client can be built."""
    db_app.config.update(
        SPOTIFY_CLIENT_ID="client-5d41",
        SPOTIFY_CLIENT_SECRET="secret-5d41",
        SPOTIFY_REDIRECT_URI="http://localhost:5000/callback",
    )
    credential_refresher._kept.clear()
    yield db_app
    credential_refresher._kept.clear()


def reply(status, body):
    response = requests.Response()
    response.status_code, response._content = status, json.dumps(body).encode()
    return response


class Spotify:
    """Spotify's token endpoint and Web API, answered here: every grant issues a new refresh token."""

    def __init__(self, monkeypatch):
        self.grants, self.presented = [], []
        # A token request inside a request's Spotify deadline goes through a Session, as every API call does.
        monkeypatch.setattr("shuffify.spotify.auth.requests.post", self.token)
        monkeypatch.setattr("requests.sessions.Session.request", self.request)

    def request(self, method, url, **kwargs):
        # Patched onto requests.Session as a bound method, so the session is not passed.
        return self.token(url, **kwargs) if url.endswith("/api/token") else self.web_api()

    def token(self, url, data=None, **kwargs):
        self.grants.append(data["grant_type"])
        if data["grant_type"] == "refresh_token":
            self.presented.append(data["refresh_token"])
        number = len(self.grants)
        return reply(200, {"access_token": f"access-{number}", "token_type": "Bearer", "expires_in": 3600,
                           "refresh_token": f"refresh-{number}"})

    def web_api(self):
        return reply(200, {"id": "alice", "display_name": "Alice"})


def add_user(spotify_id="alice", refresh_token="refresh-0"):
    user = User(spotify_id=spotify_id,
                encrypted_refresh_token=TokenService.encrypt_token(refresh_token) if refresh_token else None)
    db.session.add(user)
    db.session.commit()
    return user


def stored(spotify_id="alice"):
    db.session.expire_all()
    return TokenService.decrypt_token(User.query.filter_by(spotify_id=spotify_id).one().encrypted_refresh_token)


def session_of(client):
    with client.session_transaction() as sess:
        return dict(sess)


def test_sign_in_stores_the_refresh_token_in_the_users_row_only(spotify_app, monkeypatch):
    spotify = Spotify(monkeypatch)
    client = spotify_app.test_client()
    with client.session_transaction() as sess:
        sess["oauth_state"] = "state-5d41"

    response = client.get("/callback?code=code-5d41&state=state-5d41")

    assert response.status_code == 302 and spotify.grants == ["authorization_code"]
    token = session_of(client)["spotify_token"]
    assert set(token) == {"access_token", "token_type", "expires_at"} and token["access_token"] == "access-1"
    assert stored() == "refresh-1"


def test_a_refresh_writes_the_access_token_to_the_session_and_the_rotated_token_to_the_row(
    spotify_app, monkeypatch,
):
    spotify = Spotify(monkeypatch)
    with spotify_app.test_request_context():
        add_user()
        session.update(user_data={"id": "alice"}, spotify_token=EXPIRED)
        assert require_auth() is not None
        token = dict(session["spotify_token"])

    assert spotify.presented == ["refresh-0"]
    assert set(token) == {"access_token", "token_type", "expires_at"} and token["access_token"] == "access-1"
    assert stored() == "refresh-1"


@pytest.mark.parametrize("key", sorted(REFRESH_KEYS))
def test_a_live_session_that_still_carries_a_refresh_token_has_it_dropped_on_its_next_request(auth_client, key):
    with auth_client.session_transaction() as sess:
        token = {name: value for name, value in sess["spotify_token"].items() if name not in REFRESH_KEYS}
        carried = "refresh-5d41" if key == "refresh_token" else TokenService.encrypt_token("refresh-5d41")
        sess["spotify_token"] = {**token, key: carried}

    response = auth_client.get(api_path("/session"))

    assert response.status_code == 200 and response.get_json()["signed_in"] is True
    assert session_of(auth_client)["spotify_token"] == token


class TestTheRowId:
    def test_a_session_without_the_cached_row_id_refreshes_and_then_caches_it(self, spotify_app, monkeypatch):
        spotify = Spotify(monkeypatch)
        with spotify_app.test_request_context():
            user = add_user()
            session.update(user_data={"id": "alice"}, spotify_token=EXPIRED)
            assert "_db_user_id" not in session
            assert require_auth() is not None
            assert session["_db_user_id"] == user.id
        assert spotify.presented == ["refresh-0"]

    def test_a_session_whose_user_has_no_row_is_signed_out(self, spotify_app, monkeypatch):
        spotify = Spotify(monkeypatch)
        with spotify_app.test_request_context():
            session.update(user_data={"id": "nobody"}, spotify_token=EXPIRED)
            assert require_auth() is None
        assert spotify.grants == []


class TestNoAccessToken:
    """A session that names its account and holds no access token, as one signed in again elsewhere."""

    @staticmethod
    def signed_in_without_a_token(app):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_data"] = {"id": "alice", "display_name": "Alice"}
        return client

    def test_with_a_stored_token_it_loads_data_without_reconnecting(self, spotify_app, monkeypatch):
        spotify = Spotify(monkeypatch)
        add_user()
        client = self.signed_in_without_a_token(spotify_app)

        response = client.get(api_path("/activity"))

        assert response.status_code == 200, response.get_json()
        assert spotify.presented == ["refresh-0"]
        assert session_of(client)["spotify_token"]["access_token"] == "access-1"

    def test_without_a_stored_token_it_is_signed_out(self, spotify_app, monkeypatch):
        spotify = Spotify(monkeypatch)
        add_user(refresh_token=None)
        client = self.signed_in_without_a_token(spotify_app)

        response = client.get(api_path("/activity"))

        assert response.status_code == 401
        assert spotify.grants == []
        assert "spotify_token" not in session_of(client)

    def test_a_database_refusal_finding_its_row_answers_503_and_keeps_the_session(self, spotify_app, monkeypatch):
        spotify = Spotify(monkeypatch)
        add_user()

        def unavailable(spotify_id):
            raise OperationalError("SELECT users", {}, Exception("connection lost"))

        monkeypatch.setattr("shuffify.services.user_service.UserService.get_by_spotify_id", unavailable)
        client = self.signed_in_without_a_token(spotify_app)
        before = session_of(client)

        response = client.get(api_path("/activity"))

        assert response.status_code == 503, response.get_json()
        assert response.headers["Retry-After"] == str(credential_refresher.LOCK_TIMEOUT_SECONDS)
        assert session_of(client) == before
        assert spotify.grants == []


@pytest.mark.parametrize("token", [EXPIRED, None], ids=["expired-access-token", "no-access-token"])
def test_a_session_whose_stored_token_cannot_be_decrypted_is_signed_out(spotify_app, monkeypatch, token):
    spotify = Spotify(monkeypatch)
    db.session.add(User(spotify_id="alice", encrypted_refresh_token="not-a-fernet-token"))
    db.session.commit()
    with spotify_app.test_request_context():
        session["user_data"] = {"id": "alice"}
        if token is not None:
            session["spotify_token"] = token
        assert require_auth() is None
    assert spotify.grants == []


def test_a_database_refusal_answers_503_with_retry_after_and_keeps_the_session(spotify_app, monkeypatch):
    spotify = Spotify(monkeypatch)
    user = add_user()

    def unavailable(storage, user_id):
        raise OperationalError("SELECT ... FOR NO KEY UPDATE", {}, Exception("lock timeout"))

    monkeypatch.setattr(credential_refresher, "_locked_stored_token", unavailable)
    client = spotify_app.test_client()
    with client.session_transaction() as sess:
        sess.update(user_data={"id": "alice"}, _db_user_id=user.id, spotify_token=EXPIRED)
    before = session_of(client)

    response = client.get(api_path("/activity"))

    assert response.status_code == 503, response.get_json()
    assert response.headers["Retry-After"] == str(credential_refresher.LOCK_TIMEOUT_SECONDS)
    assert session_of(client) == before
    assert spotify.grants == []
