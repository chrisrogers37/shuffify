"""
Tests for SpotifyCredentials.

Tests cover credential creation, validation, and credentials_for.
"""

import os
from unittest.mock import patch

import pytest

from shuffify.spotify.credentials import SpotifyCredentials, credentials_for
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyAppUnavailable,
    SpotifyAuthError,
    SpotifyError,
)


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


class TestThereIsNoEnvironmentFallback:
    """The environment is not a source of credentials: only credentials_for
    builds them, from the Flask config."""

    def test_from_env_is_gone(self):
        assert not hasattr(SpotifyCredentials, "from_env")

    def test_the_environment_is_not_read_without_an_app_context(self):
        env_vars = {
            'SPOTIFY_CLIENT_ID': 'env_client_id',
            'SPOTIFY_CLIENT_SECRET': 'env_secret',
            'SPOTIFY_REDIRECT_URI': 'http://env/callback'
        }

        with patch.dict(os.environ, env_vars, clear=False):
            with pytest.raises(RuntimeError):
                credentials_for(None)


class TestCredentialsFor:
    """credentials_for is the one place credentials come from."""

    @staticmethod
    def _config(app, **values):
        app.config.update(
            SPOTIFY_CLIENT_ID='cfg_client_id',
            SPOTIFY_CLIENT_SECRET='cfg_secret',
            SPOTIFY_REDIRECT_URI='http://cfg/callback',
        )
        app.config.update(values)

    def test_it_returns_the_apps_credentials_from_the_flask_config(self, app):
        self._config(app)

        with app.app_context():
            creds = credentials_for(None)

        assert creds == SpotifyCredentials('cfg_client_id', 'cfg_secret', 'http://cfg/callback')

    def test_the_owner_does_not_change_them_yet(self, app):
        self._config(app)

        with app.app_context():
            assert credentials_for(object()) == credentials_for(None)

    @pytest.mark.parametrize('missing', ['SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET'])
    @pytest.mark.parametrize('empty', ['', None])
    def test_an_empty_id_or_secret_is_unavailable(self, app, missing, empty):
        self._config(app, **{missing: empty})

        with app.app_context():
            with pytest.raises(SpotifyAppUnavailable):
                credentials_for(None)

    def test_empty_config_is_unavailable(self, app):
        self._config(app, SPOTIFY_CLIENT_ID='', SPOTIFY_CLIENT_SECRET='', SPOTIFY_REDIRECT_URI='')

        with app.app_context():
            with pytest.raises(SpotifyAppUnavailable):
                credentials_for(None)

    def test_a_missing_key_is_unavailable(self, app):
        self._config(app)
        del app.config['SPOTIFY_CLIENT_SECRET']

        with app.app_context():
            with pytest.raises(SpotifyAppUnavailable):
                credentials_for(None)

    def test_an_empty_callback_url_is_unavailable_too(self, app):
        self._config(app, SPOTIFY_REDIRECT_URI='')

        with app.app_context():
            with pytest.raises(SpotifyAppUnavailable) as caught:
                credentials_for(None)

        assert isinstance(caught.value.__cause__, ValueError)

    def test_the_error_does_not_carry_the_secret(self, app):
        self._config(app, SPOTIFY_CLIENT_ID='')

        with app.app_context():
            with pytest.raises(SpotifyAppUnavailable) as caught:
                credentials_for(None)

        assert 'cfg_secret' not in str(caught.value)

    def test_it_is_a_spotify_error_but_not_an_auth_or_token_failure(self):
        assert issubclass(SpotifyAppUnavailable, SpotifyError)
        assert not issubclass(SpotifyAppUnavailable, SpotifyAuthError)
        assert not issubclass(SpotifyAppUnavailable, SpotifyAPIError)

    def test_only_credentials_for_builds_credentials_in_the_app(self):
        """Nothing outside credentials.py builds credentials or reads them from the environment."""
        from pathlib import Path

        package = Path(__file__).resolve().parents[2] / 'shuffify'
        offenders = [
            str(path.relative_to(package))
            for path in package.rglob('*.py')
            if path != package / 'spotify' / 'credentials.py'
            and any(needle in path.read_text() for needle in ('from_env', 'SpotifyCredentials('))
        ]

        assert offenders == []
