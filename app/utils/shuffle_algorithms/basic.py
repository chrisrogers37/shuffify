import random
from typing import List, Optional
from spotipy import Spotify
from . import ShuffleAlgorithm

class BasicShuffle(ShuffleAlgorithm):
    """Randomly shuffle your playlist while optionally keeping tracks in place at the top."""
    
    @property
    def name(self) -> str:
        return "Basic"
    
    @property
    def description(self) -> str:
        return "Randomly shuffle your playlist while optionally keeping tracks in place at the top."
    
    @property
    def parameters(self) -> dict:
        return {
            'keep_first': {
                'type': 'integer',
                'description': 'Number of tracks to keep at start',
                'default': 0,
                'min': 0
            }
        }
    
    def shuffle(self, tracks: List[str], sp: Optional[Spotify] = None, **kwargs) -> List[str]:
        keep_first = kwargs.get('keep_first', 0)
        
        if len(tracks) <= 1 or keep_first >= len(tracks):
            return tracks
            
        if keep_first > 0:
            kept_tracks = tracks[:keep_first]
            to_shuffle = tracks[keep_first:]
            random.shuffle(to_shuffle)
            return kept_tracks + to_shuffle
        
        shuffled = tracks.copy()
        random.shuffle(shuffled)
        return shuffled 