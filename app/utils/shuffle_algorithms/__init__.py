from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from spotipy import Spotify
from app.services.track_cache import TrackCache

class ShuffleAlgorithm(ABC):
    """Base class for shuffle algorithms."""
    
    @abstractmethod
    def shuffle(self, tracks: List[str], sp: Optional[Spotify] = None, **kwargs) -> List[str]:
        """
        Shuffle the given tracks using the algorithm's strategy.
        
        Args:
            tracks: List of track URIs to shuffle
            sp: Optional Spotify client for algorithms that need track metadata
            **kwargs: Additional algorithm-specific parameters
            
        Returns:
            Shuffled list of track URIs
        """
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the algorithm."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Description of how the algorithm works."""
        pass
    
    @property
    @abstractmethod
    def parameters(self) -> dict:
        """
        Dictionary describing the algorithm's parameters.
        Example:
        {
            'keep_first': {
                'type': 'integer',
                'description': 'Number of tracks to keep at start',
                'default': 0,
                'min': 0
            }
        }
        """
        pass
    
    def get_track_features(self, sp: Spotify, tracks: List[str]) -> Dict[str, Dict[str, float]]:
        """
        Get audio features for tracks, using cache when possible.
        This is available to all algorithms but only used by those that need it.
        """
        return TrackCache.load_track_features(sp, tracks) 