import logging
from typing import List, Dict, Any, Optional
import spotipy
from spotipy.oauth2 import SpotifyPKCE
import time
import streamlit as st
from spotipy.exceptions import SpotifyException

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SpotifyClient:
    """Handles all Spotify API interactions with proper error handling and logging."""
    
    def __init__(self) -> None:
        """Initialize the Spotify client with PKCE authentication."""
        self.scope: str = " ".join([
            "playlist-read-private",
            "playlist-read-collaborative",
            "playlist-modify-private",
            "playlist-modify-public",
            "user-read-private"
        ])
        
        self.sp: Optional[spotipy.Spotify] = None
        self.last_shuffle: Optional[Dict[str, Any]] = None
        self.original_state: Optional[Dict[str, Any]] = None
        self.undo_timeout: int = 3600  # 1 hour timeout for undo operations
        
        try:
            auth_manager = SpotifyPKCE(
                client_id=st.secrets["SPOTIPY_CLIENT_ID"],
                redirect_uri=st.secrets["SPOTIPY_REDIRECT_URI"],
                scope=self.scope,
                open_browser=False
            )
            
            self.sp = spotipy.Spotify(auth_manager=auth_manager)
            user = self.sp.current_user()
            logger.info(f"Successfully authenticated user: {user['display_name']}")
            st.success(f"Successfully connected as: {user['display_name']}")
            
        except SpotifyException as e:
            logger.error(f"Spotify authentication error: {str(e)}")
            self._handle_auth_error()
        except Exception as e:
            logger.error(f"Unexpected error during initialization: {str(e)}")
            raise Exception("Authentication required")
    
    def _handle_auth_error(self) -> None:
        """Handle authentication errors by displaying the Spotify login button."""
        try:
            auth_manager = SpotifyPKCE(
                client_id=st.secrets["SPOTIPY_CLIENT_ID"],
                redirect_uri=st.secrets["SPOTIPY_REDIRECT_URI"],
                scope=self.scope,
                open_browser=False
            )
            auth_url = auth_manager.get_authorize_url()
            
            st.markdown(f'''
            <a href="{auth_url}" target="_self">
                <button style="
                    background-color: #1DB954;
                    color: white;
                    padding: 12px 24px;
                    border: none;
                    border-radius: 24px;
                    cursor: pointer;
                    font-size: 16px;
                    font-weight: bold;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    width: 100%;
                    max-width: 300px;
                    margin: 0 auto;
                ">
                    <svg style="margin-right: 8px;" width="24" height="24" viewBox="0 0 24 24" fill="white">
                        <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
                    </svg>
                    Connect with Spotify
                </button>
            </a>
            ''', unsafe_allow_html=True)
            
            st.stop()
            
        except Exception as e:
            logger.error(f"Error creating auth button: {str(e)}")
            st.error("Failed to create authentication button. Please try again.")
            st.stop()
    
    def update_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """Update playlist tracks with error handling and progress tracking."""
        try:
            # Store original state for undo functionality
            if not self.original_state or self.original_state['playlist_id'] != playlist_id:
                current_tracks = self.get_playlist_tracks(playlist_id)
                original_uris = [item['track']['uri'] for item in current_tracks if item['track']]
                self.original_state = {
                    'playlist_id': playlist_id,
                    'original_uris': original_uris,
                    'timestamp': time.time()
                }
                logger.info(f"Stored original state for playlist: {playlist_id}")
            
            self.last_shuffle = {
                'playlist_id': playlist_id,
                'timestamp': time.time()
            }
            
            # Verify playlist access
            current = self.sp.playlist_items(playlist_id, fields='items.track.uri')
            total_tracks = self.sp.playlist(playlist_id)['tracks']['total']
            
            logger.info(f"Updating playlist {playlist_id} with {len(track_uris)} tracks")
            
            if total_tracks > 0:
                self._clear_playlist(playlist_id, total_tracks)
            
            self._add_tracks(playlist_id, track_uris)
            
            logger.info(f"Successfully updated playlist: {playlist_id}")
            return True
            
        except SpotifyException as e:
            logger.error(f"Spotify API error: {str(e)}")
            st.error(f"Error updating playlist: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            st.error("An unexpected error occurred while updating the playlist")
            return False
    
    def _clear_playlist(self, playlist_id: str, total_tracks: int) -> None:
        """Clear all tracks from a playlist with progress tracking."""
        logger.info(f"Clearing {total_tracks} tracks from playlist: {playlist_id}")
        progress_text = "Removing tracks..."
        progress_bar = st.progress(0)
        progress_count = st.empty()
        
        current_count = 0
        while True:
            items = self.sp.playlist_items(playlist_id, limit=50)['items']
            if not items:
                break
            
            track_ids = [item['track']['id'] for item in items if item['track']]
            if track_ids:
                self.sp.playlist_remove_all_occurrences_of_items(playlist_id, track_ids)
                current_count += len(track_ids)
                progress_bar.progress(current_count / total_tracks)
                progress_count.text(f"{progress_text} ({current_count}/{total_tracks})")
    
    def _add_tracks(self, playlist_id: str, track_uris: List[str]) -> None:
        """Add tracks to a playlist with progress tracking."""
        logger.info(f"Adding {len(track_uris)} tracks to playlist: {playlist_id}")
        progress_text = "Adding tracks..."
        progress_bar = st.progress(0)
        progress_count = st.empty()
        
        batch_size = 50
        for i in range(0, len(track_uris), batch_size):
            batch = track_uris[i:i + batch_size]
            self.sp.playlist_add_items(playlist_id, batch)
            progress = (i + len(batch)) / len(track_uris)
            progress_bar.progress(progress)
            progress_count.text(f"{progress_text} ({i + len(batch)}/{len(track_uris)})")
    
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
            
        except SpotifyException as e:
            logger.error(f"Error fetching playlists: {str(e)}")
            st.error("Failed to fetch your playlists. Please try again.")
            return []
    
    def get_playlist_tracks(self, playlist_id: str) -> List[Dict[str, Any]]:
        """Get all tracks from a playlist."""
        try:
            tracks = []
            results = self.sp.playlist_items(playlist_id)
            
            while results:
                tracks.extend(results['items'])
                results = self.sp.next(results) if results['next'] else None
            
            logger.info(f"Retrieved {len(tracks)} tracks from playlist: {playlist_id}")
            return tracks
            
        except SpotifyException as e:
            logger.error(f"Error fetching playlist tracks: {str(e)}")
            st.error("Failed to fetch playlist tracks. Please try again.")
            return []
    
    def undo_last_shuffle(self) -> bool:
        """Restore the playlist to its original state before shuffling."""
        if not self.original_state:
            logger.warning("No original state found for undo operation")
            return False
        
        elapsed_time = time.time() - self.original_state['timestamp']
        if elapsed_time > self.undo_timeout:
            logger.warning("Undo operation expired")
            st.error("⚠️ Undo operation has expired (1 hour limit)")
            self.original_state = None
            self.last_shuffle = None
            return False
        
        try:
            playlist = self.sp.playlist(self.original_state['playlist_id'])
            logger.info(f"Restoring playlist {playlist['name']} to original order")
            
            success = self.update_playlist_tracks(
                self.original_state['playlist_id'],
                self.original_state['original_uris']
            )
            
            if success:
                self.original_state = None
                self.last_shuffle = None
                logger.info("Successfully restored playlist to original order")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error restoring playlist: {str(e)}")
            st.error(f"Error restoring playlist: {str(e)}")
            return False 