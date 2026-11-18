"""A sign-in writes no personal data to the logs.

The sign-in runs the real callback, user, token and login history code. Only
Spotify's token and profile endpoints are stood in for.
"""

import time
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from shuffify.models.db import LoginHistory
from shuffify.services.token_service import TokenService
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo

CLIENT_IP = "203.0.113.77"
SPOTIFY_ID = "spotify-user-5d41"
DISPLAY_NAME = "Marker Display Name 5d41"


@pytest.fixture
def token_encryption():
    """A sign-in stores the refresh token only while encryption is set up."""
    TokenService.reset()
    TokenService.initialize(Fernet.generate_key().decode())
    yield
    TokenService.reset()


@pytest.fixture
def spotify():
    """Spotify's token and profile endpoints."""
    token = TokenInfo(
        access_token="access-5d41",
        token_type="Bearer",
        expires_at=time.time() + 3600,
        refresh_token="refresh-5d41",
    )
    with (
        patch.object(SpotifyAuthManager, "exchange_code", autospec=True, return_value=token),
        patch("shuffify.spotify.api.SpotifyHTTPClient", autospec=True) as http,
    ):
        http.return_value.get.return_value = {
            "id": SPOTIFY_ID,
            "display_name": DISPLAY_NAME,
            "images": [],
        }
        yield


def _sign_in(client):
    with client.session_transaction() as sess:
        sess["oauth_state"] = "state-5d41"
    return client.get(
        "/callback?code=code-5d41&state=state-5d41",
        environ_base={"REMOTE_ADDR": CLIENT_IP},
    )


def test_a_sign_in_logs_no_ip_address_spotify_id_or_display_name(
    db_app, token_encryption, spotify, shuffify_logs
):
    with db_app.test_client() as client:
        assert _sign_in(client).status_code == 302  # creates the user
        assert _sign_in(client).status_code == 302  # updates the user

    # The lines that carried personal data still run.
    for line in (
        "Retrieved user",
        "Created new user",
        "Updated existing user",
        "Success: upsert user",
        "Stored encrypted refresh token for user",
        "Success: store refresh token for user",
        "Success: record login for user_id=",
        "authenticated successfully",
    ):
        assert line in shuffify_logs.text
    for value in (CLIENT_IP, SPOTIFY_ID, DISPLAY_NAME):
        assert value not in shuffify_logs.text
    # Login history keeps the address in its table.
    assert [row.ip_address for row in LoginHistory.query.all()] == [CLIENT_IP, CLIENT_IP]
