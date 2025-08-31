import random
from typing import List, Dict, Any, Optional
from . import ShuffleAlgorithm

class PercentageShuffle(ShuffleAlgorithm):
    """Shuffle a portion of your playlist while keeping the rest in order."""
    
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
                'description': 'Percentage of tracks to shuffle (0-100)',
                'default': 50.0,
                'min': 0.0,
                'max': 100.0
            },
            'shuffle_location': {
                'type': 'string',
                'description': 'Location to shuffle (front or back)',
                'default': 'front',
                'options': ['front', 'back']
            }
        }
    
    @property
    def requires_features(self) -> bool:
        return False
    
    def shuffle(self, tracks: List[Dict[str, Any]], features: Optional[Dict[str, Dict[str, Any]]] = None, **kwargs) -> List[str]:
        """
        Shuffle a portion of the playlist while keeping the rest in order.
        
        Args:
            tracks: List of track dictionaries from Spotify API
            features: Optional dictionary of track URIs to audio features (unused in percentage shuffle)
            **kwargs: Additional parameters
                - shuffle_percentage: Percentage of tracks to shuffle (0-100)
                - shuffle_location: Location to shuffle ('front' or 'back')
                
        Returns:
            List of shuffled track URIs
        """
        shuffle_percentage = kwargs.get('shuffle_percentage', 50.0)
        shuffle_location = kwargs.get('shuffle_location', 'front')
        
        # Extract URIs from track dictionaries
        uris = [track['uri'] for track in tracks if track.get('uri')]
        
        if len(uris) <= 1:
            return uris
            
        # Calculate number of tracks to shuffle
        total_tracks = len(uris)
        shuffle_count = int(total_tracks * (shuffle_percentage / 100.0))
        
        if shuffle_count <= 0:
            return uris
            
        if shuffle_location == 'front':
            # Shuffle the front portion
            to_shuffle = uris[:shuffle_count]
            kept = uris[shuffle_count:]
            random.shuffle(to_shuffle)
            return to_shuffle + kept
        else:
            # Shuffle the back portion
            kept = uris[:-shuffle_count]
            to_shuffle = uris[-shuffle_count:]
            random.shuffle(to_shuffle)
            return kept + to_shuffle 