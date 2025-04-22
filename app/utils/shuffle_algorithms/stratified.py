from typing import List, Optional
import random
from spotipy import Spotify
from . import ShuffleAlgorithm
import logging

logger = logging.getLogger(__name__)

class StratifiedSample(ShuffleAlgorithm):
    """Divide the playlist into chunks, shuffle each chunk independently, and reassemble in order."""
    
    @property
    def name(self) -> str:
        return "Stratified"
    
    @property
    def description(self) -> str:
        return "Divide the playlist into chunks, shuffle each chunk independently, and reassemble the chunks in the original order. This preserves general structure while adding local variety."
    
    @property
    def parameters(self) -> dict:
        return {
            'keep_first': {
                'type': 'integer',
                'description': 'Number of tracks to keep at start',
                'default': 0,
                'min': 0
            },
            'chunk_count': {
                'type': 'integer',
                'description': 'Number of chunks to divide the playlist into',
                'default': 5,
                'min': 1,
                'max': 20
            }
        }
    
    def shuffle(self, tracks: List[str], sp: Optional[Spotify] = None, **kwargs) -> List[str]:
        keep_first = kwargs.get('keep_first', 0)
        chunk_count = kwargs.get('chunk_count', 5)
        
        logger.info(f"Starting stratified sample shuffle with {len(tracks)} tracks (keep_first={keep_first}, chunks={chunk_count})")
        
        if len(tracks) <= 1 or keep_first >= len(tracks):
            return tracks
            
        # Split tracks into kept and to_shuffle portions
        kept_tracks = tracks[:keep_first] if keep_first > 0 else []
        to_shuffle = tracks[keep_first:]
        
        if len(to_shuffle) <= 1:
            return kept_tracks + to_shuffle
            
        # Calculate chunk size and handle remainder
        chunk_size = len(to_shuffle) // chunk_count
        remainder = len(to_shuffle) % chunk_count
        
        # Create chunks
        chunks = []
        start = 0
        
        for i in range(chunk_count):
            # Add one extra track to chunks until remainder is used up
            current_chunk_size = chunk_size + (1 if i < remainder else 0)
            end = start + current_chunk_size
            chunk = to_shuffle[start:end]
            chunks.append(chunk)
            start = end
            
        logger.info(f"Divided {len(to_shuffle)} tracks into {len(chunks)} chunks")
        
        # Shuffle each chunk independently
        for chunk in chunks:
            random.shuffle(chunk)
            
        # Reassemble chunks in order
        result = kept_tracks.copy()
        for chunk in chunks:
            result.extend(chunk)
            
        logger.info(f"Completed stratified sample shuffle. Final playlist length: {len(result)}")
        return result 