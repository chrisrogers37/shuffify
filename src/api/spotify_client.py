from typing import List, Dict, Any
import spotipy
from ..auth.spotify_auth import SpotifyAuthenticator

class SpotifyClient:
    """Handles all Spotify API interactions."""
    
    def __init__(self):
        self.authenticator = SpotifyAuthenticator()
        self.client = self.authenticator.get_spotify_client()

    def get_user_playlists(self) -> List[Dict[str, Any]]:
        """Fetches all playlists for the authenticated user."""
        playlists = []
        results = self.client.current_user_playlists()
        
        while results:
            playlists.extend(results['items'])
            if results['next']:
                results = self.client.next(results)
            else:
                break
                
        return playlists

    def get_playlist_tracks(self, playlist_id: str) -> List[Dict[str, Any]]:
        """Fetches all tracks from a specific playlist."""
        tracks = []
        results = self.client.playlist_tracks(playlist_id)
        
        while results:
            tracks.extend(results['items'])
            if results['next']:
                results = self.client.next(results)
            else:
                break
                
        return tracks

    def update_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """Updates the playlist with the new track order."""
        try:
            # Clear the playlist
            self.client.playlist_replace_items(playlist_id, [])
            
            # Add tracks in batches of 100 (Spotify API limit)
            for i in range(0, len(track_uris), 100):
                batch = track_uris[i:i + 100]
                self.client.playlist_add_items(playlist_id, batch)
            return True
        except Exception as e:
            print(f"Error updating playlist: {e}")
            return False 