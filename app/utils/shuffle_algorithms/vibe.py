from typing import List, Optional, Dict, Any
import numpy as np
from app.spotify.client import SpotifyClient
from . import ShuffleAlgorithm
import logging
import random
import math

logger = logging.getLogger(__name__)

class VibeShuffle(ShuffleAlgorithm):
    """Creates smooth transitions by analyzing track characteristics using Spotify's audio features."""
    
    # Feature weights and ranges
    FEATURE_WEIGHTS = {
        'tempo': 0.3,
        'energy': 0.3,
        'valence': 0.2,
        'danceability': 0.2
    }
    
    FEATURE_RANGES = {
        'tempo': (0, 250),  # BPM typically ranges from 0 to 250
        'energy': (0, 1),
        'valence': (0, 1),
        'danceability': (0, 1)
    }
    
    @property
    def name(self) -> str:
        """Name of the algorithm."""
        return "Vibe"
    
    @property
    def description(self) -> str:
        """Description of what the algorithm does."""
        return "Creates smooth transitions by analyzing track characteristics using Spotify's audio features."
    
    @property
    def parameters(self) -> dict:
        """Parameters that can be configured for this algorithm."""
        return {
            'keep_first': {
                'type': 'integer',
                'description': 'Number of tracks to keep in their original position',
                'default': 0,
                'min': 0
            },
            'smoothness': {
                'type': 'float',
                'description': 'How smooth the transitions should be (0.0 to 1.0)',
                'default': 0.5,
                'min': 0.0,
                'max': 1.0
            }
        }
    
    @property
    def requires_features(self) -> bool:
        """Whether this algorithm requires audio features."""
        return True
    
    def _create_feature_vector(self, features: Dict[str, float]) -> np.ndarray:
        """Create a normalized feature vector from track features."""
        vector = []
        for feature, weight in self.FEATURE_WEIGHTS.items():
            value = features.get(feature, 0)
            min_val, max_val = self.FEATURE_RANGES[feature]
            # Normalize to 0-1 range
            normalized = (value - min_val) / (max_val - min_val)
            # Apply weight
            vector.append(normalized * weight)
        return np.array(vector)
    
    def _normalize_feature(self, value: float, feature: str) -> float:
        """Normalize a feature value to [0,1] range."""
        min_val, max_val = self.FEATURE_RANGES[feature]
        return (value - min_val) / (max_val - min_val)
    
    def _calculate_similarity(self, vec1: np.ndarray, vec2: np.ndarray, smoothness: float) -> float:
        """Calculate similarity between two feature vectors."""
        # Use cosine similarity
        similarity = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))
        # Adjust based on smoothness parameter
        return similarity * smoothness + (1 - similarity) * (1 - smoothness)
    
    def _calculate_distance(self, track1: Dict[str, Any], track2: Dict[str, Any]) -> float:
        """Calculate weighted Euclidean distance between two tracks based on their features."""
        distance = 0.0
        for feature, weight in self.FEATURE_WEIGHTS.items():
            if feature in track1 and feature in track2:
                val1 = self._normalize_feature(track1[feature], feature)
                val2 = self._normalize_feature(track2[feature], feature)
                distance += weight * (val1 - val2) ** 2
        return math.sqrt(distance)
    
    def _calculate_probabilities(self, current_track: Dict[str, Any], candidates: List[Dict[str, Any]], smoothness: float) -> List[float]:
        """Calculate selection probabilities for candidate tracks."""
        distances = [self._calculate_distance(current_track, track) for track in candidates]
        max_distance = max(distances) if distances else 1.0
        
        # Convert distances to similarities and apply smoothness
        similarities = [1 - (d / max_distance) for d in distances]
        probabilities = [s ** (1 / (smoothness + 0.1)) for s in similarities]
        
        # Normalize probabilities
        total = sum(probabilities)
        return [p / total for p in probabilities] if total > 0 else [1.0 / len(probabilities)] * len(probabilities)
    
    def shuffle(self, tracks: List[Dict[str, Any]], features: Optional[Dict[str, Dict[str, Any]]] = None, **kwargs) -> List[str]:
        """
        Shuffle tracks while creating smooth transitions based on audio features.
        
        Args:
            tracks: List of track dictionaries from Spotify API
            features: Dictionary of track URIs to audio features
            **kwargs: Additional parameters
                - keep_first: Number of tracks to keep at start
                - smoothness: How smooth the transitions should be
                
        Returns:
            List of shuffled track URIs
        """
        keep_first = kwargs.get('keep_first', 0)
        smoothness = kwargs.get('smoothness', 0.5)
        
        if not features:
            raise ValueError("Audio features are required for Vibe shuffle")
            
        # Extract URIs and features
        track_data = []
        for track in tracks:
            if track.get('uri') and track['uri'] in features:
                track_data.append({
                    'uri': track['uri'],
                    'features': features[track['uri']]
                })
        
        if len(track_data) <= 1:
            return [track['uri'] for track in track_data]
            
        # Split tracks into kept and to_shuffle portions
        kept_tracks = track_data[:keep_first] if keep_first > 0 else []
        to_shuffle = track_data[keep_first:]
        
        if len(to_shuffle) <= 1:
            return [track['uri'] for track in kept_tracks + to_shuffle]
            
        # Start with first track
        result = [to_shuffle[0]]
        remaining = to_shuffle[1:]
        
        # Build sequence based on feature similarity
        while remaining:
            current_track = result[-1]
            probabilities = self._calculate_probabilities(current_track['features'], [t['features'] for t in remaining], smoothness)
            
            # Select next track based on probabilities
            selected_index = random.choices(range(len(remaining)), weights=probabilities)[0]
            result.append(remaining.pop(selected_index))
            
        # Add kept tracks back at the start
        return [track['uri'] for track in kept_tracks] + [track['uri'] for track in result] 