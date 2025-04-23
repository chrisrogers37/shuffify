from typing import List, Optional, Dict, Any, Protocol
from app.spotify.client import SpotifyClient
import random
import logging

logger = logging.getLogger(__name__)

class ShuffleAlgorithm(Protocol):
    """Interface for shuffle algorithms."""
    
    @property
    def name(self) -> str:
        """Name of the algorithm."""
        ...
    
    @property
    def description(self) -> str:
        """Description of what the algorithm does."""
        ...
    
    @property
    def parameters(self) -> dict:
        """Parameters that can be configured for this algorithm."""
        ...
    
    def shuffle(self, tracks: List[str], sp: Optional[SpotifyClient] = None, **kwargs) -> List[str]:
        """
        Shuffle the tracks according to the algorithm.
        
        Args:
            tracks: List of track URIs to shuffle
            sp: SpotifyClient for fetching audio features
            **kwargs: Additional parameters specific to the algorithm
            
        Returns:
            List of shuffled track URIs
        """
        ... 