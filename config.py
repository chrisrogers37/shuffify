import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration."""
    SECRET_KEY = os.getenv('SECRET_KEY', os.urandom(24))
    SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
    SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
    SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI')
    
    # Session configuration
    SESSION_TYPE = 'filesystem'
    SESSION_FILE_DIR = './.flask_session/'
    SESSION_PERMANENT = False
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour

    # Application settings
    DEBUG = False
    TESTING = False
    PORT = int(os.getenv('PORT', 8000))
    HOST = os.getenv('HOST', '0.0.0.0')

class ProductionConfig(Config):
    """Production configuration."""
    # Use secure cookie in production
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    TESTING = False
    PORT = 8000
    HOST = 'localhost'

class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DEBUG = True
    PORT = 8000
    HOST = 'localhost'

# Dictionary for easy config selection
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
} 