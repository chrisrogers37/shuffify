from typing import List, Dict, Any
import spotipy
from spotipy.oauth2 import SpotifyPKCE
import os
from dotenv import load_dotenv
from tqdm import tqdm
import time
from datetime import datetime, timedelta
import json
import os.path

load_dotenv()

class SpotifyClient:
    """Handles all Spotify API interactions."""
    
    def __init__(self):
        # Define all required scopes
        self.scope = " ".join([
            "playlist-read-private",
            "playlist-read-collaborative",
            "playlist-modify-private",
            "playlist-modify-public",
            "user-read-private"
        ])
        
        # Use PKCE authentication
        auth_manager = SpotifyPKCE(
            client_id="7ae66f7d8a4a428abb27996b87fb74be",  # This is public and safe to share
            redirect_uri="http://localhost:8888/callback",
            scope=self.scope,
            cache_path='.spotifycache'
        )
        
        self.sp = spotipy.Spotify(auth_manager=auth_manager)
        
        # Verify authentication and get user info
        try:
            user = self.sp.current_user()
            print(f"\nAuthenticated as: {user['display_name']} ({user['id']})")
        except Exception as e:
            print("Authentication failed. Please try again.")
            raise e
        
        self.last_shuffle = None
        self.original_state = None
        self.undo_timeout = 3600
    
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
            # Get total number of tracks first
            total_tracks = self.sp.playlist(playlist_id)['tracks']['total']
            
            # Clear the playlist in batches with progress bar
            if total_tracks > 0:
                with tqdm(
                    total=total_tracks,
                    desc="Removing tracks",
                    bar_format="{desc}: {percentage:3.0f}% |{bar}| {n_fmt}/{total_fmt} songs [elapsed: {elapsed}, remaining: {remaining}]"
                ) as pbar:
                    while True:
                        items = self.sp.playlist_items(playlist_id, limit=50)['items']
                        if not items:
                            break
                        
                        track_ids = [item['track']['id'] for item in items if item['track']]
                        if track_ids:
                            self.sp.playlist_remove_all_occurrences_of_items(playlist_id, track_ids)
                            pbar.update(len(track_ids))
            
            print("\nAdding shuffled tracks...")
            # Add new tracks in small batches with progress bar
            batch_size = 50
            with tqdm(
                total=len(track_uris),
                desc="Adding tracks",
                bar_format="{desc}: {percentage:3.0f}% |{bar}| {n_fmt}/{total_fmt} songs [elapsed: {elapsed}, remaining: {remaining}]"
            ) as pbar:
                for i in range(0, len(track_uris), batch_size):
                    batch = track_uris[i:i + batch_size]
                    self.sp.playlist_add_items(playlist_id, batch)
                    pbar.update(len(batch))
            
            return True
            
        except Exception as e:
            print(f"\nError in update_playlist_tracks: {str(e)}")
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
            print("⚠️ Undo operation has expired (1 hour limit)")
            self.original_state = None
            self.last_shuffle = None
            return False
            
        try:
            playlist = self.sp.playlist(self.original_state['playlist_id'])
            
            print(f"\nAre you sure you want to restore '{playlist['name']}' to its original order from when you started?")
            confirm = input("Type 'yes' to confirm: ").strip().lower()
            if confirm != 'yes':
                print("Undo cancelled.")
                return False
            
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
            print(f"Error restoring playlist: {str(e)}")
            return False 