from typing import List, Dict, Any, Optional
import random
from spotipy import Spotify
from . import ShuffleAlgorithm
import logging

logger = logging.getLogger(__name__)

class StratifiedShuffle:
    """Divide the playlist into sections, shuffle each section independently, and reassemble the sections in the original order."""
    
    @property
    def name(self) -> str:
        return "Stratified"
    
    @property
    def description(self) -> str:
        return "Divide the playlist into sections, shuffle each section independently, and reassemble the sections in the original order."
    
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
                'default': 5,
                'min': 1,
                'max': 20
            }
        }
    
    @property
    def requires_features(self) -> bool:
        return False
    
    def shuffle(self, tracks: List[Dict[str, Any]], features: Optional[Dict[str, Dict[str, Any]]] = None, **kwargs) -> List[str]:
        """
        Shuffle tracks by dividing them into sections, shuffling each section independently, and reassembling in order.
        
        Args:
            tracks: List of track dictionaries from Spotify API
            features: Optional dictionary of track URIs to audio features (unused in stratified shuffle)
            **kwargs: Additional parameters
                - keep_first: Number of tracks to keep at start
                - section_count: Number of sections to divide playlist into
                
        Returns:
            List of shuffled track URIs
        """
        keep_first = kwargs.get('keep_first', 0)
        section_count = kwargs.get('section_count', 5)
        
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
            
        # Reassemble sections in original order
        result = kept_tracks.copy()
        for section in sections:
            result.extend(section)
            
        return result 