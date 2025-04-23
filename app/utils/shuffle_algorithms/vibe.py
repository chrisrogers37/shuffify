from typing import List, Optional, Dict, Any
import numpy as np
from app.spotify.client import SpotifyClient
from . import ShuffleAlgorithm
import logging
import random

logger = logging.getLogger(__name__)

class VibeShuffle(ShuffleAlgorithm):
    """Creates smooth transitions by analyzing track characteristics using Spotify's audio features."""
    
    FEATURE_WEIGHTS = {
        'tempo': 0.3,
        'energy': 0.3,
        'valence': 0.2,
        'danceability': 0.2
    }
    
    # Feature normalization ranges based on Spotify API documentation
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
                'description': 'Number of tracks to keep at start',
                'default': 0,
                'min': 0
            },
            'smoothness': {
                'type': 'float',
                'description': 'How smooth should transitions be?',
                'secondary_description': '0.0 will prioritize dissimilar tracks, 1.0 will prioritize similar tracks',
                'default': 0.5,
                'min': 0.0,
                'max': 1.0
            }
        }
    
    def shuffle(self, tracks: List[str], sp: Optional[SpotifyClient] = None, **kwargs) -> List[str]:
        """
        Shuffle tracks based on their audio features to create smooth transitions.
        
        Args:
            tracks: List of track URIs to shuffle
            sp: SpotifyClient for fetching audio features
            **kwargs: Additional parameters
                - keep_first: Number of tracks to keep at start
                - smoothness: How smooth transitions should be (0.0-1.0)
                
        Returns:
            List of shuffled track URIs
        """
        if not tracks:
            return []
            
        if not sp:
            logger.warning("No Spotify client provided, falling back to random shuffle")
            return random.sample(tracks, len(tracks))
        
        try:
            # Get parameters with defaults
            keep_first = kwargs.get('keep_first', 0)
            smoothness = kwargs.get('smoothness', 0.5)
            
            # Validate parameters
            if not 0 <= smoothness <= 1:
                logger.warning(f"Invalid smoothness value {smoothness}, using default 0.5")
                smoothness = 0.5
                
            if not 0 <= keep_first < len(tracks):
                logger.warning(f"Invalid keep_first value {keep_first}, using default 0")
                keep_first = 0
            
            # Get audio features for all tracks
            features = sp.get_track_features(tracks)
            
            if not features:
                logger.warning("No features available, falling back to random shuffle")
                return random.sample(tracks, len(tracks))
            
            # Separate tracks with and without features
            tracks_with_features = [t for t in tracks if t in features]
            tracks_without_features = [t for t in tracks if t not in features]
            
            if not tracks_with_features:
                logger.warning("No tracks with features available, falling back to random shuffle")
                return random.sample(tracks, len(tracks))
            
            # Keep first N tracks if specified
            if keep_first > 0:
                keep_tracks = tracks[:keep_first]
                remaining_tracks = tracks[keep_first:]
                remaining_features = {t: features[t] for t in remaining_tracks if t in features}
            else:
                keep_tracks = []
                remaining_tracks = tracks
                remaining_features = features
            
            # Create feature vectors for remaining tracks
            feature_vectors = {}
            for track_uri, track_features in remaining_features.items():
                feature_vectors[track_uri] = self._create_feature_vector(track_features)
            
            # Start with first track (or random if keeping first N)
            if keep_tracks:
                shuffled = keep_tracks.copy()
                current_track = keep_tracks[-1]
            else:
                shuffled = []
                current_track = random.choice(list(feature_vectors.keys()))
                shuffled.append(current_track)
                del feature_vectors[current_track]
            
            # Build shuffled list based on feature similarity
            while feature_vectors:
                # Get current track's features
                current_features = remaining_features[current_track]
                
                # Calculate similarity scores
                similarities = {}
                for track_uri, features in feature_vectors.items():
                    similarity = self._calculate_similarity(
                        self._create_feature_vector(current_features),
                        features,
                        smoothness
                    )
                    similarities[track_uri] = similarity
                
                # Select next track based on smoothness
                if smoothness > 0.5:
                    # Prefer similar tracks
                    next_track = max(similarities.items(), key=lambda x: x[1])[0]
                else:
                    # Prefer dissimilar tracks
                    next_track = min(similarities.items(), key=lambda x: x[1])[0]
                
                # Add to shuffled list and remove from remaining
                shuffled.append(next_track)
                del feature_vectors[next_track]
                current_track = next_track
            
            # Add tracks without features at the end
            if tracks_without_features:
                shuffled.extend(random.sample(tracks_without_features, len(tracks_without_features)))
            
            return shuffled
            
        except Exception as e:
            logger.error(f"Error in Vibe shuffle: {str(e)}")
            # Fall back to random shuffle on error
            return random.sample(tracks, len(tracks))
    
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
    
    def _calculate_similarity(self, vec1: np.ndarray, vec2: np.ndarray, smoothness: float) -> float:
        """Calculate similarity between two feature vectors."""
        # Use cosine similarity
        similarity = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))
        # Adjust based on smoothness parameter
        return similarity * smoothness + (1 - similarity) * (1 - smoothness) 