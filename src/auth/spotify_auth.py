import spotipy
from spotipy.oauth2 import SpotifyOAuth
from ..config import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPES,
    CACHE_PATH
)

class SpotifyAuthenticator:
    """Handles Spotify authentication and token management."""
    
    def __init__(self):
        self.auth_manager = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=' '.join(SPOTIFY_SCOPES),
            cache_path=CACHE_PATH
        )
        
    def get_spotify_client(self) -> spotipy.Spotify:
        """Returns an authenticated Spotify client instance."""
        return spotipy.Spotify(auth_manager=self.auth_manager)

    def validate_token(self) -> bool:
        """Validates the current token and refreshes if necessary."""
        return self.auth_manager.validate_token(self.auth_manager.get_cached_token()) 