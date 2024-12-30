import random
from typing import List, Dict, Any

class PlaylistShuffler:
    """Handles the shuffling of playlist tracks."""
    
    @staticmethod
    def shuffle_tracks(tracks: List[Dict[str, Any]], keep_first: int = 0) -> List[str]:
        """
        Shuffles tracks using Fisher-Yates algorithm and returns track URIs.
        
        Args:
            tracks: List of track objects from Spotify API
            keep_first: Number of tracks to keep at their original position (default: 0)
        """
        # Extract track URIs, ensuring they exist and are valid
        track_uris = []
        for track in tracks:
            if track and 'track' in track and track['track'] and 'uri' in track['track']:
                uri = track['track']['uri']
                if uri and isinstance(uri, str) and uri.startswith('spotify:track:'):
                    track_uris.append(uri)
        
        if not track_uris:
            raise ValueError("No valid track URIs found in the playlist")
            
        # Validate keep_first value
        keep_first = max(0, min(keep_first, len(track_uris)))
        
        # Split the list into tracks to keep and tracks to shuffle
        fixed_tracks = track_uris[:keep_first]
        tracks_to_shuffle = track_uris[keep_first:]
        
        # Shuffle the remaining tracks using Fisher-Yates
        for i in range(len(tracks_to_shuffle) - 1, 0, -1):
            j = random.randint(0, i)
            tracks_to_shuffle[i], tracks_to_shuffle[j] = tracks_to_shuffle[j], tracks_to_shuffle[i]
            
        # Combine fixed and shuffled tracks
        return fixed_tracks + tracks_to_shuffle 