from typing import List, Dict, Optional
from flask import session
import logging

logger = logging.getLogger(__name__)

class TrackCache:
    """Service for caching track metadata and audio features in the user's session."""
    
    FEATURES_KEY = 'track_features_cache'
    
    @classmethod
    def get_features(cls, track_uris: List[str]) -> Dict[str, Dict[str, float]]:
        """Get features for tracks from the session cache."""
        cache = session.get(cls.FEATURES_KEY, {})
        return {
            uri: features
            for uri, features in cache.items()
            if uri in track_uris and features and all(v is not None for v in features.values())
        }
    
    @classmethod
    def cache_features(cls, features_data: Dict[str, dict]) -> None:
        """Cache track features in the session."""
        try:
            # Get existing cache or initialize new one
            cache = session.get(cls.FEATURES_KEY, {})
            
            # Update cache with new features
            for track_uri, track_features in features_data.items():
                if track_features:  # Only cache if we have valid features
                    cache[track_uri] = {
                        'tempo': track_features.get('tempo'),
                        'energy': track_features.get('energy'),
                        'valence': track_features.get('valence'),
                        'danceability': track_features.get('danceability')
                    }
                    # Only keep features if all values are present
                    if any(v is None for v in cache[track_uri].values()):
                        del cache[track_uri]
            
            # Store updated cache in session
            session[cls.FEATURES_KEY] = cache
            
        except Exception as e:
            logger.error(f"Error caching features in session: {str(e)}")
            raise
    
    @classmethod
    def load_track_features(cls, sp, tracks: List[str]) -> Dict[str, Dict[str, float]]:
        """Load track features, using cache when possible and fetching missing data."""
        # First, try to get features from session cache
        features = cls.get_features(tracks)
        
        # Identify tracks that need features fetched
        missing_tracks = [t for t in tracks if t not in features]
        
        if missing_tracks:
            logger.info(f"Fetching features for {len(missing_tracks)} tracks from Spotify API")
            new_features = {}
            
            try:
                # Process tracks in batches of 100 (Spotify API limit)
                for i in range(0, len(missing_tracks), 100):
                    batch = missing_tracks[i:i + 100]
                    try:
                        batch_features = sp.audio_features(batch)
                        
                        if batch_features:
                            for track_uri, track_features in zip(batch, batch_features):
                                if track_features:
                                    # Log specific missing features
                                    missing = [f for f in ['tempo', 'energy', 'valence', 'danceability'] 
                                             if track_features.get(f) is None]
                                    if missing:
                                        logger.warning(f"Track {track_uri} missing features: {missing}")
                                    
                                    new_features[track_uri] = {
                                        'tempo': track_features.get('tempo'),
                                        'energy': track_features.get('energy'),
                                        'valence': track_features.get('valence'),
                                        'danceability': track_features.get('danceability')
                                    }
                                    # Only keep features if all values are present
                                    if any(v is None for v in new_features[track_uri].values()):
                                        del new_features[track_uri]
                                        logger.warning(f"Track {track_uri} removed due to missing required features")
                    except Exception as e:
                        logger.error(f"Error fetching features for batch {i//100 + 1}: {str(e)}")
                        # Log the specific error details
                        if hasattr(e, 'response'):
                            logger.error(f"Response status: {e.response.status_code}")
                            logger.error(f"Response body: {e.response.text}")
                        # Continue with next batch instead of failing completely
                        continue
                    
            except Exception as e:
                logger.error(f"Error in feature fetching process: {str(e)}")
                # Don't raise the exception, return what we have
            
            # Cache the new features
            if new_features:
                try:
                    cls.cache_features(new_features)
                    features.update(new_features)
                except Exception as e:
                    logger.error(f"Error caching features: {str(e)}")
        
        # Log summary of feature availability
        total_tracks = len(tracks)
        tracks_with_features = len(features)
        logger.info(f"Feature summary: {tracks_with_features}/{total_tracks} tracks have complete features")
        
        return features

    @classmethod
    def verify_session(cls) -> bool:
        """Verify that session storage is working correctly."""
        try:
            # Try to store and retrieve a test value
            test_key = '_test_cache_key'
            test_value = {'test': 'value'}
            
            # Store test value
            session[test_key] = test_value
            
            # Try to retrieve it
            retrieved = session.get(test_key)
            
            # Clean up
            if test_key in session:
                del session[test_key]
            
            # Verify retrieval worked
            if retrieved != test_value:
                logger.error("Session storage verification failed: retrieved value doesn't match stored value")
                return False
                
            logger.info("Session storage verification successful")
            return True
            
        except Exception as e:
            logger.error(f"Session storage verification failed with error: {str(e)}")
            return False
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear the feature cache."""
        if cls.FEATURES_KEY in session:
            del session[cls.FEATURES_KEY]
            logger.info("Feature cache cleared") 