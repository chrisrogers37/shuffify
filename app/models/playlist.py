from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)

@dataclass
class Playlist:
    """Represents a Spotify playlist with tracks and optional audio features."""
    
    id: str
    name: str
    owner_id: str
    description: Optional[str] = None
    tracks: List[Dict[str, Any]] = field(default_factory=list)
    audio_features: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    followers: int = 0
    
    def __post_init__(self) -> None:
        if not self.id:
            logger.error("Playlist ID is required")
            raise ValueError("Playlist ID is required")

    @classmethod
    def from_spotify(cls, spotify_client: "SpotifyClient", playlist_id: str, include_features: bool = False) -> "Playlist":
        """Load a playlist and optionally its audio features."""
        playlist_data = spotify_client.get_playlist(playlist_id)
        raw_tracks = spotify_client.get_playlist_tracks(playlist_id)

        tracks = []
        for track in raw_tracks:
            if not track.get('id') or not track.get('uri'):
                continue
            tracks.append({
                'id': track['id'],
                'name': track['name'],
                'uri': track['uri'],
                'duration_ms': track.get('duration_ms'),
                'is_local': track.get('is_local', False),
                'artists': [artist.get('name') for artist in track.get('artists', [])],
                'artist_urls': [artist.get('external_urls', {}).get('spotify') for artist in track.get('artists', [])],
                'album_name': track.get('album', {}).get('name'),
                'album_image_url': track.get('album', {}).get('images', [{}])[0].get('url'),
                'track_url': track.get('external_urls', {}).get('spotify'),
            })

        audio_features = {}
        if include_features and tracks:
            track_ids = [track['id'] for track in tracks]
            if track_ids:
                audio_features = spotify_client.get_track_audio_features(track_ids)

        followers = playlist_data.get('followers', {}).get('total', 0)

        return cls(
            id=playlist_data['id'],
            name=playlist_data['name'],
            owner_id=playlist_data['owner']['id'],
            description=playlist_data.get('description'),
            tracks=tracks,
            audio_features=audio_features,
            followers=followers
        )

    def get_track_uris(self) -> List[str]:
        """Return all track URIs."""
        return [track['uri'] for track in self.tracks if track.get('uri')]

    def get_tracks_with_features(self) -> List[Dict[str, Any]]:
        """Return tracks, attaching audio features when available."""
        enriched_tracks = []
        for track in self.tracks:
            track_id = track['id']
            enriched_track = {**track}
            enriched_track['features'] = self.audio_features.get(track_id, {})
            enriched_tracks.append(enriched_track)
        return enriched_tracks

    def get_track(self, uri: str) -> Optional[Dict[str, Any]]:
        """Retrieve a track by its URI."""
        return next((track for track in self.tracks if track.get('uri') == uri), None)

    def get_track_with_features(self, uri: str) -> Optional[Dict[str, Any]]:
        """Retrieve a track and its features."""
        track = self.get_track(uri)
        if not track:
            return None
        track_id = track.get('id')
        return {**track, 'features': self.audio_features.get(track_id, {})}

    def has_features(self) -> bool:
        """Check if features were loaded."""
        return bool(self.audio_features)

    def get_feature_stats(self) -> Dict[str, Any]:
        """Aggregate simple statistics over key audio features."""
        if not self.audio_features:
            return {}

        feature_keys = ['tempo', 'energy', 'valence', 'danceability']
        stats = {key: {'min': float('inf'), 'max': float('-inf'), 'sum': 0} for key in feature_keys}
        count = 0

        for features in self.audio_features.values():
            count += 1
            for key in feature_keys:
                if key in features:
                    value = features[key]
                    stats[key]['min'] = min(stats[key]['min'], value)
                    stats[key]['max'] = max(stats[key]['max'], value)
                    stats[key]['sum'] += value

        if count == 0:
            return {}

        for key in stats:
            stats[key]['avg'] = stats[key]['sum'] / count
            del stats[key]['sum']

        return stats

    def to_dict(self) -> Dict[str, Any]:
        """Convert Playlist to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'owner_id': self.owner_id,
            'description': self.description,
            'tracks': self.tracks,
            'audio_features': self.audio_features,
            'followers': self.followers
        }

    def __len__(self) -> int:
        return len(self.tracks)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self.tracks[index]

    def __iter__(self):
        return iter(self.tracks)

    def __str__(self) -> str:
        return f"{self.name} ({self.id}) - {len(self.tracks)} tracks"
