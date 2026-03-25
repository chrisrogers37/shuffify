import random
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from . import ShuffleAlgorithm
from .utils import extract_uris

# Timezone-aware minimum datetime used as fallback for missing/bad added_at
_DATETIME_MIN = datetime.min.replace(tzinfo=timezone.utc)


class NewestFirstShuffle(ShuffleAlgorithm):
    """Reorder tracks by date added, newest first, with optional jitter."""

    @property
    def name(self) -> str:
        return "Newest First"

    @property
    def description(self) -> str:
        return (
            "Reorders tracks so the most recently added appear "
            "at the top, with adjustable jitter to keep things "
            "feeling natural."
        )

    @property
    def parameters(self) -> dict:
        return {
            "jitter": {
                "type": "integer",
                "description": (
                    "Window size for local shuffling "
                    "(1 = exact date sort, higher = more variation)"
                ),
                "default": 5,
                "min": 1,
                "max": 50,
            }
        }

    @property
    def requires_features(self) -> bool:
        return False

    def _parse_added_at(self, value: Optional[str]) -> datetime:
        """Parse ISO 8601 added_at timestamp.

        Returns datetime.min for missing or malformed values so they
        sort to the end (oldest) in descending order.
        """
        if not value:
            return _DATETIME_MIN
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return _DATETIME_MIN

    def shuffle(
        self,
        tracks: List[Dict[str, Any]],
        features: Optional[Dict[str, Dict[str, Any]]] = None,
        **kwargs,
    ) -> List[str]:
        """
        Sort tracks by added_at descending, then shuffle within windows.

        Tracks with missing or malformed added_at are treated as oldest
        and placed at the end.

        Args:
            tracks: List of track dictionaries from Spotify API.
            features: Unused.
            **kwargs: Additional parameters.
                - jitter: Window size for local shuffling (default 5).

        Returns:
            List of track URIs in newest-first order with jitter.
        """
        jitter = kwargs.get("jitter", 5)

        uris = extract_uris(tracks)
        if len(uris) <= 1:
            return uris

        # Build URI -> added_at mapping
        uri_to_added = {}
        for t in tracks:
            uri = t.get("uri")
            if uri:
                uri_to_added[uri] = self._parse_added_at(
                    t.get("added_at")
                )

        # Sort by added_at descending (newest first)
        sorted_uris = sorted(
            uris,
            key=lambda u: uri_to_added.get(u, _DATETIME_MIN),
            reverse=True,
        )

        # Apply jitter: shuffle within windows
        if jitter <= 1:
            return sorted_uris

        result = []
        for i in range(0, len(sorted_uris), jitter):
            window = sorted_uris[i: i + jitter]
            random.shuffle(window)
            result.extend(window)

        return result
