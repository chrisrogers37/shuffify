import random
from typing import List, Dict, Any, Optional
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
    
    @property
    def requires_features(self) -> bool:
        return False
    
    def shuffle(self, tracks: List[Dict[str, Any]], features: Optional[Dict[str, Dict[str, Any]]] = None, **kwargs) -> List[str]:
        """
        Randomly shuffle tracks while optionally keeping some at the start.
        
        Args:
            tracks: List of track dictionaries from Spotify API
            features: Optional dictionary of track URIs to audio features (unused in basic shuffle)
            **kwargs: Additional parameters
                - keep_first: Number of tracks to keep at start
                
        Returns:
            List of shuffled track URIs
        """
        keep_first = kwargs.get('keep_first', 0)
        
        # Extract URIs from track dictionaries
        uris = [track['uri'] for track in tracks if track.get('uri')]
        
        if len(uris) <= 1 or keep_first >= len(uris):
            return uris
            
        if keep_first > 0:
            kept_uris = uris[:keep_first]
            to_shuffle = uris[keep_first:]
            random.shuffle(to_shuffle)
            return kept_uris + to_shuffle
        
        shuffled = uris.copy()
        random.shuffle(shuffled)
        return shuffled 