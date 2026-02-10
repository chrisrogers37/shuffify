import random
import heapq
from typing import List, Dict, Any, Optional
from collections import defaultdict
from . import ShuffleAlgorithm
from .utils import extract_uris


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

        uris = extract_uris(tracks)
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

        # Use a max-heap approach: always pick from the artist with the
        # most remaining tracks (that isn't blocked by spacing). This
        # prevents the algorithm from painting itself into a corner.
        # Heap entries: (-count, random_tiebreaker, artist_name)
        heap = []
        for artist, tracks_list in artist_tracks.items():
            heapq.heappush(heap, (-len(tracks_list), random.random(), artist))

        result = []
        recent_artists = []  # Sliding window of recent artist names
        cooldown = []  # Artists waiting out their spacing cooldown

        while heap or cooldown:
            # Move artists off cooldown if enough tracks have been placed
            still_waiting = []
            for wait_until, entry in cooldown:
                if len(result) >= wait_until:
                    heapq.heappush(heap, entry)
                else:
                    still_waiting.append((wait_until, entry))
            cooldown = still_waiting

            if not heap:
                # All artists are on cooldown — impossible to satisfy
                # spacing. Pick the one that comes off cooldown soonest.
                cooldown.sort(key=lambda x: x[0])
                _, entry = cooldown.pop(0)
                neg_count, tiebreaker, artist = entry
            else:
                neg_count, tiebreaker, artist = heapq.heappop(heap)

            # Take one track from this artist
            chosen = artist_tracks[artist].pop()
            result.append(chosen)
            recent_artists.append(artist)

            # Put artist back on cooldown if they have remaining tracks
            remaining_count = -neg_count - 1
            if remaining_count > 0:
                release_at = len(result) + min_spacing
                cooldown.append(
                    (release_at, (-remaining_count, random.random(), artist))
                )

        return result
