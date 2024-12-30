import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Spotify API Configuration
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI')

# Required Spotify API Scopes
SPOTIFY_SCOPES = [
    'playlist-read-private',
    'playlist-modify-public',
    'playlist-modify-private'
]

# Application Settings
CACHE_PATH = '.spotify_cache' 