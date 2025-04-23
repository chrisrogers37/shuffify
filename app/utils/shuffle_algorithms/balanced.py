from typing import List, Dict, Any, Optional
import random
from spotipy import Spotify
from . import ShuffleAlgorithm
import logging

logger = logging.getLogger(__name__)

class BalancedShuffle(ShuffleAlgorithm):
    """Ensures fair representation from all parts of the playlist by dividing it into sections and using a round-robin selection process."""
    
    @property
    def name(self) -> str:
        return "Balanced"
    
    @property
    def description(self) -> str:
        return "Ensures fair representation from all parts of the playlist by dividing it into sections and using a round-robin selection process."
    
    @property
    def parameters(self) -> dict:
        return {
            'keep_first': {
                'type': 'integer',
                'description': 'Number of tracks to keep in their original position',
                'default': 0,
                'min': 0
            },
            'section_count': {
                'type': 'integer',
                'description': 'Number of sections to divide the playlist into',
                'default': 4,
                'min': 2,
                'max': 10
            }
        }
    
    @property
    def requires_features(self) -> bool:
        return False
    
    def shuffle(self, tracks: List[Dict[str, Any]], features: Optional[Dict[str, Dict[str, Any]]] = None, **kwargs) -> List[str]:
        """
        Shuffle tracks while ensuring fair representation from all parts of the playlist.
        
        Args:
            tracks: List of track dictionaries from Spotify API
            features: Optional dictionary of track URIs to audio features (unused in balanced shuffle)
            **kwargs: Additional parameters
                - keep_first: Number of tracks to keep at start
                - section_count: Number of sections to divide playlist into
                
        Returns:
            List of shuffled track URIs
        """
        keep_first = kwargs.get('keep_first', 0)
        section_count = kwargs.get('section_count', 4)
        
        # Extract URIs from track dictionaries
        uris = [track['uri'] for track in tracks if track.get('uri')]
        
        if len(uris) <= 1:
            return uris
            
        # Split tracks into kept and to_shuffle portions
        kept_tracks = uris[:keep_first] if keep_first > 0 else []
        to_shuffle = uris[keep_first:]
        
        if len(to_shuffle) <= 1:
            return kept_tracks + to_shuffle
            
        # Calculate section sizes
        total_tracks = len(to_shuffle)
        base_section_size = total_tracks // section_count
        remainder = total_tracks % section_count
        
        # Create sections
        sections = []
        start = 0
        
        for i in range(section_count):
            # Add one extra track to sections until remainder is used up
            current_section_size = base_section_size + (1 if i < remainder else 0)
            end = start + current_section_size
            section = to_shuffle[start:end]
            sections.append(section)
            start = end
            
        # Shuffle each section internally
        for section in sections:
            random.shuffle(section)
            
        # Build final sequence using round-robin selection
        result = kept_tracks.copy()
        while any(sections):
            for section in sections:
                if section:
                    result.append(section.pop(0))
                    
        return result 