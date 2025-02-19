import logging
from typing import List, Dict, Any, Optional
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from flask import current_app, flash
import time

logger = logging.getLogger(__name__)

class SpotifyClient:
    """Handles all Spotify API interactions with proper error handling and logging."""
    
    def __init__(self, token: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the Spotify client with OAuth authentication."""
        self.scope: str = " ".join([
            "playlist-read-private",
            "playlist-read-collaborative",
            "playlist-modify-private",
            "playlist-modify-public",
            "user-read-private"
        ])
        
        self.sp = None
        self._initialize_client(token)
    
    def _initialize_client(self, token: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the Spotify client with the provided token or new authentication."""
        try:
            auth_manager = SpotifyOAuth(
                client_id=current_app.config['SPOTIFY_CLIENT_ID'],
                client_secret=current_app.config['SPOTIFY_CLIENT_SECRET'],
                redirect_uri=current_app.config['SPOTIFY_REDIRECT_URI'],
                scope=self.scope,
                open_browser=False
            )
            
            if token:
                auth_manager.token = token
                self.sp = spotipy.Spotify(auth_manager=auth_manager)
                # Verify the connection
                self.sp.current_user()
            else:
                # If no token, just initialize the auth manager
                self.sp = None
                
            logger.info("Successfully initialized Spotify client")
            
        except Exception as e:
            logger.error(f"Error initializing Spotify client: {str(e)}")
            raise
    
    def get_auth_url(self) -> str:
        """Get the Spotify authorization URL."""
        try:
            auth_manager = SpotifyOAuth(
                client_id=current_app.config['SPOTIFY_CLIENT_ID'],
                client_secret=current_app.config['SPOTIFY_CLIENT_SECRET'],
                redirect_uri=current_app.config['SPOTIFY_REDIRECT_URI'],
                scope=self.scope,
                open_browser=False
            )
            return auth_manager.get_authorize_url()
        except Exception as e:
            logger.error(f"Error getting auth URL: {str(e)}")
            raise
    
    def get_token(self, code: str) -> Dict[str, Any]:
        """Get access token from authorization code."""
        try:
            auth_manager = SpotifyOAuth(
                client_id=current_app.config['SPOTIFY_CLIENT_ID'],
                client_secret=current_app.config['SPOTIFY_CLIENT_SECRET'],
                redirect_uri=current_app.config['SPOTIFY_REDIRECT_URI'],
                scope=self.scope,
                open_browser=False
            )
            token = auth_manager.get_access_token(code, as_dict=True, check_cache=False)
            # Initialize client with new token
            self._initialize_client(token)
            logger.info("Successfully obtained access token")
            return token
        except Exception as e:
            logger.error(f"Error getting token: {str(e)}")
            raise
    
    def get_current_user(self) -> Dict[str, Any]:
        """Get current user's profile."""
        try:
            return self.sp.current_user()
        except Exception as e:
            logger.error(f"Error getting current user: {str(e)}")
            raise
    
    def get_user_playlists(self) -> List[Dict[str, Any]]:
        """Get user's playlists that they can modify."""
        try:
            playlists = []
            results = self.sp.current_user_playlists()
            user_id = self.sp.current_user()['id']
            
            while results:
                for playlist in results['items']:
                    if playlist['owner']['id'] == user_id or playlist.get('collaborative'):
                        playlists.append(playlist)
                
                results = self.sp.next(results) if results['next'] else None
            
            logger.info(f"Retrieved {len(playlists)} editable playlists")
            return playlists
            
        except Exception as e:
            logger.error(f"Error fetching playlists: {str(e)}")
            flash("Failed to fetch your playlists. Please try again.", "error")
            return []
    
    def get_playlist_tracks(self, playlist_id: str) -> List[str]:
        """Get all track URIs from a playlist."""
        try:
            tracks = []
            results = self.sp.playlist_items(playlist_id)
            
            while results:
                tracks.extend([
                    item['track']['uri'] for item in results['items']
                    if item.get('track', {}).get('uri')
                ])
                results = self.sp.next(results) if results['next'] else None
            
            logger.info(f"Retrieved {len(tracks)} tracks from playlist: {playlist_id}")
            return tracks
            
        except Exception as e:
            logger.error(f"Error fetching playlist tracks: {str(e)}")
            raise
    
    def update_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """Update playlist tracks."""
        try:
            # First, clear the playlist
            self.sp.playlist_replace_items(playlist_id, [])
            logger.info(f"Cleared playlist: {playlist_id}")
            
            # Add tracks in batches
            batch_size = 100
            for i in range(0, len(track_uris), batch_size):
                batch = track_uris[i:i + batch_size]
                self.sp.playlist_add_items(playlist_id, batch)
                logger.info(f"Added batch of {len(batch)} tracks")
            
            logger.info(f"Successfully updated playlist {playlist_id} with {len(track_uris)} tracks")
            return True
            
        except Exception as e:
            logger.error(f"Error updating playlist: {str(e)}")
            return False 