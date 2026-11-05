"""The session keeps the Spotify refresh token encrypted; a client is built from the decrypted token."""

import time
from unittest.mock import patch

import pytest
from flask import session

from shuffify.routes import require_auth
from shuffify.services.token_service import TokenService
from tests.paths import api_path


def session_token(**fields):
    return {"access_token": "access-5d41", "token_type": "Bearer", "expires_at": time.time() + 3600, **fields}


@pytest.fixture
def spotify_app(db_app):
    """The app with Spotify credentials configured, so a client can be built."""
    db_app.config.update(
        SPOTIFY_CLIENT_ID="client-5d41",
        SPOTIFY_CLIENT_SECRET="secret-5d41",
        SPOTIFY_REDIRECT_URI="http://localhost:5000/callback",
    )
    return db_app


def test_require_auth_decrypts_session_refresh_token(spotify_app):
    with spotify_app.test_request_context():
        session["spotify_token"] = session_token(encrypted_refresh_token=TokenService.encrypt_token("refresh-5d41"))
        with patch("shuffify.services.auth_service.SpotifyAPI", autospec=True) as spotify_api:
            assert require_auth() is spotify_api.return_value

    assert spotify_api.call_args.args[0].refresh_token == "refresh-5d41"


def test_require_auth_rejects_a_session_refresh_token_it_cannot_decrypt(spotify_app):
    with spotify_app.test_request_context():
        session["spotify_token"] = session_token(encrypted_refresh_token="not-a-fernet-token")
        with patch("shuffify.services.auth_service.SpotifyAPI", autospec=True):
            assert require_auth() is None


def test_plaintext_session_token_is_encrypted_on_next_request(auth_client):
    response = auth_client.get(api_path("/session"))

    assert response.status_code == 200
    assert response.get_json()["signed_in"] is True
    with auth_client.session_transaction() as sess:
        stored = sess["spotify_token"]
    assert "refresh_token" not in stored
    assert TokenService.decrypt_token(stored["encrypted_refresh_token"]) == "test_refresh"
