from typing import List, Dict, Any
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

class SpotifyClient:
    """Handles all Spotify API interactions."""
    
    def __init__(self):
        # Define all required scopes
        self.scope = " ".join([
            "playlist-read-private",
            "playlist-read-collaborative",
            "playlist-modify-private",
            "playlist-modify-public"
        ])
        
        # Get credentials from environment variables
        client_id = os.getenv('SPOTIPY_CLIENT_ID')
        client_secret = os.getenv('SPOTIPY_CLIENT_SECRET')
        redirect_uri = os.getenv('SPOTIPY_REDIRECT_URI')
        
        # Debug print (remove these after confirming they work)
        print(f"Client ID loaded: {'Yes' if client_id else 'No'}")
        print(f"Client Secret loaded: {'Yes' if client_secret else 'No'}")
        print(f"Redirect URI: {redirect_uri}")
        
        if not all([client_id, client_secret, redirect_uri]):
            raise ValueError(
                "Missing Spotify credentials. Please set SPOTIPY_CLIENT_ID, "
                "SPOTIPY_CLIENT_SECRET, and SPOTIPY_REDIRECT_URI environment variables"
            )
        
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=self.scope,
            cache_path='.spotifycache'
        ))
    
    def update_playlist_tracks(self, playlist_id, track_uris):
        try:
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