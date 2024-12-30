from typing import List, Dict, Any
import spotipy
from ..auth.spotify_auth import SpotifyAuthenticator

class SpotifyClient:
    """Handles all Spotify API interactions."""
    
    def __init__(self):
        self.authenticator = SpotifyAuthenticator()
        self.client = self.authenticator.get_spotify_client()
        # Get current user's ID for checking playlist ownership
        self.user_id = self.client.current_user()['id']

    def is_playlist_editable(self, playlist: Dict[str, Any]) -> bool:
        """
        Checks if the current user can edit the playlist.
        A playlist is editable if:
        1. The user is the owner, or
        2. The playlist is collaborative
        """
        is_owner = playlist['owner']['id'] == self.user_id
        is_collaborative = playlist.get('collaborative', False)
        return is_owner or is_collaborative

    def get_user_playlists(self) -> List[Dict[str, Any]]:
        """Fetches all playlists that the user can edit."""
        editable_playlists = []
        results = self.client.current_user_playlists()
        
        while results:
            # Filter playlists to only include those we can edit
            for playlist in results['items']:
                if self.is_playlist_editable(playlist):
                    editable_playlists.append(playlist)
            
            if results['next']:
                results = self.client.next(results)
            else:
                break
                
        return editable_playlists

    def get_playlist_tracks(self, playlist_id: str) -> List[Dict[str, Any]]:
        """Fetches all tracks from a specific playlist."""
        tracks = []
        results = self.client.playlist_tracks(playlist_id)
        
        while results:
            if 'items' in results:
                tracks.extend(results['items'])
            
            if results['next']:
                results = self.client.next(results)
            else:
                break
                
        return tracks

    def update_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """Updates the playlist with the new track order."""
        try:
            # Verify we can edit this playlist
            playlist = self.client.playlist(playlist_id)
            if not self.is_playlist_editable(playlist):
                raise ValueError("You don't have permission to edit this playlist")
            
            # Replace all items at once
            self.client.playlist_replace_items(playlist_id, track_uris)
            return True
        except Exception as e:
            print(f"Error updating playlist: {e}")
            return False 