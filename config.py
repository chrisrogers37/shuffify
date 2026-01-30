import os
from dotenv import load_dotenv

load_dotenv()

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
    
    # Session configuration - improved for OAuth compatibility
    SESSION_TYPE = 'filesystem'
    SESSION_FILE_DIR = './.flask_session/'
    SESSION_PERMANENT = False
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour
    
    # Session security settings for OAuth
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'  # More permissive for OAuth flows
    SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS

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
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

class DevConfig(Config):
    """Development configuration."""
    # Note: FLASK_ENV was removed in Flask 3.0. Use config_name parameter instead.
    CONFIG_NAME = 'development'
    DEBUG = True
    TESTING = True
    SESSION_COOKIE_SECURE = False

# Dictionary for easy config selection
config = {
    'development': DevConfig,
    'production': ProdConfig,
    'default': DevConfig
} 
