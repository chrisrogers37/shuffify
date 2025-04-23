from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
import logging
from ..spotify.client import SpotifyClient

logger = logging.getLogger(__name__)

@dataclass
class Playlist:
    """Represents a Spotify playlist with its tracks and features."""
    
    # Basic playlist information
    id: str
    name: str
    owner_id: str
    description: Optional[str] = None
    
    # Track data
    tracks: List[Dict[str, Any]] = field(default_factory=list)
    audio_features: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        """Validate playlist data after initialization."""
        if not self.id:
            logger.error("Playlist ID is required")
            raise ValueError("Playlist ID is required")
    
    @classmethod
    def from_spotify(cls, client: SpotifyClient, playlist_id: str, include_features: bool = False) -> 'Playlist':
        """Create a Playlist instance from Spotify API."""
        try:
            # Get complete playlist data
            playlist_data = client.get_playlist_with_tracks(playlist_id, include_features)
            return cls.from_api_data(playlist_data)
        except Exception as e:
            logger.error(f"Error creating playlist from Spotify: {e}")
            raise
    
    @classmethod
    def from_api_data(cls, playlist_data: Dict[str, Any]) -> 'Playlist':
        """Create a Playlist instance from Spotify API data."""
        try:
            return cls(
                id=playlist_data['id'],
                name=playlist_data['name'],
                owner_id=playlist_data['owner']['id'],
                description=playlist_data.get('description'),
                tracks=playlist_data.get('tracks', []),
                audio_features=playlist_data.get('audio_features', {})
            )
        except KeyError as e:
            logger.error(f"Missing required playlist data: {e}")
            raise ValueError(f"Invalid playlist data: missing {e}")
    
    def get_track_uris(self) -> List[str]:
        """Get list of track URIs in the playlist."""
        return [track['uri'] for track in self.tracks if track.get('uri')]
    
    def get_tracks_with_features(self) -> List[Dict[str, Any]]:
        """Get tracks that have audio features."""
        return [
            {**track, 'features': self.audio_features.get(track['uri'])}
            for track in self.tracks
            if track.get('uri') in self.audio_features
        ]
    
    def get_track(self, uri: str) -> Optional[Dict[str, Any]]:
        """Get a specific track by URI."""
        for track in self.tracks:
            if track.get('uri') == uri:
                return track
        return None
    
    def get_track_with_features(self, uri: str) -> Optional[Dict[str, Any]]:
        """Get a specific track with its features by URI."""
        track = self.get_track(uri)
        if track and uri in self.audio_features:
            return {**track, 'features': self.audio_features[uri]}
        return track
    
    def has_features(self) -> bool:
        """Check if the playlist has audio features loaded."""
        return bool(self.audio_features)
    
    def get_feature_stats(self) -> Dict[str, Any]:
        """Get statistics about the playlist's audio features."""
        if not self.audio_features:
            return {}
        
        stats = {
            'tempo': {'min': float('inf'), 'max': float('-inf'), 'avg': 0},
            'energy': {'min': float('inf'), 'max': float('-inf'), 'avg': 0},
            'valence': {'min': float('inf'), 'max': float('-inf'), 'avg': 0},
            'danceability': {'min': float('inf'), 'max': float('-inf'), 'avg': 0}
        }
        
        count = 0
        for features in self.audio_features.values():
            count += 1
            for key in stats:
                if key in features:
                    value = features[key]
                    stats[key]['min'] = min(stats[key]['min'], value)
                    stats[key]['max'] = max(stats[key]['max'], value)
                    stats[key]['avg'] += value
        
        if count > 0:
            for key in stats:
                stats[key]['avg'] /= count
        
        return stats
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert playlist to dictionary format."""
        return {
            'id': self.id,
            'name': self.name,
            'owner_id': self.owner_id,
            'description': self.description,
            'tracks': self.tracks,
            'audio_features': self.audio_features
        }
    
    def __str__(self) -> str:
        """String representation of playlist."""
        return f"{self.name} ({self.id}) - {len(self.tracks)} tracks"
    
    def __len__(self) -> int:
        """Number of tracks in the playlist."""
        return len(self.tracks)
    
    def __getitem__(self, index: int) -> Dict[str, Any]:
        """Get track by index."""
        return self.tracks[index]
    
    def __iter__(self):
        """Iterate over tracks."""
        return iter(self.tracks) 