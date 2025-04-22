from typing import List, Optional
import random
from spotipy import Spotify
from . import ShuffleAlgorithm
import logging

logger = logging.getLogger(__name__)

class BalancedShuffle(ShuffleAlgorithm):
    """Divide the playlist into sections, shuffle each section, and then rebuild the playlist by selecting tracks from each section in a round-robin fashion."""
    
    @property
    def name(self) -> str:
        return "Balanced"
    
    @property
    def description(self) -> str:
        return "Divide the playlist into sections, shuffle each section, and then rebuild the playlist by selecting tracks from each section in a round-robin fashion."
    
    @property
    def parameters(self) -> dict:
        return {
            'keep_first': {
                'type': 'integer',
                'description': 'Number of tracks to keep at start',
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
    
    def shuffle(self, tracks: List[str], sp: Optional[Spotify] = None, **kwargs) -> List[str]:
        keep_first = kwargs.get('keep_first', 0)
        section_count = kwargs.get('section_count', 4)
        
        logger.info(f"Starting balanced shuffle with {len(tracks)} tracks (keep_first={keep_first}, sections={section_count})")
        
        if len(tracks) <= 1 or keep_first >= len(tracks):
            return tracks
            
        # Split tracks into kept and to_shuffle portions
        kept_tracks = tracks[:keep_first] if keep_first > 0 else []
        to_shuffle = tracks[keep_first:]
        
        if len(to_shuffle) <= 1:
            return kept_tracks + to_shuffle
            
        # Divide remaining tracks into sections
        section_size = len(to_shuffle) // section_count
        sections = []
        
        # Create sections of equal size (except possibly the last one)
        for i in range(section_count - 1):
            start = i * section_size
            end = start + section_size
            sections.append(to_shuffle[start:end])
            
        # Last section gets any remaining tracks
        sections.append(to_shuffle[(section_count-1) * section_size:])
        
        logger.info(f"Divided {len(to_shuffle)} tracks into {len(sections)} sections")
        
        # Shuffle each section internally
        for section in sections:
            random.shuffle(section)
        
        # Build final sequence by taking tracks from each section in a round-robin fashion
        result = kept_tracks.copy()  # Start with kept tracks
        remaining = [list(section) for section in sections]
        
        while any(remaining):
            for section in remaining:
                if section:
                    result.append(section.pop(0))
        
        logger.info(f"Completed balanced shuffle. Final playlist length: {len(result)}")
        return result 