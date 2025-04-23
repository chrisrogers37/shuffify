import logging
import traceback
from typing import List, Dict, Any, Optional
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from flask import current_app, flash, session
import time

logger = logging.getLogger(__name__)

class SpotifyClient:
    """Handles all Spotify API interactions and caching with proper error handling and logging."""
    
    BATCH_SIZE = 50  # Maximum number of tracks per audio features request
    CACHE_KEY = 'spotify_cache'
    
    def __init__(self, token: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the Spotify client with OAuth authentication."""
        try:
            logger.debug("Initializing SpotifyClient with token: %s", "Present" if token else "None")
            logger.debug("Current app config: %s", {
                k: v for k, v in current_app.config.items() 
                if k in ['SPOTIFY_CLIENT_ID', 'SPOTIFY_REDIRECT_URI'] or k.startswith('SESSION_')
            })
            
            self.scope: str = " ".join([
                "playlist-read-private",
                "playlist-read-collaborative",
                "playlist-modify-private",
                "playlist-modify-public",
                "user-read-private",
                "user-read-playback-state",
                "user-read-email"
            ])
            
            self.sp = None
            self._initialize_client(token)
        except Exception as e:
            logger.error("Error in SpotifyClient init: %s\nTraceback: %s", str(e), traceback.format_exc())
            raise
    
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
            logger.debug("Getting token for code: %s", code)
            auth_manager = SpotifyOAuth(
                client_id=current_app.config['SPOTIFY_CLIENT_ID'],
                client_secret=current_app.config['SPOTIFY_CLIENT_SECRET'],
                redirect_uri=current_app.config['SPOTIFY_REDIRECT_URI'],
                scope=self.scope,
                open_browser=False
            )
            
            logger.debug("Created auth manager, getting access token")
            token = auth_manager.get_access_token(code, as_dict=True, check_cache=False)
            
            if not token:
                logger.error("No token returned from Spotify")
                raise Exception("Failed to get access token from Spotify")
                
            logger.debug("Got token, initializing client")
            self._initialize_client(token)
            logger.info("Successfully obtained access token")
            return token
            
        except Exception as e:
            logger.error("Error getting token: %s\nTraceback: %s", str(e), traceback.format_exc())
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
    
    def get_track_features(self, track_uris: List[str]) -> Dict[str, Dict[str, float]]:
        """Get audio features for tracks, using cache when possible."""
        if not track_uris:
            return {}
            
        # First, try to get features from cache
        features = self.get_cached_features(track_uris)
        
        # Identify tracks that need features fetched
        missing_tracks = [t for t in track_uris if t not in features]
        
        if missing_tracks:
            logger.info(f"Fetching features for {len(missing_tracks)} tracks from Spotify API")
            new_features = {}
            
            try:
                # Process tracks in batches
                for i in range(0, len(missing_tracks), self.BATCH_SIZE):
                    batch = missing_tracks[i:i + self.BATCH_SIZE]
                    try:
                        batch_features = self.sp.audio_features(batch)
                        
                        if batch_features:
                            for track_uri, track_features in zip(batch, batch_features):
                                if track_features:
                                    # Log specific missing features
                                    missing = [f for f in ['tempo', 'energy', 'valence', 'danceability'] 
                                             if track_features.get(f) is None]
                                    if missing:
                                        logger.warning(f"Track {track_uri} missing features: {missing}")
                                    
                                    new_features[track_uri] = {
                                        'tempo': track_features.get('tempo'),
                                        'energy': track_features.get('energy'),
                                        'valence': track_features.get('valence'),
                                        'danceability': track_features.get('danceability'),
                                        'key': track_features.get('key'),
                                        'mode': track_features.get('mode')
                                    }
                                    # Only require tempo, energy, valence, and danceability
                                    if any(v is None for v in [new_features[track_uri]['tempo'], 
                                                             new_features[track_uri]['energy'],
                                                             new_features[track_uri]['valence'],
                                                             new_features[track_uri]['danceability']]):
                                        del new_features[track_uri]
                                        logger.warning(f"Track {track_uri} removed due to missing required features")
                                    else:
                                        logger.debug(f"Successfully got features for track {track_uri}")
                    except Exception as e:
                        logger.error(f"Error fetching features for batch {i//self.BATCH_SIZE + 1}: {str(e)}")
                        # Log the specific error details
                        if hasattr(e, 'response'):
                            logger.error(f"Response status: {e.response.status_code}")
                            logger.error(f"Response body: {e.response.text}")
                        # Continue with next batch instead of failing completely
                        continue
                    
            except Exception as e:
                logger.error(f"Error in feature fetching process: {str(e)}")
                # Don't raise the exception, return what we have
            
            # Cache any new features we successfully fetched
            if new_features:
                self.cache_features(new_features)
                features.update(new_features)
        
        # Log summary of feature availability
        total_tracks = len(track_uris)
        tracks_with_features = len(features)
        logger.info(f"Feature summary: {tracks_with_features}/{total_tracks} tracks have complete features")
        
        return features
    
    def get_cached_features(self, track_uris: List[str]) -> Dict[str, Dict[str, float]]:
        """Get cached features for tracks."""
        cache = self._get_cache()
        features = cache.get('features', {})
        return {
            uri: feature_data
            for uri, feature_data in features.items()
            if uri in track_uris and feature_data and all(v is not None for v in feature_data.values())
        }
    
    def cache_features(self, features_data: Dict[str, Dict[str, float]]) -> None:
        """Cache track features."""
        try:
            cache = self._get_cache()
            features = cache.get('features', {})
            
            # Update cache with new features
            for track_uri, track_features in features_data.items():
                if track_features:  # Only cache if we have valid features
                    features[track_uri] = {
                        'tempo': track_features.get('tempo'),
                        'energy': track_features.get('energy'),
                        'valence': track_features.get('valence'),
                        'danceability': track_features.get('danceability'),
                        'key': track_features.get('key'),
                        'mode': track_features.get('mode')
                    }
                    # Only keep features if all values are present
                    if any(v is None for v in features[track_uri].values()):
                        del features[track_uri]
            
            # Store updated cache
            cache['features'] = features
            self._update_cache(cache)
            
        except Exception as e:
            logger.error(f"Error caching features: {str(e)}")
            raise
    
    def clear_cache(self) -> None:
        """Clear the feature cache."""
        if self.CACHE_KEY in session:
            del session[self.CACHE_KEY]
            logger.info("Feature cache cleared")
    
    def _get_cache(self) -> Dict[str, Any]:
        """Get the session cache."""
        return session.get(self.CACHE_KEY, {})
    
    def _update_cache(self, cache: Dict[str, Any]) -> None:
        """Update the session cache."""
        session[self.CACHE_KEY] = cache 