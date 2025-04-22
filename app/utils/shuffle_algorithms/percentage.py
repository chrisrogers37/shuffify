from typing import List, Optional
import random
from spotipy import Spotify
from . import ShuffleAlgorithm

class PercentageShuffle(ShuffleAlgorithm):
    """Shuffle only a specified percentage of the playlist."""
    
    @property
    def name(self) -> str:
        return "Percentage"
    
    @property
    def description(self) -> str:
        return "Shuffle a portion of your playlist while keeping the rest in order."
    
    @property
    def parameters(self) -> dict:
        return {
            'shuffle_percentage': {
                'type': 'float',
                'description': 'Percentage to shuffle (0-100%)',
                'default': 50.0,
                'min': 0.0,
                'max': 100.0
            },
            'shuffle_location': {
                'type': 'string',
                'description': 'Which part to shuffle',
                'default': 'back',
                'options': ['front', 'back']
            }
        }
    
    def shuffle(self, tracks: List[str], sp: Optional[Spotify] = None, **kwargs) -> List[str]:
        shuffle_percentage = kwargs.get('shuffle_percentage', 50.0)
        shuffle_location = kwargs.get('shuffle_location', 'back')
        
        if len(tracks) <= 1 or shuffle_percentage <= 0:
            return tracks
            
        # Calculate how many tracks to shuffle
        shuffle_count = int((shuffle_percentage / 100.0) * len(tracks))
        
        if shuffle_count >= len(tracks):
            shuffled = tracks.copy()
            random.shuffle(shuffled)
            return shuffled
            
        if shuffle_location == 'back':
            # Keep beginning in order, shuffle the end
            kept = tracks[:len(tracks) - shuffle_count]
            to_shuffle = tracks[len(tracks) - shuffle_count:]
            random.shuffle(to_shuffle)
            return kept + to_shuffle
        else:
            # Shuffle beginning, keep end in order
            to_shuffle = tracks[:shuffle_count]
            kept = tracks[shuffle_count:]
            random.shuffle(to_shuffle)
            return to_shuffle + kept 