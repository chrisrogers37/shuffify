import random
from typing import List, Dict, Any, Optional
from collections import defaultdict
from . import ShuffleAlgorithm


class AlbumSequenceShuffle(ShuffleAlgorithm):
    """Keep album tracks together but shuffle the order of albums."""

    @property
    def name(self) -> str:
        return "Album Sequence"

    @property
    def description(self) -> str:
        return (
            "Keeps tracks from the same album together in their "
            "original order, but shuffles which album plays first."
        )

    @property
    def parameters(self) -> dict:
        return {
            "shuffle_within_albums": {
                "type": "string",
                "description": "Also shuffle track order within each album",
                "default": "no",
                "options": ["no", "yes"],
            }
        }

    @property
    def requires_features(self) -> bool:
        return False

    def _get_album_name(self, track: Dict[str, Any]) -> str:
        """Extract album name from a track."""
        album = track.get("album")
        if isinstance(album, dict):
            return album.get("name", "Unknown Album")
        album_name = track.get("album_name")
        if album_name:
            return album_name
        return "Unknown Album"

    def shuffle(
        self,
        tracks: List[Dict[str, Any]],
        features: Optional[Dict[str, Dict[str, Any]]] = None,
        **kwargs,
    ) -> List[str]:
        """
        Shuffle album order while keeping album tracks together.

        Args:
            tracks: List of track dictionaries from Spotify API.
            features: Unused.
            **kwargs: Additional parameters.
                - shuffle_within_albums: "yes" or "no" (default "no").

        Returns:
            List of shuffled track URIs.
        """
        shuffle_within = kwargs.get("shuffle_within_albums", "no") == "yes"

        uris = [t["uri"] for t in tracks if t.get("uri")]
        if len(uris) <= 1:
            return uris

        # Group tracks by album, preserving insertion order
        album_groups = defaultdict(list)
        album_order = []
        for t in tracks:
            uri = t.get("uri")
            if not uri:
                continue
            album = self._get_album_name(t)
            if album not in album_groups:
                album_order.append(album)
            album_groups[album].append(uri)

        # Shuffle album order
        random.shuffle(album_order)

        # Optionally shuffle within albums
        if shuffle_within:
            for album in album_order:
                random.shuffle(album_groups[album])

        # Concatenate
        result = []
        for album in album_order:
            result.extend(album_groups[album])

        return result
