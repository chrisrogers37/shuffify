"""
Tests for SpotifyCredentials.

Tests cover credential creation, validation, and factory methods, and
credentials_for, which every caller gets them from.
"""

import pytest

from shuffify.spotify.credentials import SpotifyCredentials, credentials_for
from shuffify.spotify.exceptions import SpotifyAppUnavailable


class TestSpotifyCredentialsInit:
    """Tests for SpotifyCredentials initialization."""

    def test_create_with_valid_credentials(self):
        """Should create credentials with valid inputs."""
        creds = SpotifyCredentials(
            client_id='test_client_id',
            client_secret='test_client_secret',
            redirect_uri='http://localhost:5000/callback'
        )

        assert creds.client_id == 'test_client_id'
        assert creds.client_secret == 'test_client_secret'
        assert creds.redirect_uri == 'http://localhost:5000/callback'

    def test_credentials_are_immutable(self):
        """Credentials should be frozen (immutable)."""
        creds = SpotifyCredentials(
            client_id='test',
            client_secret='secret',
            redirect_uri='http://localhost/callback'
        )

        with pytest.raises(AttributeError):
            creds.client_id = 'new_id'

    def test_missing_client_id_raises_error(self):
        """Should raise ValueError for empty client_id."""
        with pytest.raises(ValueError) as exc_info:
            SpotifyCredentials(
                client_id='',
                client_secret='secret',
                redirect_uri='http://localhost/callback'
            )
        assert 'client_id is required' in str(exc_info.value)

    def test_missing_client_secret_raises_error(self):
        """Should raise ValueError for empty client_secret."""
        with pytest.raises(ValueError) as exc_info:
            SpotifyCredentials(
                client_id='test',
                client_secret='',
                redirect_uri='http://localhost/callback'
            )
        assert 'client_secret is required' in str(exc_info.value)

    def test_missing_redirect_uri_raises_error(self):
        """Should raise ValueError for empty redirect_uri."""
        with pytest.raises(ValueError) as exc_info:
            SpotifyCredentials(
                client_id='test',
                client_secret='secret',
                redirect_uri=''
            )
        assert 'redirect_uri is required' in str(exc_info.value)


class TestSpotifyCredentialsFromFlaskConfig:
    """Tests for from_flask_config factory method."""

    def test_from_flask_config_success(self):
        """Should create credentials from Flask config dict."""
        config = {
            'SPOTIFY_CLIENT_ID': 'flask_client_id',
            'SPOTIFY_CLIENT_SECRET': 'flask_secret',
            'SPOTIFY_REDIRECT_URI': 'http://flask/callback'
        }

        creds = SpotifyCredentials.from_flask_config(config)

        assert creds.client_id == 'flask_client_id'
        assert creds.client_secret == 'flask_secret'
        assert creds.redirect_uri == 'http://flask/callback'

    def test_from_flask_config_missing_keys(self):
        """Should raise ValueError when Flask config keys are missing."""
        config = {'SPOTIFY_CLIENT_ID': 'test'}

        with pytest.raises(ValueError):
            SpotifyCredentials.from_flask_config(config)

    def test_from_flask_config_empty_config(self):
        """Should raise ValueError for empty config."""
        with pytest.raises(ValueError):
            SpotifyCredentials.from_flask_config({})


class TestCredentialsFor:
    """credentials_for: the running app's Spotify credentials, for any owner."""

    SETTINGS = {
        "SPOTIFY_CLIENT_ID": "config_client_id",
        "SPOTIFY_CLIENT_SECRET": "config_secret",
        "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8000/callback",
    }

    def test_returns_the_credentials_in_the_apps_config(self, app):
        app.config.update(self.SETTINGS)
        with app.app_context():
            assert credentials_for(None) == credentials_for(object()) == SpotifyCredentials(
                "config_client_id", "config_secret", "http://127.0.0.1:8000/callback"
            )

    @pytest.mark.parametrize("key", sorted(SETTINGS))
    @pytest.mark.parametrize("value", ["", None])
    def test_an_unset_setting_means_there_is_no_app(self, app, key, value):
        app.config.update(self.SETTINGS, **{key: value})
        with app.app_context():
            with pytest.raises(SpotifyAppUnavailable, match="The Spotify app is not configured"):
                credentials_for(None)
