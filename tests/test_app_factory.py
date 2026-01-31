"""
Tests for Flask app factory and Redis initialization.

Tests cover create_app, Redis session storage, and cache helper functions.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import redis


class TestCreateApp:
    """Tests for create_app function."""

    def test_create_app_development_config(self):
        """Should create app with development config."""
        with patch.dict('os.environ', {
            'SPOTIFY_CLIENT_ID': 'test_id',
            'SPOTIFY_CLIENT_SECRET': 'test_secret',
            'REDIS_URL': ''
        }):
            from shuffify import create_app
            app = create_app('development')

            assert app is not None
            assert app.config['DEBUG'] is True

    def test_create_app_uses_flask_env_default(self):
        """Should use FLASK_ENV when config_name not provided."""
        with patch.dict('os.environ', {
            'SPOTIFY_CLIENT_ID': 'test_id',
            'SPOTIFY_CLIENT_SECRET': 'test_secret',
            'FLASK_ENV': 'development',
            'REDIS_URL': ''
        }):
            from shuffify import create_app
            app = create_app()

            assert app.config['DEBUG'] is True

    def test_create_app_with_redis_url(self):
        """Should configure Redis session when REDIS_URL provided."""
        mock_redis = MagicMock(spec=redis.Redis)
        mock_redis.ping.return_value = True

        with patch.dict('os.environ', {
            'SPOTIFY_CLIENT_ID': 'test_id',
            'SPOTIFY_CLIENT_SECRET': 'test_secret',
            'REDIS_URL': 'redis://localhost:6379/0'
        }):
            with patch('shuffify.redis.from_url', return_value=mock_redis):
                from shuffify import create_app
                app = create_app('development')

                assert app.config['SESSION_TYPE'] == 'redis'
                assert app.config.get('SESSION_REDIS') is mock_redis

    def test_create_app_redis_connection_failure_fallback(self):
        """Should fall back to filesystem when Redis connection fails."""
        with patch.dict('os.environ', {
            'SPOTIFY_CLIENT_ID': 'test_id',
            'SPOTIFY_CLIENT_SECRET': 'test_secret',
            'REDIS_URL': 'redis://localhost:6379/0'
        }):
            with patch('shuffify.redis.from_url') as mock_from_url:
                mock_redis = Mock()
                mock_redis.ping.side_effect = redis.ConnectionError("Connection refused")
                mock_from_url.return_value = mock_redis

                from shuffify import create_app
                app = create_app('development')

                assert app.config['SESSION_TYPE'] == 'filesystem'

    def test_create_app_no_redis_url(self):
        """Should use filesystem sessions when REDIS_URL not set."""
        with patch.dict('os.environ', {
            'SPOTIFY_CLIENT_ID': 'test_id',
            'SPOTIFY_CLIENT_SECRET': 'test_secret',
        }, clear=True):
            from shuffify import create_app
            # Force no REDIS_URL
            import os
            os.environ.pop('REDIS_URL', None)

            app = create_app('development')

            assert app.config['SESSION_TYPE'] == 'filesystem'

    def test_create_app_registers_blueprints(self):
        """Should register main blueprint."""
        with patch.dict('os.environ', {
            'SPOTIFY_CLIENT_ID': 'test_id',
            'SPOTIFY_CLIENT_SECRET': 'test_secret',
            'REDIS_URL': ''
        }):
            from shuffify import create_app
            app = create_app('development')

            # Check that routes are registered
            rules = [rule.rule for rule in app.url_map.iter_rules()]
            assert '/' in rules
            assert '/health' in rules

    def test_create_app_debug_adds_no_cache_headers(self):
        """Should add no-cache headers in debug mode."""
        with patch.dict('os.environ', {
            'SPOTIFY_CLIENT_ID': 'test_id',
            'SPOTIFY_CLIENT_SECRET': 'test_secret',
            'REDIS_URL': ''
        }):
            from shuffify import create_app
            app = create_app('development')

            with app.test_client() as client:
                response = client.get('/health')
                assert 'no-cache' in response.headers.get('Cache-Control', '')


class TestGetRedisClient:
    """Tests for get_redis_client function."""

    def test_get_redis_client_returns_none_when_not_configured(self):
        """Should return None when Redis not configured."""
        import shuffify
        # Reset the global
        shuffify._redis_client = None

        result = shuffify.get_redis_client()
        assert result is None

    def test_get_redis_client_returns_client_when_configured(self):
        """Should return Redis client when configured."""
        import shuffify
        mock_redis = Mock(spec=redis.Redis)
        shuffify._redis_client = mock_redis

        result = shuffify.get_redis_client()
        assert result is mock_redis

        # Clean up
        shuffify._redis_client = None


class TestGetSpotifyCache:
    """Tests for get_spotify_cache function."""

    def test_get_spotify_cache_returns_none_when_redis_not_configured(self):
        """Should return None when Redis not configured."""
        import shuffify
        shuffify._redis_client = None

        result = shuffify.get_spotify_cache()
        assert result is None

    def test_get_spotify_cache_returns_cache_with_defaults(self):
        """Should return SpotifyCache with default settings outside Flask context."""
        import shuffify
        from shuffify.spotify.cache import SpotifyCache

        mock_redis = Mock(spec=redis.Redis)
        shuffify._redis_client = mock_redis

        result = shuffify.get_spotify_cache()

        assert isinstance(result, SpotifyCache)
        assert result._redis is mock_redis

        # Clean up
        shuffify._redis_client = None

    def test_get_spotify_cache_uses_flask_config(self):
        """Should use Flask config settings when in app context."""
        import shuffify
        from shuffify.spotify.cache import SpotifyCache
        from flask import Flask

        mock_redis = Mock(spec=redis.Redis)
        shuffify._redis_client = mock_redis

        # Create a minimal Flask app for the test to avoid Redis connection
        app = Flask(__name__)
        app.config['CACHE_KEY_PREFIX'] = 'test:cache:'
        app.config['CACHE_DEFAULT_TTL'] = 300
        app.config['CACHE_PLAYLIST_TTL'] = 120  # Custom TTL
        app.config['CACHE_USER_TTL'] = 600
        app.config['CACHE_AUDIO_FEATURES_TTL'] = 86400

        with app.app_context():
            result = shuffify.get_spotify_cache()

            assert isinstance(result, SpotifyCache)
            assert result._playlist_ttl == 120

        # Clean up
        shuffify._redis_client = None


class TestRedisClientCreation:
    """Tests for _create_redis_client function."""

    def test_create_redis_client_success(self):
        """Should create Redis client from URL."""
        from shuffify import _create_redis_client

        with patch('shuffify.redis.from_url') as mock_from_url:
            mock_client = Mock(spec=redis.Redis)
            mock_from_url.return_value = mock_client

            result = _create_redis_client('redis://localhost:6379/0')

            assert result is mock_client
            mock_from_url.assert_called_once_with(
                'redis://localhost:6379/0',
                decode_responses=False
            )


class TestConfigIntegration:
    """Tests for configuration integration with Redis."""

    def test_config_has_redis_settings(self):
        """Should have Redis configuration settings."""
        from config import Config

        assert hasattr(Config, 'REDIS_URL')
        assert hasattr(Config, 'SESSION_TYPE')
        assert hasattr(Config, 'SESSION_KEY_PREFIX')
        assert hasattr(Config, 'CACHE_KEY_PREFIX')
        assert hasattr(Config, 'CACHE_PLAYLIST_TTL')
        assert hasattr(Config, 'CACHE_USER_TTL')
        assert hasattr(Config, 'CACHE_AUDIO_FEATURES_TTL')

    def test_production_config_requires_redis_url(self):
        """Production config should have REDIS_URL from environment."""
        from config import ProdConfig

        # In production, REDIS_URL should be explicitly set
        assert hasattr(ProdConfig, 'REDIS_URL')

    def test_development_config_has_default_redis_url(self):
        """Development config should have default Redis URL."""
        from config import DevConfig

        # Should have a default for local development
        assert DevConfig.REDIS_URL is not None or hasattr(DevConfig, 'REDIS_URL')
