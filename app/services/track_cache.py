from typing import List, Dict, Optional, Any
from flask import session
import logging

logger = logging.getLogger(__name__)

class TrackCache:
    """Service for caching track metadata and audio features in the user's session."""
    
    FEATURES_KEY = 'track_features_cache'
    
    @classmethod
    def get_features(cls, playlist_id: str, track_uris: List[str]) -> Dict[str, Dict[str, float]]:
        """Get features for tracks from the session cache for a specific playlist."""
        cache = session.get(cls.FEATURES_KEY, {})
        playlist_cache = cache.get(playlist_id, {})
        return {
            uri: features
            for uri, features in playlist_cache.items()
            if uri in track_uris and features and all(v is not None for v in features.values())
        }
    
    @classmethod
    def cache_features(cls, playlist_id: str, features_data: Dict[str, dict]) -> None:
        """Cache track features in the session for a specific playlist."""
        try:
            # Get existing cache or initialize new one
            cache = session.get(cls.FEATURES_KEY, {})
            playlist_cache = cache.get(playlist_id, {})
            
            # Update cache with new features
            for track_uri, track_features in features_data.items():
                if track_features:  # Only cache if we have valid features
                    playlist_cache[track_uri] = {
                        'tempo': track_features.get('tempo'),
                        'energy': track_features.get('energy'),
                        'valence': track_features.get('valence'),
                        'danceability': track_features.get('danceability'),
                        'key': track_features.get('key'),
                        'mode': track_features.get('mode')
                    }
                    # Only keep features if all values are present
                    if any(v is None for v in playlist_cache[track_uri].values()):
                        del playlist_cache[track_uri]
            
            # Store updated cache in session
            cache[playlist_id] = playlist_cache
            session[cls.FEATURES_KEY] = cache
            
        except Exception as e:
            logger.error(f"Error caching features in session: {str(e)}")
            raise
    
    @classmethod
    def load_track_features(cls, sp, playlist_id: str, tracks: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
        """Load track features, using cache when possible and fetching missing data.
        
        Args:
            sp: Spotify client
            playlist_id: ID of the playlist being processed
            tracks: List of track objects from the initial playlist fetch
        """
        # First, try to get features from session cache
        track_uris = [track['uri'] for track in tracks]
        features = cls.get_features(playlist_id, track_uris)
        
        # Identify tracks that still need features fetched
        missing_tracks = [t['uri'] for t in tracks if t['uri'] not in features]
        
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
                                        'danceability': track_features.get('danceability'),
                                        'key': track_features.get('key'),
                                        'mode': track_features.get('mode')
                                    }
                                    # Only keep features if all required values are present
                                    if any(v is None for v in [new_features[track_uri]['tempo'], 
                                                             new_features[track_uri]['key'],
                                                             new_features[track_uri]['mode']]):
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
            
            # Cache any new features we successfully fetched
            if new_features:
                cls.cache_features(playlist_id, new_features)
                features.update(new_features)
        
        return features
    
    @classmethod
    def verify_session(cls) -> bool:
        """Verify that the session cache is valid."""
        try:
            cache = session.get(cls.FEATURES_KEY, {})
            return isinstance(cache, dict)
        except Exception as e:
            logger.error(f"Error verifying session cache: {str(e)}")
            return False
    
    @classmethod
    def clear_cache(cls, playlist_id: Optional[str] = None) -> None:
        """Clear the cache for a specific playlist or all playlists."""
        try:
            if playlist_id:
                cache = session.get(cls.FEATURES_KEY, {})
                if playlist_id in cache:
                    del cache[playlist_id]
                    session[cls.FEATURES_KEY] = cache
            else:
                session.pop(cls.FEATURES_KEY, None)
        except Exception as e:
            logger.error(f"Error clearing cache: {str(e)}")
            raise 