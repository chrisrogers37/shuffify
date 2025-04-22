from typing import List, Optional, Dict, Any
import numpy as np
from spotipy import Spotify
from . import ShuffleAlgorithm
from app.services.track_cache import TrackCache
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
        return "Vibe"
    
    @property
    def description(self) -> str:
        return "Creates smooth transitions by analyzing track characteristics using Spotify's audio features."
    
    @property
    def parameters(self) -> dict:
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
                'secondary_description': '0.0 will prioritize dissimilar transitions, 1.0 will prioritize similar transitions.',
                'default': 0.5,
                'min': 0.0,
                'max': 1.0
            }
        }
    
    def _normalize_feature(self, value: float, feature: str) -> float:
        """Normalize a feature value to [0,1] range based on its typical range."""
        min_val, max_val = self.FEATURE_RANGES[feature]
        return (value - min_val) / (max_val - min_val)
    
    def _calculate_distance(self, features1: Dict[str, float], features2: Dict[str, float]) -> float:
        """Calculate weighted Euclidean distance between two tracks' features."""
        try:
            distance = 0.0
            for feature, weight in self.FEATURE_WEIGHTS.items():
                if feature not in features1 or feature not in features2:
                    logger.warning(f"Missing feature {feature} in track features")
                    logger.debug(f"Features1: {features1}")
                    logger.debug(f"Features2: {features2}")
                    return float('inf')
                
                # Normalize both values before calculating difference
                val1 = self._normalize_feature(features1[feature], feature)
                val2 = self._normalize_feature(features2[feature], feature)
                diff = val1 - val2
                distance += weight * (diff ** 2)
            
            return np.sqrt(distance)
        except Exception as e:
            logger.error(f"Error calculating distance: {str(e)}")
            return float('inf')
    
    def _calculate_probabilities(self, distances: Dict[str, float], smoothness: float) -> Dict[str, float]:
        """Calculate selection probabilities based on distances and smoothness."""
        if not distances:
            return {}
            
        # Normalize distances to [0, 1] range
        max_dist = max(distances.values())
        min_dist = min(distances.values())
        
        if max_dist == min_dist:
            return {uri: 1.0/len(distances) for uri in distances}
            
        # Convert distances to similarities (1 - normalized_distance)
        similarities = {
            uri: 1 - (dist - min_dist) / (max_dist - min_dist)
            for uri, dist in distances.items()
        }
        
        # Apply smoothness factor with exponential weighting for more dramatic effect
        # Higher smoothness = more emphasis on similar tracks
        probabilities = {
            uri: similarity ** (1 / (smoothness + 0.1))  # Add 0.1 to avoid division by zero
            for uri, similarity in similarities.items()
        }
        
        # Normalize probabilities
        total = sum(probabilities.values())
        return {uri: p/total for uri, p in probabilities.items()}
    
    def _find_next_track(self, current_features: Dict[str, float], 
                        available_features: Dict[str, Dict[str, float]], 
                        smoothness: float) -> str:
        """Find the next track based on feature similarity and randomness."""
        try:
            # Calculate distances between current track and all available tracks
            distances = {
                uri: self._calculate_distance(current_features, features)
                for uri, features in available_features.items()
            }
            
            if not distances:
                logger.warning("No distances calculated, returning random track")
                return random.choice(list(available_features.keys()))
            
            # Calculate probabilities using the new method
            probabilities = self._calculate_probabilities(distances, smoothness)
            
            # Choose next track based on probabilities
            uris = list(probabilities.keys())
            probs = list(probabilities.values())
            
            # Log probability distribution for debugging
            sorted_probs = sorted(zip(uris, probs), key=lambda x: x[1], reverse=True)
            logger.debug(f"Probability distribution (smoothness={smoothness}):")
            for uri, prob in sorted_probs[:3]:
                logger.debug(f"  Track {uri[-8:]}: {prob:.3f}")
            
            selected = np.random.choice(uris, p=probs)
            logger.debug(f"Selected track {selected[-8:]} with probability {probabilities[selected]:.3f}")
            
            return selected
            
        except Exception as e:
            logger.error(f"Error finding next track: {str(e)}")
            return random.choice(list(available_features.keys()))
    
    def shuffle(self, tracks: List[str], sp: Any, **kwargs) -> List[str]:
        """Shuffle tracks while maintaining a cohesive vibe."""
        if not tracks:
            return []
            
        keep_first = kwargs.get('keep_first', 0)
        if keep_first > 0:
            kept_tracks = tracks[:keep_first]
            to_shuffle = tracks[keep_first:]
        else:
            kept_tracks = []
            to_shuffle = tracks.copy()
        
        # Get audio features for all tracks
        features = TrackCache.load_track_features(sp, to_shuffle)
        
        # Separate tracks with and without features
        tracks_with_features = [t for t in to_shuffle if t in features]
        tracks_without_features = [t for t in to_shuffle if t not in features]
        
        if not tracks_with_features:
            logger.warning("No tracks have complete features, falling back to random shuffle")
            shuffled = to_shuffle.copy()
            random.shuffle(shuffled)
            return kept_tracks + shuffled
        
        # Start with a random track that has features
        shuffled = [random.choice(tracks_with_features)]
        remaining = [t for t in tracks_with_features if t != shuffled[0]]
        
        # Build the shuffled playlist while maintaining vibe
        while remaining:
            current_track = shuffled[-1]
            current_features = features[current_track]
            
            # Find the next track that best matches the current vibe
            next_track = self._find_next_track(current_features, remaining, features)
            if next_track:
                shuffled.append(next_track)
                remaining.remove(next_track)
            else:
                # If no good match found, pick a random remaining track
                next_track = random.choice(remaining)
                shuffled.append(next_track)
                remaining.remove(next_track)
        
        # Insert tracks without features at positions that maintain the vibe
        if tracks_without_features:
            logger.info(f"Inserting {len(tracks_without_features)} tracks without features")
            for track in tracks_without_features:
                position = self._find_best_insert_position(track, shuffled, features)
                shuffled.insert(position, track)
        
        return kept_tracks + shuffled

    def _find_best_insert_position(self, track: str, shuffled: List[str], features: Dict[str, Dict[str, float]]) -> int:
        """Find the best position to insert a track without features."""
        if not shuffled:
            return 0
        
        # Calculate average distances between consecutive tracks
        distances = []
        for i in range(len(shuffled) - 1):
            track1 = shuffled[i]
            track2 = shuffled[i + 1]
            if track1 in features and track2 in features:
                distance = self._calculate_distance(features[track1], features[track2])
                distances.append((i, distance))
        
        if not distances:
            return random.randint(0, len(shuffled))
        
        # Find the position with the largest gap (most suitable for random insertion)
        max_gap_position = max(distances, key=lambda x: x[1])[0]
        return max_gap_position + 1 