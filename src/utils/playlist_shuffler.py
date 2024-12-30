import random
from typing import List, Dict, Any

class PlaylistShuffler:
    """Handles the shuffling of playlist tracks."""
    
    @staticmethod
    def shuffle_tracks(tracks: List[Dict[str, Any]]) -> List[str]:
        """
        Shuffles tracks using Fisher-Yates algorithm and returns track URIs.
        """
        # Extract track URIs
        track_uris = [track['track']['uri'] for track in tracks if track['track']]
        
        # Fisher-Yates shuffle
        for i in range(len(track_uris) - 1, 0, -1):
            j = random.randint(0, i)
            track_uris[i], track_uris[j] = track_uris[j], track_uris[i]
            
        return track_uris 