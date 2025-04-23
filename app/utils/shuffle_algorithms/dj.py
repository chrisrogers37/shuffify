from typing import List, Optional, Dict, Any, Tuple
from spotipy import Spotify
from . import ShuffleAlgorithm
from app.services.track_cache import TrackCache
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

class DJShuffle(ShuffleAlgorithm):
    """Sorts tracks by BPM and key using the Camelot wheel system for optimal DJ mixing."""
    
    # Camelot wheel mapping from Spotify key to Camelot notation
    # Based on the lookup table where mode 1 = Major, mode 0 = Minor
    CAMELOT_MAPPING = {
        0: {'major': '8B', 'minor': '5A'},    # C
        1: {'major': '3B', 'minor': '12A'},   # C#/Db
        2: {'major': '10B', 'minor': '7A'},   # D
        3: {'major': '5B', 'minor': '2A'},    # D#/Eb
        4: {'major': '12B', 'minor': '9A'},   # E
        5: {'major': '7B', 'minor': '4A'},    # F
        6: {'major': '2B', 'minor': '11A'},   # F#/Gb
        7: {'major': '9B', 'minor': '6A'},    # G
        8: {'major': '4B', 'minor': '1A'},    # G#/Ab
        9: {'major': '11B', 'minor': '8A'},   # A
        10: {'major': '6B', 'minor': '3A'},   # A#/Bb
        11: {'major': '1B', 'minor': '10A'},  # B
    }
    
    @property
    def name(self) -> str:
        return "DJ"
    
    @property
    def description(self) -> str:
        return "Sorts tracks by BPM and key using the Camelot wheel system for optimal DJ mixing."
    
    @property
    def parameters(self) -> dict:
        return {
            'keep_first': {
                'type': 'integer',
                'description': 'Number of tracks to keep at start',
                'default': 0,
                'min': 0
            },
            'bpm_tolerance': {
                'type': 'float',
                'description': 'Maximum BPM shift for beatmatching (±BPM)',
                'default': 4.0,
                'min': 0.0,
                'max': 20.0
            }
        }
    
    def _get_camelot_key(self, key: int, mode: int) -> str:
        """Convert Spotify key and mode to Camelot wheel notation."""
        if key is None or mode is None or key == -1:
            return '0A'  # Unknown key
        
        # Get the appropriate Camelot notation based on mode
        # mode = 1 for major, mode = 0 for minor
        camelot_key = None
        if mode == 1:  # Major
            camelot_key = self.CAMELOT_MAPPING[key]['major']
        else:  # Minor (mode == 0)
            camelot_key = self.CAMELOT_MAPPING[key]['minor']
            
        return camelot_key if camelot_key is not None else '0A'
    
    def _can_reach_bpm(self, bpm1: float, bpm2: float) -> bool:
        """Check if two tracks can reach each other's BPM within the tolerance range.
        This means bpm1 + tolerance >= bpm2 - tolerance."""
        return (bpm1 + self.bpm_tolerance) >= (bpm2 - self.bpm_tolerance)
    
    def _create_bpm_groups(self, track_features: List[Tuple[float, str, str]]) -> List[List[Tuple[float, str, str]]]:
        """Create groups of tracks where each track can reach the next track's BPM within tolerance."""
        if not track_features:
            return []

        # Sort all tracks by BPM first
        track_features.sort(key=lambda x: x[0])
        
        groups = []
        current_group = [track_features[0]]  # Start first group with first track
        
        for i in range(1, len(track_features)):
            current = track_features[i]
            previous = current_group[-1]  # Compare with the last track in the current group
            
            # Check if current track can reach the previous track's BPM within tolerance
            if self._can_reach_bpm(previous[0], current[0]):
                current_group.append(current)
            else:
                # Sort the current group by Camelot key before adding to groups
                current_group.sort(key=lambda x: x[1])
                groups.append(current_group)
                current_group = [current]  # Start new group with current track
        
        # Add the last group
        if current_group:
            current_group.sort(key=lambda x: x[1])
            groups.append(current_group)
        
        return groups
    
    def _sort_tracks(self, tracks: List[str], features: Dict[str, Dict[str, float]]) -> List[str]:
        """Sort tracks by BPM groups and Camelot key for optimal DJ mixing."""
        track_features = []
        
        # First pass: collect valid features and handle missing data
        for track_uri in tracks:
            track_feature = features.get(track_uri, {})
            tempo = track_feature.get('tempo')
            key = track_feature.get('key')
            mode = track_feature.get('mode')
            
            # Handle missing features more gracefully
            if tempo is None:
                # Use the average BPM if available, otherwise use a default value
                valid_tempos = [f.get('tempo', 0) for f in features.values() if f.get('tempo') is not None]
                tempo = sum(valid_tempos) / len(valid_tempos) if valid_tempos else 120.0  # Default to 120 BPM if no data
            
            if key is None or mode is None:
                # Use '8B' for major and '5A' for minor as they're common keys
                camelot_key = '8B' if mode == 1 else '5A'
            else:
                camelot_key = self._get_camelot_key(key, mode)
            
            track_features.append((tempo, camelot_key, track_uri))
        
        # Create BPM groups where each track can reach the next track's BPM
        bpm_groups = self._create_bpm_groups(track_features)
        
        # Flatten the groups back into a single list
        sorted_tracks = []
        for group in bpm_groups:
            sorted_tracks.extend(track_uri for _, _, track_uri in group)
        
        return sorted_tracks
    
    def shuffle(self, tracks: List[Dict[str, Any]], sp: Optional[Spotify] = None, **kwargs) -> List[str]:
        """Shuffle tracks using DJ-style mixing based on BPM and key.
        
        Args:
            tracks: List of track dictionaries with basic metadata
            sp: Spotify client for fetching audio features
            **kwargs: Additional parameters including playlist_id
            
        Returns:
            List of shuffled track URIs
        """
        try:
            if not sp:
                logger.error("Spotify client is required for DJ shuffle")
                return [track['uri'] for track in tracks]
                
            playlist_id = kwargs.get('playlist_id')
            if not playlist_id:
                logger.error("playlist_id is required for DJ shuffle")
                return [track['uri'] for track in tracks]
                
            # Get track features from cache
            features = TrackCache.load_track_features(sp, playlist_id, tracks)
            
            # Log feature availability
            total_tracks = len(tracks)
            tracks_with_features = len(features)
            logger.info(f"DJ shuffle: {tracks_with_features}/{total_tracks} tracks have required features")
            
            # Filter out tracks with missing features
            valid_tracks = []
            invalid_tracks = []
            
            for track in tracks:
                track_uri = track['uri']
                track_features = features.get(track_uri, {})
                if track_uri in features and all(track_features.get(f) is not None 
                                               for f in ['tempo', 'key', 'mode']):
                    valid_tracks.append(track_uri)
                else:
                    invalid_tracks.append(track_uri)
                    missing_features = [f for f in ['tempo', 'key', 'mode'] 
                                      if track_features.get(f) is None]
                    logger.warning(f"Track {track_uri} missing required features: {missing_features}")
            
            if not valid_tracks:
                logger.warning("No tracks with valid features found for DJ shuffle")
                return [track['uri'] for track in tracks]
                
            # Sort valid tracks by BPM and key
            sorted_tracks = self._sort_tracks(valid_tracks, features)
            
            # Append invalid tracks at the end
            sorted_tracks.extend(invalid_tracks)
            
            return sorted_tracks
            
        except Exception as e:
            logger.error(f"Error in DJ shuffle: {str(e)}")
            return [track['uri'] for track in tracks] 