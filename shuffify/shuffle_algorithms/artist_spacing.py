import random
from typing import List, Dict, Any, Optional
from collections import defaultdict
from . import ShuffleAlgorithm


class ArtistSpacingShuffle(ShuffleAlgorithm):
    """Shuffle tracks so the same artist never appears back-to-back."""

    @property
    def name(self) -> str:
        return "Artist Spacing"

    @property
    def description(self) -> str:
        return (
            "Shuffles tracks while ensuring the same artist "
            "never appears back-to-back for a more varied listening experience."
        )

    @property
    def parameters(self) -> dict:
        return {
            "min_spacing": {
                "type": "integer",
                "description": "Minimum tracks between same artist",
                "default": 1,
                "min": 1,
                "max": 10,
            }
        }

    @property
    def requires_features(self) -> bool:
        return False

    def _get_artist_name(self, track: Dict[str, Any]) -> str:
        """Extract the primary artist name from a track."""
        artists = track.get("artists", [])
        if artists and isinstance(artists, list):
            first = artists[0]
            if isinstance(first, dict):
                return first.get("name", "Unknown")
            return str(first)
        return "Unknown"

    def shuffle(
        self,
        tracks: List[Dict[str, Any]],
        features: Optional[Dict[str, Dict[str, Any]]] = None,
        **kwargs,
    ) -> List[str]:
        """
        Shuffle tracks ensuring minimum spacing between same artist.

        Uses a greedy algorithm: builds the result one track at a time,
        always picking from tracks whose artist hasn't appeared in the
        last `min_spacing` positions. Falls back to the least-recently-
        used artist if no valid candidate exists.

        Args:
            tracks: List of track dictionaries from Spotify API.
            features: Unused.
            **kwargs: Additional parameters.
                - min_spacing: Minimum tracks between same artist.

        Returns:
            List of shuffled track URIs.
        """
        min_spacing = kwargs.get("min_spacing", 1)

        uris = [t["uri"] for t in tracks if t.get("uri")]
        if len(uris) <= 1:
            return uris

        # Build a mapping from URI to artist name
        uri_to_artist = {}
        for t in tracks:
            uri = t.get("uri")
            if uri:
                uri_to_artist[uri] = self._get_artist_name(t)

        # Group URIs by artist and shuffle within each group
        artist_tracks = defaultdict(list)
        for uri in uris:
            artist_tracks[uri_to_artist[uri]].append(uri)
        for group in artist_tracks.values():
            random.shuffle(group)

        # Build result using greedy spacing approach
        result = []
        recent_artists = []  # Track recent artists for spacing
        remaining = list(uris)
        random.shuffle(remaining)

        while remaining:
            # Find candidates that respect spacing constraint
            blocked_artists = set(recent_artists[-min_spacing:])
            candidates = [
                u for u in remaining if uri_to_artist[u] not in blocked_artists
            ]

            if candidates:
                chosen = candidates[0]
            else:
                # No valid candidate; pick the one whose artist appeared
                # longest ago to maximize spacing
                best = None
                best_dist = -1
                for u in remaining:
                    a = uri_to_artist[u]
                    # Distance since last occurrence
                    dist = len(result)
                    for i in range(len(recent_artists) - 1, -1, -1):
                        if recent_artists[i] == a:
                            dist = len(recent_artists) - 1 - i
                            break
                    if dist > best_dist:
                        best_dist = dist
                        best = u
                chosen = best

            result.append(chosen)
            recent_artists.append(uri_to_artist[chosen])
            remaining.remove(chosen)

        return result
