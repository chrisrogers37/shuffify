"""
Tests for configuration module.

Tests cover Config classes, environment variable handling,
and Spotify credentials retrieval.
"""

import pytest
import os
from unittest.mock import patch


class TestConfigClass:
    """Test base Config class."""

    def test_config_has_required_attributes(self):
        """Config should have all required attributes."""
        from config import Config

        assert hasattr(Config, 'SECRET_KEY')
        assert hasattr(Config, 'SESSION_COOKIE_NAME')
        assert hasattr(Config, 'SPOTIFY_CLIENT_ID')
        assert hasattr(Config, 'SPOTIFY_CLIENT_SECRET')
        assert hasattr(Config, 'SPOTIFY_REDIRECT_URI')
        assert hasattr(Config, 'SESSION_TYPE')

    def test_session_configuration(self):
        """Session settings should be properly configured."""
        from config import Config

        assert Config.SESSION_TYPE == 'filesystem'
        assert Config.SESSION_FILE_DIR == './.flask_session/'
        assert Config.SESSION_PERMANENT is False
        assert Config.PERMANENT_SESSION_LIFETIME == 3600

    def test_session_cookie_security(self):
        """Session cookie should have security settings."""
        from config import Config

        assert Config.SESSION_COOKIE_HTTPONLY is True
        assert Config.SESSION_COOKIE_SAMESITE == 'Lax'

    def test_default_port(self):
        """Default port should be 8000."""
        from config import Config

        # Clear PORT env var for this test
        with patch.dict(os.environ, {}, clear=True):
            # Re-import to get fresh defaults
            import importlib
            import config
            importlib.reload(config)

            assert config.Config.PORT == 8000

    def test_get_spotify_credentials(self):
        """get_spotify_credentials should return credential dict."""
        from config import Config

        with patch.dict(os.environ, {
            'SPOTIFY_CLIENT_ID': 'test_client_id',
            'SPOTIFY_CLIENT_SECRET': 'test_client_secret',
            'SPOTIFY_REDIRECT_URI': 'http://localhost:5000/callback'
        }):
            import importlib
            import config
            importlib.reload(config)

            creds = config.Config.get_spotify_credentials()

            assert 'client_id' in creds
            assert 'client_secret' in creds
            assert 'redirect_uri' in creds


class TestProdConfig:
    """Test production configuration."""

    def test_prod_config_exists(self):
        """ProdConfig should exist."""
        from config import ProdConfig

        assert ProdConfig is not None

    def test_prod_debug_disabled(self):
        """Production should have debug disabled."""
        from config import ProdConfig

        assert ProdConfig.DEBUG is False
        assert ProdConfig.TESTING is False

    def test_prod_secure_cookies(self):
        """Production should have secure cookies."""
        from config import ProdConfig

        assert ProdConfig.SESSION_COOKIE_SECURE is True
        assert ProdConfig.SESSION_COOKIE_HTTPONLY is True

    def test_prod_flask_env(self):
        """Production should set FLASK_ENV to production."""
        from config import ProdConfig

        assert ProdConfig.FLASK_ENV == 'production'


class TestDevConfig:
    """Test development configuration."""

    def test_dev_config_exists(self):
        """DevConfig should exist."""
        from config import DevConfig

        assert DevConfig is not None

    def test_dev_debug_enabled(self):
        """Development should have debug enabled."""
        from config import DevConfig

        assert DevConfig.DEBUG is True
        assert DevConfig.TESTING is True

    def test_dev_insecure_cookies(self):
        """Development should allow insecure cookies for localhost."""
        from config import DevConfig

        assert DevConfig.SESSION_COOKIE_SECURE is False

    def test_dev_flask_env(self):
        """Development should set FLASK_ENV to development."""
        from config import DevConfig

        assert DevConfig.FLASK_ENV == 'development'


class TestConfigDict:
    """Test config dictionary for easy selection."""

    def test_config_dict_exists(self):
        """config dict should exist for environment selection."""
        from config import config

        assert isinstance(config, dict)

    def test_config_dict_has_environments(self):
        """config dict should have development and production."""
        from config import config

        assert 'development' in config
        assert 'production' in config
        assert 'default' in config

    def test_config_dict_values(self):
        """config dict should map to correct classes."""
        from config import config, DevConfig, ProdConfig

        assert config['development'] is DevConfig
        assert config['production'] is ProdConfig
        assert config['default'] is DevConfig


class TestValidateRequiredEnvVars:
    """Test environment variable validation function."""

    def test_validate_with_all_vars_present(self):
        """Should not raise when all required vars are present."""
        from config import validate_required_env_vars

        with patch.dict(os.environ, {
            'SPOTIFY_CLIENT_ID': 'test_id',
            'SPOTIFY_CLIENT_SECRET': 'test_secret'
        }):
            # Should not raise
            validate_required_env_vars()

    def test_validate_missing_client_id(self):
        """Should raise ValueError when SPOTIFY_CLIENT_ID is missing."""
        from config import validate_required_env_vars

        with patch.dict(os.environ, {'SPOTIFY_CLIENT_SECRET': 'test_secret'}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                validate_required_env_vars()

            assert 'SPOTIFY_CLIENT_ID' in str(exc_info.value)

    def test_validate_missing_client_secret(self):
        """Should raise ValueError when SPOTIFY_CLIENT_SECRET is missing."""
        from config import validate_required_env_vars

        with patch.dict(os.environ, {'SPOTIFY_CLIENT_ID': 'test_id'}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                validate_required_env_vars()

            assert 'SPOTIFY_CLIENT_SECRET' in str(exc_info.value)

    def test_validate_missing_both(self):
        """Should raise ValueError listing both missing vars."""
        from config import validate_required_env_vars

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                validate_required_env_vars()

            error_msg = str(exc_info.value)
            assert 'SPOTIFY_CLIENT_ID' in error_msg
            assert 'SPOTIFY_CLIENT_SECRET' in error_msg


class TestEnvironmentVariableHandling:
    """Test how config handles environment variables."""

    def test_secret_key_default(self):
        """SECRET_KEY should have a default for development."""
        with patch.dict(os.environ, {}, clear=True):
            import importlib
            import config
            importlib.reload(config)

            # Should have a default value
            assert config.Config.SECRET_KEY is not None
            assert len(config.Config.SECRET_KEY) > 0

    def test_secret_key_from_env(self):
        """SECRET_KEY should be read from environment."""
        with patch.dict(os.environ, {'SECRET_KEY': 'my-custom-secret'}):
            import importlib
            import config
            importlib.reload(config)

            assert config.Config.SECRET_KEY == 'my-custom-secret'

    def test_redirect_uri_default(self):
        """SPOTIFY_REDIRECT_URI should have a default."""
        with patch.dict(os.environ, {}, clear=True):
            import importlib
            import config
            importlib.reload(config)

            assert config.Config.SPOTIFY_REDIRECT_URI == 'http://localhost:8000/callback'

    def test_port_from_env(self):
        """PORT should be read from environment as integer."""
        with patch.dict(os.environ, {'PORT': '9000'}):
            import importlib
            import config
            importlib.reload(config)

            assert config.Config.PORT == 9000
            assert isinstance(config.Config.PORT, int)

    def test_host_default(self):
        """HOST should default to 0.0.0.0."""
        with patch.dict(os.environ, {}, clear=True):
            import importlib
            import config
            importlib.reload(config)

            assert config.Config.HOST == '0.0.0.0'


class TestConfigInheritance:
    """Test that child configs inherit from base Config."""

    def test_dev_inherits_from_config(self):
        """DevConfig should inherit from Config."""
        from config import Config, DevConfig

        assert issubclass(DevConfig, Config)

    def test_prod_inherits_from_config(self):
        """ProdConfig should inherit from Config."""
        from config import Config, ProdConfig

        assert issubclass(ProdConfig, Config)

    def test_dev_overrides_debug(self):
        """DevConfig should override DEBUG setting."""
        from config import Config, DevConfig

        # Base Config has DEBUG = False, DevConfig should override to True
        assert DevConfig.DEBUG is True

    def test_prod_session_settings(self):
        """ProdConfig should have stricter session settings."""
        from config import ProdConfig

        assert ProdConfig.SESSION_COOKIE_SECURE is True
        assert ProdConfig.SESSION_COOKIE_HTTPONLY is True
        assert ProdConfig.SESSION_COOKIE_SAMESITE == 'Lax'
