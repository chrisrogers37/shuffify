"""
Tests for SpotifyCredentials.

Tests cover credential creation, validation, and factory methods.
"""

import pytest
import os
from unittest.mock import patch, MagicMock

from shuffify.spotify.credentials import SpotifyCredentials


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


class TestSpotifyCredentialsFromEnv:
    """Tests for from_env factory method."""

    def test_from_env_success(self):
        """Should create credentials from environment variables."""
        env_vars = {
            'SPOTIFY_CLIENT_ID': 'env_client_id',
            'SPOTIFY_CLIENT_SECRET': 'env_secret',
            'SPOTIFY_REDIRECT_URI': 'http://env/callback'
        }

        with patch.dict(os.environ, env_vars, clear=False):
            creds = SpotifyCredentials.from_env()

        assert creds.client_id == 'env_client_id'
        assert creds.client_secret == 'env_secret'
        assert creds.redirect_uri == 'http://env/callback'

    def test_from_env_missing_vars(self):
        """Should raise ValueError when env vars are missing."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError):
                SpotifyCredentials.from_env()


class TestSpotifyCredentialsToDict:
    """Tests for to_dict method."""

    def test_to_dict_returns_correct_keys(self):
        """Should return dict with correct keys."""
        creds = SpotifyCredentials(
            client_id='test',
            client_secret='secret',
            redirect_uri='http://localhost/callback'
        )

        result = creds.to_dict()

        assert 'client_id' in result
        assert 'client_secret' in result
        assert 'redirect_uri' in result
        assert result['client_id'] == 'test'
        assert result['client_secret'] == 'secret'
        assert result['redirect_uri'] == 'http://localhost/callback'

    def test_to_dict_returns_new_dict(self):
        """Should return a new dict each time."""
        creds = SpotifyCredentials(
            client_id='test',
            client_secret='secret',
            redirect_uri='http://localhost/callback'
        )

        dict1 = creds.to_dict()
        dict2 = creds.to_dict()

        assert dict1 is not dict2
        assert dict1 == dict2
