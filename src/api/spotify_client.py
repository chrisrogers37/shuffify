from typing import List, Dict, Any
import spotipy
from spotipy.oauth2 import SpotifyPKCE
import time
import streamlit as st

class SpotifyClient:
    """Handles all Spotify API interactions."""
    
    def __init__(self):
        self.scope = " ".join([
            "playlist-read-private",
            "playlist-read-collaborative",
            "playlist-modify-private",
            "playlist-modify-public",
            "user-read-private"
        ])
        
        # Initialize sp as None
        self.sp = None
        self.last_shuffle = None
        self.original_state = None
        self.undo_timeout = 3600
        
        try:
            # Use PKCE authentication
            auth_manager = SpotifyPKCE(
                client_id=st.secrets["SPOTIPY_CLIENT_ID"],
                redirect_uri=st.secrets["SPOTIPY_REDIRECT_URI"],
                scope=self.scope,
                open_browser=True,
                cache_handler=spotipy.CacheFileHandler(cache_path=".spotifycache")
            )
            
            # Initialize Spotify client
            self.sp = spotipy.Spotify(auth_manager=auth_manager)
            
            # Verify authentication
            user = self.sp.current_user()
            st.success(f"Successfully logged in as: {user['display_name']}")
            
        except Exception as e:
            st.error("Please authorize with Spotify to continue.")
            auth_url = auth_manager.get_authorize_url()
            st.markdown(f"[Click here to authorize with Spotify]({auth_url})")
            raise Exception("Authentication required")
    
    def update_playlist_tracks(self, playlist_id, track_uris):
        try:
            # Only store the original state if this is the first operation on this playlist
            if not self.original_state or self.original_state['playlist_id'] != playlist_id:
                current_tracks = self.get_playlist_tracks(playlist_id)
                original_uris = [item['track']['uri'] for item in current_tracks if item['track']]
                self.original_state = {
                    'playlist_id': playlist_id,
                    'original_uris': original_uris,
                    'timestamp': time.time()
                }
            
            # Store the current operation for undo tracking
            self.last_shuffle = {
                'playlist_id': playlist_id,
                'timestamp': time.time()
            }
            
            # First, get the current tracks to verify we can access the playlist
            current = self.sp.playlist_items(playlist_id, fields='items.track.uri')
            
            print("\nClearing playlist...")
            total_tracks = self.sp.playlist(playlist_id)['tracks']['total']
            
            if total_tracks > 0:
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
            
            print("\nAdding shuffled tracks...")
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
            
            return True
            
        except Exception as e:
            st.error(f"Error in update_playlist_tracks: {str(e)}")
            return False
    
    def get_user_playlists(self):
        """Get user's playlists that they can modify."""
        try:
            playlists = []
            results = self.sp.current_user_playlists()
            
            while results:
                for playlist in results['items']:
                    # Only include playlists user can modify
                    if playlist['owner']['id'] == self.sp.current_user()['id'] or playlist.get('collaborative'):
                        playlists.append(playlist)
                
                results = self.sp.next(results) if results['next'] else None
            
            return playlists
            
        except Exception as e:
            print(f"Error fetching playlists: {str(e)}")
            return []
    
    def get_playlist_tracks(self, playlist_id):
        """Get all tracks from a playlist."""
        try:
            tracks = []
            results = self.sp.playlist_items(playlist_id)
            
            while results:
                tracks.extend(results['items'])
                results = self.sp.next(results) if results['next'] else None
            
            return tracks
            
        except Exception as e:
            print(f"Error fetching playlist tracks: {str(e)}")
            return [] 
    
    def undo_last_shuffle(self):
        """Restore the playlist to its original state before any shuffling."""
        if not self.original_state:
            return False
            
        # Check if undo has expired
        elapsed_time = time.time() - self.original_state['timestamp']
        if elapsed_time > self.undo_timeout:
            st.error("⚠️ Undo operation has expired (1 hour limit)")
            self.original_state = None
            self.last_shuffle = None
            return False
            
        try:
            playlist = self.sp.playlist(self.original_state['playlist_id'])
            
            # Remove confirmation prompt (will be handled in streamlit_app.py)
            print(f"\nRestoring '{playlist['name']}' to original order...")
            
            # Restore the complete original order
            success = self.update_playlist_tracks(
                self.original_state['playlist_id'],
                self.original_state['original_uris']
            )
            
            if success:
                # Clear both states after successful restore
                self.original_state = None
                self.last_shuffle = None
                return True
                
            return False
            
        except Exception as e:
            st.error(f"Error restoring playlist: {str(e)}")
            return False 