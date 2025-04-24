import logging
import traceback
from typing import List, Dict, Any, Optional, Callable, TypeVar, Generic
from functools import wraps
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from flask import current_app, flash, session
import time

# Configure spotipy logging to reduce noise
logging.getLogger('spotipy').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

T = TypeVar('T')

def spotify_error_handler(func: Callable) -> Callable:
    """Decorator to handle Spotify API errors consistently."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}")
            if hasattr(e, 'response'):
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise
    return wrapper

class BatchProcessor(Generic[T]):
    """Utility class for processing items in batches."""
    
    def __init__(self, batch_size: int):
        self.batch_size = batch_size
    
    def process(self, items: List[T], process_batch: Callable[[List[T]], Any]) -> None:
        """Process items in batches."""
        for i in range(0, len(items), self.batch_size):
            batch = items[i:i + self.batch_size]
            process_batch(batch)

class SpotifyClient:
    """Handles Spotify API interactions and authentication."""
    
    PLAYLIST_BATCH_SIZE = 100  # Maximum number of tracks per playlist update
    
    def __init__(self, token: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the Spotify client with OAuth authentication."""
        try:
            # Only log token presence at debug level
            logger.debug("Initializing SpotifyClient with token: %s", "Present" if token else "None")
            
            self.scope: str = " ".join([
                "playlist-read-private",
                "playlist-read-collaborative",
                "playlist-modify-private",
                "playlist-modify-public",
                "user-read-private",
                "user-read-playback-state",
                "user-read-email",
                "user-read-currently-playing",
                "user-read-recently-played",
                "user-top-read"
            ])
            
            self.sp = None
            self._initialize_client(token)
            self._playlist_processor = BatchProcessor(self.PLAYLIST_BATCH_SIZE)
        except Exception as e:
            logger.error("Error in SpotifyClient init: %s", str(e))
            raise
    
    def _create_auth_manager(self) -> SpotifyOAuth:
        """Create a SpotifyOAuth manager with current configuration."""
        return SpotifyOAuth(
            client_id=current_app.config['SPOTIFY_CLIENT_ID'],
            client_secret=current_app.config['SPOTIFY_CLIENT_SECRET'],
            redirect_uri=current_app.config['SPOTIFY_REDIRECT_URI'],
            scope=self.scope,
            open_browser=False
        )
    
    def _initialize_client(self, token: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the Spotify client with the provided token or new authentication."""
        try:
            auth_manager = self._create_auth_manager()
            
            if token:
                logger.debug("Initializing with existing token")
                # Validate token format
                required_keys = ['access_token', 'token_type', 'expires_at']
                if not all(key in token for key in required_keys):
                    logger.error("Invalid token format")
                    raise ValueError("Invalid token format")
                    
                # Check if token is expired
                if token.get('expires_at', 0) < time.time():
                    logger.error("Token is expired")
                    raise ValueError("Token is expired")
                    
                auth_manager.token = token
                self.sp = spotipy.Spotify(auth_manager=auth_manager)
                
                # Verify the connection with a lightweight API call
                try:
                    self.sp.current_user()
                except Exception as e:
                    logger.error("Failed to verify token: %s", str(e))
                    raise ValueError("Invalid or expired token")
            else:
                logger.debug("Initializing without token")
                self.sp = None
                
            logger.info("Successfully initialized Spotify client")
            
        except Exception as e:
            logger.error("Error initializing Spotify client: %s\nTraceback: %s", str(e), traceback.format_exc())
            raise
    
    @spotify_error_handler
    def get_auth_url(self) -> str:
        """Get the Spotify authorization URL."""
        return self._create_auth_manager().get_authorize_url()
    
    @spotify_error_handler
    def get_token(self, code: str) -> Dict[str, Any]:
        """Get access token from authorization code."""
        logger.debug("Getting token for code: %s", code)
        auth_manager = self._create_auth_manager()
        
        logger.debug("Created auth manager, getting access token")
        token = auth_manager.get_access_token(code, as_dict=True, check_cache=False)
        
        if not token:
            logger.error("No token returned from Spotify")
            raise Exception("Failed to get access token from Spotify")
            
        logger.debug("Got token, initializing client")
        self._initialize_client(token)
        logger.info("Successfully obtained access token")
        return token
    
    @spotify_error_handler
    def get_current_user_data(self) -> Dict[str, Any]:
        """Get current user's profile data from Spotify API."""
        return self.sp.current_user()
    
    @spotify_error_handler
    def get_user_playlists_data(self) -> List[Dict[str, Any]]:
        """Get user's playlists data from Spotify API."""
        playlists = []
        results = self.sp.current_user_playlists()
        user_id = self.sp.current_user()['id']
        
        while results:
            for playlist in results['items']:
                if playlist['owner']['id'] == user_id or playlist.get('collaborative'):
                    playlists.append(playlist)
            
            results = self.sp.next(results) if results['next'] else None
        
        logger.debug(f"Retrieved {len(playlists)} editable playlists")
        return playlists
    
    def _validate_token(self) -> bool:
        """Validate the current token and its scopes."""
        try:
            if not self.sp:
                logger.error("Spotify client not initialized")
                return False
                
            # Get token info
            token_info = self.sp._auth_manager.get_cached_token()
            if not token_info:
                logger.error("No token found in auth manager")
                return False
                
            # Log token details
            logger.debug("Token details:")
            logger.debug(f"Expires at: {token_info.get('expires_at')}")
            logger.debug(f"Scopes: {token_info.get('scope', 'No scopes found')}")
            logger.debug(f"Token type: {token_info.get('token_type')}")
                
            # Check expiration
            if token_info.get('expires_at', 0) < time.time():
                logger.error("Token is expired")
                return False
                
            # Verify token with a lightweight API call
            self.sp.current_user()
            return True
            
        except Exception as e:
            logger.error(f"Token validation failed: {str(e)}")
            return False

    @spotify_error_handler
    def get_playlist_with_tracks(self, playlist_id: str) -> Dict[str, Any]:
        """Get complete playlist data including tracks."""
        try:
            # Get playlist metadata
            playlist_data = self.sp.playlist(playlist_id)
            
            # Get all tracks
            tracks = []
            results = self.sp.playlist_items(playlist_id)
            
            while results:
                tracks.extend([
                    item['track'] for item in results['items']
                    if item.get('track', {}).get('uri')
                ])
                results = self.sp.next(results) if results['next'] else None
            
            logger.debug(f"Retrieved {len(tracks)} tracks for playlist {playlist_id}")
            if tracks:
                logger.debug(f"First track URI: {tracks[0].get('uri')}")
                logger.debug(f"First track ID: {tracks[0].get('id')}")
            
            # Add tracks to playlist data
            playlist_data['tracks'] = tracks
            return playlist_data
            
        except Exception as e:
            logger.error(f"Error getting playlist data: {str(e)}")
            raise
    
    @spotify_error_handler
    def update_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """Update playlist tracks via Spotify API."""
        # First, clear the playlist
        self.sp.playlist_replace_items(playlist_id, [])
        logger.info(f"Cleared playlist: {playlist_id}")
        
        def process_batch(batch: List[str]) -> None:
            self.sp.playlist_add_items(playlist_id, batch)
            logger.info(f"Added batch of {len(batch)} tracks")
        
        self._playlist_processor.process(track_uris, process_batch)
        logger.info(f"Successfully updated playlist {playlist_id} with {len(track_uris)} tracks")
        return True 