# app/spotify/client.py
import logging
import traceback
import time
from typing import List, Dict, Any, Optional
from functools import wraps
import spotipy
from spotipy.oauth2 import SpotifyOAuth

logger = logging.getLogger(__name__)
logging.getLogger('spotipy').setLevel(logging.WARNING)

def spotify_error_handler(func):
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

class SpotifyClient:
    def __init__(self, token: Optional[Dict[str, Any]] = None, credentials: Optional[Dict[str, str]] = None) -> None:
        self.scope = " ".join([
            "playlist-read-private", "playlist-read-collaborative",
            "playlist-modify-private", "playlist-modify-public",
            "user-read-private", "user-read-playback-state",
            "user-read-email", "user-read-currently-playing",
            "user-read-recently-played", "user-top-read"
        ])
        self.credentials = credentials
        self.token = token
        self.auth_manager = self._create_auth_manager()
        self.sp = None

        if token:
            self._initialize_client(token)
        else:
            logger.debug("Initialized SpotifyClient without token")

    def _create_auth_manager(self) -> SpotifyOAuth:
        if self.credentials:
            return SpotifyOAuth(
                client_id=self.credentials['client_id'],
                client_secret=self.credentials['client_secret'],
                redirect_uri=self.credentials['redirect_uri'],
                scope=self.scope,
                open_browser=False
            )
        else:
            from flask import current_app
            return SpotifyOAuth(
                client_id=current_app.config['SPOTIFY_CLIENT_ID'],
                client_secret=current_app.config['SPOTIFY_CLIENT_SECRET'],
                redirect_uri=current_app.config['SPOTIFY_REDIRECT_URI'],
                scope=self.scope,
                open_browser=False
            )

    def _initialize_client(self, token: Dict[str, Any]) -> None:
        if not token or token.get('expires_at', 0) < time.time():
            logger.error("Cannot initialize Spotify client without valid token")
            raise ValueError("Invalid or expired token for Spotify initialization.")
        self.token = token
        self.auth_manager.cache_handler.save_token_to_cache(token)
        self.sp = spotipy.Spotify(auth_manager=self.auth_manager)
        logger.info("Spotify client initialized with fresh token")

    @spotify_error_handler
    def get_auth_url(self) -> str:
        return self.auth_manager.get_authorize_url()

    @spotify_error_handler
    def get_token(self, code: str) -> Dict[str, Any]:
        token_info = self.auth_manager.get_access_token(code, as_dict=True, check_cache=False)
        if not token_info:
            logger.error("No token returned from Spotify")
            raise Exception("Failed to get access token from Spotify")
        logger.debug("Token received, reinitializing client")
        self._initialize_client(token_info)
        return token_info

    def _refresh_token_if_needed(self):
        token_info = self.auth_manager.cache_handler.get_cached_token()
        if not token_info:
            logger.error("No cached token available")
            raise RuntimeError("No cached token available to refresh")
        if token_info.get('expires_at', 0) < time.time():
            logger.info("Refreshing Spotify token")
            refreshed = self.auth_manager.refresh_access_token(token_info['refresh_token'])
            self._initialize_client(refreshed)

    def _ensure_spotify_client(self):
        if not self.sp:
            raise RuntimeError("Spotify client not initialized. Please authenticate first.")

    @spotify_error_handler
    def get_current_user(self) -> Dict[str, Any]:
        self._refresh_token_if_needed()
        self._ensure_spotify_client()
        return self.sp.current_user()

    @spotify_error_handler
    def get_user_playlists(self) -> List[Dict[str, Any]]:
        self._refresh_token_if_needed()
        self._ensure_spotify_client()
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

    @spotify_error_handler
    def get_playlist(self, playlist_id: str) -> Dict[str, Any]:
        self._refresh_token_if_needed()
        self._ensure_spotify_client()
        return self.sp.playlist(playlist_id)

    @spotify_error_handler
    def get_playlist_tracks(self, playlist_id: str) -> List[Dict[str, Any]]:
        self._refresh_token_if_needed()
        self._ensure_spotify_client()
        tracks = []
        results = self.sp.playlist_items(playlist_id)
        while results:
            tracks.extend([item['track'] for item in results['items'] if item.get('track', {}).get('uri')])
            results = self.sp.next(results) if results['next'] else None
        logger.debug(f"Retrieved {len(tracks)} tracks for playlist {playlist_id}")
        return tracks

    @spotify_error_handler
    def get_track_audio_features(self, track_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        self._refresh_token_if_needed()
        self._ensure_spotify_client()
        features = {}
        valid_ids = [tid.split(":")[-1] for tid in track_ids if tid]
        batch_size = 50
        for i in range(0, len(valid_ids), batch_size):
            batch = valid_ids[i:i+batch_size]
            results = self.sp.audio_features(batch)
            if results:
                for track_id, feature in zip(batch, results):
                    if feature:
                        features[track_id] = feature
        return features

    @spotify_error_handler
    def update_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        self._refresh_token_if_needed()
        self._ensure_spotify_client()
        try:
            self.sp.playlist_replace_items(playlist_id, [])
            logger.info(f"Cleared playlist: {playlist_id}")
            for i in range(0, len(track_uris), 100):
                self.sp.playlist_add_items(playlist_id, track_uris[i:i+100])
            logger.info(f"Updated playlist {playlist_id} with {len(track_uris)} tracks")
            return True
        except Exception as e:
            logger.error(f"Failed to update playlist: {str(e)}")
            return False
