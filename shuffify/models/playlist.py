import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from shuffify.spotify.api import SpotifyAPI, playlist_item_count

logger = logging.getLogger(__name__)


@dataclass
class Playlist:
    """Represents a Spotify playlist with its tracks."""

    id: str
    name: str
    owner_id: str
    description: Optional[str] = None
    total_tracks: Optional[int] = None
    public: Optional[bool] = None
    tracks: List[Dict[str, Any]] = field(default_factory=list)
    # Always empty: shuffify reads no audio features. to_dict keeps the key
    # because the Workshop context contract requires it.
    audio_features: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            logger.error("Playlist ID is required")
            raise ValueError("Playlist ID is required")

    @classmethod
    def from_spotify(
        cls,
        api: "SpotifyAPI",
        playlist_id: str,
    ) -> "Playlist":
        """Load a playlist and its tracks."""
        playlist_data = api.get_playlist(playlist_id)
        raw_tracks = api.get_playlist_tracks(playlist_id)

        tracks = []
        for track in raw_tracks:
            if not track.get("id") or not track.get("uri"):
                continue
            tracks.append(
                {
                    "id": track["id"],
                    "name": track["name"],
                    "uri": track["uri"],
                    "duration_ms": track.get("duration_ms"),
                    "is_local": track.get("is_local", False),
                    "added_at": track.get("added_at"),
                    "artists": [
                        artist.get("name") for artist in track.get("artists", [])
                    ],
                    "artist_urls": [
                        artist.get("external_urls", {}).get("spotify")
                        for artist in track.get("artists", [])
                    ],
                    "album_name": track.get("album", {}).get("name"),
                    "album_image_url": track.get("album", {})
                    .get("images", [{}])[0]
                    .get("url"),
                    "track_url": track.get("external_urls", {}).get("spotify"),
                }
            )

        return cls(
            id=playlist_data["id"],
            name=playlist_data["name"],
            owner_id=playlist_data["owner"]["id"],
            description=playlist_data.get("description"),
            total_tracks=playlist_item_count(playlist_data, default=None),
            public=playlist_data.get("public"),
            tracks=tracks,
        )

    def get_track_uris(self) -> List[str]:
        """Return all track URIs."""
        return [track["uri"] for track in self.tracks if track.get("uri")]

    def to_dict(self) -> Dict[str, Any]:
        """Convert Playlist to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "owner_id": self.owner_id,
            "description": self.description,
            "total_tracks": self.total_tracks,
            "public": self.public,
            "tracks": self.tracks,
            "audio_features": self.audio_features,
        }

    def __len__(self) -> int:
        return len(self.tracks)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self.tracks[index]

    def __iter__(self):
        return iter(self.tracks)

    def __str__(self) -> str:
        return f"{self.name} ({self.id}) - {len(self.tracks)} tracks"
