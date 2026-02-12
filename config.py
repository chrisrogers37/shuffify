import os
from dotenv import load_dotenv

load_dotenv()


def _resolve_database_url(fallback: str) -> str:
    """
    Resolve DATABASE_URL with PostgreSQL compatibility fixes.

    Neon and Railway provide DATABASE_URL with the ``postgres://`` scheme,
    but SQLAlchemy 2.x requires ``postgresql://``.  This function
    transparently rewrites the prefix.

    Args:
        fallback: Default database URL if DATABASE_URL env var is not set.

    Returns:
        Resolved database URL string.
    """
    url = os.getenv('DATABASE_URL', fallback)
    if url and url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url


def validate_required_env_vars():
    """Validate that all required environment variables are present."""
    required_vars = ['SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET']
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise ValueError(f"Missing required environment variables: {missing}")

class Config:
    """Base configuration."""
    SECRET_KEY = os.getenv('SECRET_KEY', 'a_default_secret_key_for_development')
    SESSION_COOKIE_NAME = os.getenv('SESSION_COOKIE_NAME', 'shuffify_session')
    SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
    SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
    # The redirect URI must match the one set in your Spotify Developer Dashboard
    SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI', 'http://localhost:8000/callback')

    # Redis configuration
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    # Session configuration - Redis-based storage
    SESSION_TYPE = 'redis'
    SESSION_PERMANENT = False
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour
    SESSION_KEY_PREFIX = 'shuffify:session:'

    # Session security settings for OAuth
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'  # More permissive for OAuth flows
    SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS

    # Redis caching configuration for Spotify API responses
    CACHE_KEY_PREFIX = 'shuffify:cache:'
    CACHE_DEFAULT_TTL = 300  # 5 minutes default TTL
    CACHE_PLAYLIST_TTL = 60  # 1 minute for playlist data (changes frequently)
    CACHE_USER_TTL = 600  # 10 minutes for user profile data
    CACHE_AUDIO_FEATURES_TTL = 86400  # 24 hours for audio features (rarely change)

    # Database configuration
    SQLALCHEMY_DATABASE_URI = _resolve_database_url('sqlite:///shuffify.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Scheduler configuration
    SCHEDULER_ENABLED = True
    SCHEDULER_MAX_SCHEDULES_PER_USER = 5

    # Application settings
    DEBUG = False
    PORT = int(os.getenv('PORT', 8000))
    HOST = os.getenv('HOST', '0.0.0.0')

    @classmethod
    def get_spotify_credentials(cls) -> dict:
        return {
            'client_id': cls.SPOTIFY_CLIENT_ID,
            'client_secret': cls.SPOTIFY_CLIENT_SECRET,
            'redirect_uri': cls.SPOTIFY_REDIRECT_URI
        }

class ProdConfig(Config):
    """Production configuration."""
    # Note: FLASK_ENV was removed in Flask 3.0. Use config_name parameter instead.
    CONFIG_NAME = 'production'
    DEBUG = False
    TESTING = False
    SCHEDULER_ENABLED = (
        os.getenv('SCHEDULER_ENABLED', 'true').lower() == 'true'
    )
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Production Redis settings - require REDIS_URL to be set
    REDIS_URL = os.getenv('REDIS_URL')  # Must be explicitly set in production

    # Production database - PostgreSQL via Neon or Railway
    SQLALCHEMY_DATABASE_URI = _resolve_database_url('sqlite:///shuffify.db')

    # PostgreSQL engine options: connection pooling and SSL for managed providers
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 5,
        'pool_recycle': 300,
        'pool_pre_ping': True,
        'connect_args': (
            {'sslmode': 'require'}
            if os.getenv('DATABASE_URL', '').startswith('postgres')
            else {}
        ),
    }


class DevConfig(Config):
    """Development configuration."""
    # Note: FLASK_ENV was removed in Flask 3.0. Use config_name parameter instead.
    CONFIG_NAME = 'development'
    DEBUG = True
    TESTING = True
    SESSION_COOKIE_SECURE = False

    # Development Redis settings - fallback to localhost
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    # Development database - PostgreSQL if DATABASE_URL is set, else SQLite
    SQLALCHEMY_DATABASE_URI = _resolve_database_url(
        'sqlite:///shuffify_dev.db'
    )

    SCHEDULER_ENABLED = (
        os.getenv('SCHEDULER_ENABLED', 'true').lower() == 'true'
    )

# Dictionary for easy config selection
config = {
    'development': DevConfig,
    'production': ProdConfig,
    'default': DevConfig
} 
