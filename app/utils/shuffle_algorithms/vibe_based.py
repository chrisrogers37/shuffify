from typing import List, Optional, Dict, Any
import numpy as np
from spotipy import Spotify
from . import ShuffleAlgorithm
import logging
import random

logger = logging.getLogger(__name__)

class VibeShuffle(ShuffleAlgorithm):
    """Shuffle tracks based on their audio features to create smooth transitions."""
    
    FEATURE_WEIGHTS = {
        'tempo': 0.3,
        'energy': 0.3,
        'valence': 0.2,
        'danceability': 0.2
    }
    
    @property
    def name(self) -> str:
        return "Vibe Shuffle"
    
    @property
    def description(self) -> str:
        return "Reorder tracks based on their musical characteristics for smoother transitions."
    
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
                'description': 'How smooth the transitions should be (0.0 to 1.0)',
                'default': 0.5,
                'min': 0.0,
                'max': 1.0
            }
        }
    
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
                diff = features1[feature] - features2[feature]
                distance += weight * (diff ** 2)
            return np.sqrt(distance)
        except Exception as e:
            logger.error(f"Error calculating distance: {str(e)}")
            return float('inf')
    
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
            
            logger.debug(f"Calculated distances range: min={min(distances.values()):.3f}, max={max(distances.values()):.3f}")
            
            # Invert and scale the distances to get similarity scores
            max_dist = max(distances.values()) or 1.0
            min_dist = min(distances.values()) or 0.0
            
            # Normalize distances to [0, 1] range and apply smoothness
            if max_dist > min_dist:
                normalized_distances = {
                    uri: (max_dist - dist) / (max_dist - min_dist)
                    for uri, dist in distances.items()
                }
            else:
                normalized_distances = {uri: 1.0 for uri in distances.keys()}
            
            # Apply smoothness factor
            # High smoothness (1.0) = use normalized distances as is (prefer similar tracks)
            # Low smoothness (0.0) = flatten probabilities (more random)
            probabilities = {
                uri: (score * smoothness) + ((1 - smoothness) * 0.5)
                for uri, score in normalized_distances.items()
            }
            
            # Normalize probabilities
            total = sum(probabilities.values())
            if total == 0:
                logger.warning("Zero total probability, returning random track")
                return random.choice(list(available_features.keys()))
                
            probabilities = {uri: p/total for uri, p in probabilities.items()}
            
            # Choose next track based on probabilities
            uris = list(probabilities.keys())
            probs = list(probabilities.values())
            
            # Log probability distribution
            sorted_probs = sorted(zip(uris, probs), key=lambda x: x[1], reverse=True)
            logger.debug(f"Probability distribution (smoothness={smoothness}):")
            for uri, prob in sorted_probs[:3]:  # Log top 3 candidates
                logger.debug(f"  Track {uri[-8:]}: {prob:.3f}")
            
            selected = np.random.choice(uris, p=probs)
            logger.debug(f"Selected track {selected[-8:]} with probability {probabilities[selected]:.3f}")
            
            return selected
            
        except Exception as e:
            logger.error(f"Error finding next track: {str(e)}")
            return random.choice(list(available_features.keys()))
    
    def shuffle(self, tracks: List[str], sp: Optional[Spotify] = None, **kwargs) -> List[str]:
        if not sp:
            raise ValueError("Spotify client is required for vibe-based shuffling")
            
        keep_first = kwargs.get('keep_first', 0)
        smoothness = kwargs.get('smoothness', 0.5)
        
        logger.info(f"Starting vibe shuffle with {len(tracks)} tracks (keep_first={keep_first}, smoothness={smoothness})")
        
        if len(tracks) <= 1 or keep_first >= len(tracks):
            return tracks
            
        # Get audio features for all tracks using base class method
        logger.info("Fetching audio features...")
        all_features = self.get_track_features(sp, tracks)
        
        # Debug log the first few features to verify structure
        sample_tracks = list(all_features.keys())[:3] if all_features else []
        for track in sample_tracks:
            logger.info(f"Sample features for track {track[-8:]}: {all_features[track]}")
        
        logger.info(f"Retrieved features for {len(all_features)} tracks out of {len(tracks)}")
        
        if not all_features:
            logger.warning("Could not get audio features for any tracks, falling back to random shuffle")
            result = tracks.copy()
            if keep_first < len(tracks):
                random.shuffle(result[keep_first:])
            return result
        
        # Split tracks into kept and to_shuffle
        kept_tracks = tracks[:keep_first] if keep_first > 0 else []
        to_shuffle = tracks[keep_first:]
        
        # Handle tracks that don't have features within to_shuffle portion
        valid_tracks = [t for t in to_shuffle if t in all_features]
        invalid_tracks = [t for t in to_shuffle if t not in all_features]
        
        # Debug log some valid and invalid tracks
        if valid_tracks:
            logger.info(f"Sample valid track: {valid_tracks[0][-8:]} with features: {all_features[valid_tracks[0]]}")
        if invalid_tracks:
            logger.info(f"Sample invalid tracks: {[t[-8:] for t in invalid_tracks[:3]]}")
        
        logger.info(f"Found {len(valid_tracks)} tracks with features and {len(invalid_tracks)} without in shuffle portion")
        
        if len(valid_tracks) <= 1:
            logger.warning("Not enough tracks with features for vibe-based shuffling")
            result = kept_tracks.copy()
            shuffled = to_shuffle.copy()
            random.shuffle(shuffled)
            result.extend(shuffled)
            return result
        
        # Start building result with kept tracks
        result = kept_tracks.copy()
        
        # Set up available tracks for shuffling
        available_features = {uri: all_features[uri] for uri in valid_tracks}
        logger.info(f"Starting to shuffle {len(available_features)} tracks with features")
        
        # Always start with a random track for the first transition
        if available_features:
            # Pick random candidates
            candidates = random.sample(list(available_features.keys()), 
                                    min(3, len(available_features)))
            
            # Debug log the candidates
            for candidate in candidates:
                logger.info(f"Initial candidate {candidate[-8:]}: {available_features[candidate]}")
            
            if kept_tracks and kept_tracks[-1] in all_features:
                # If we have a kept track with features, use it to choose from candidates
                reference_features = all_features[kept_tracks[-1]]
                logger.info(f"Using kept track {kept_tracks[-1][-8:]} as reference with features: {reference_features}")
                current = self._find_next_track(
                    reference_features,
                    {uri: available_features[uri] for uri in candidates},
                    smoothness
                )
            else:
                # No reference track, pick completely randomly from candidates
                current = random.choice(candidates)
                logger.info(f"Randomly selected initial track {current[-8:]}")
            
            result.append(current)
            del available_features[current]
            reference_track = current
        else:
            reference_track = None
        
        # Build the shuffled sequence
        while available_features:
            if not reference_track or reference_track not in all_features:
                current = random.choice(list(available_features.keys()))
                logger.info(f"Using random track {current[-8:]} (no valid reference)")
            else:
                logger.info(f"Finding next track based on reference {reference_track[-8:]}")
                logger.info(f"Reference features: {all_features[reference_track]}")
                logger.info(f"Available tracks: {len(available_features)}")
                
                current = self._find_next_track(
                    all_features[reference_track],
                    available_features,
                    smoothness
                )
                logger.info(f"Selected track {current[-8:]} with features: {available_features[current]}")
            
            result.append(current)
            reference_track = current
            del available_features[current]
        
        # Randomly insert tracks without features
        if invalid_tracks:
            logger.info(f"Inserting {len(invalid_tracks)} tracks without features at random positions")
            random.shuffle(invalid_tracks)
            for track in invalid_tracks:
                insert_pos = random.randint(keep_first, len(result))
                result.insert(insert_pos, track)
        
        # Verify we haven't accidentally preserved the original order
        if len(result) > keep_first + 1:
            original_order = tracks[keep_first:keep_first+10]
            shuffled_order = result[keep_first:keep_first+10]
            consecutive_matches = sum(1 for i in range(len(shuffled_order)-1) 
                                   if shuffled_order[i] == original_order[i] 
                                   and shuffled_order[i+1] == original_order[i+1])
            
            logger.info(f"Original order (first 10): {[t[-8:] for t in original_order]}")
            logger.info(f"Shuffled order (first 10): {[t[-8:] for t in shuffled_order]}")
            logger.info(f"Consecutive matches: {consecutive_matches}")
            
            if consecutive_matches > 2:
                logger.warning("Detected potential preservation of original order, applying additional shuffle")
                to_reshuffle = result[keep_first:]
                random.shuffle(to_reshuffle)
                result = kept_tracks + to_reshuffle
        
        logger.info(f"Completed vibe shuffle. Final playlist length: {len(result)}")
        return result 