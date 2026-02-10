import random
from typing import List, Dict, Any, Optional
from . import ShuffleAlgorithm
from .utils import extract_uris, split_keep_first


class BasicShuffle(ShuffleAlgorithm):
    """Randomly shuffle your playlist while optionally keeping tracks in place at the top."""

    @property
    def name(self) -> str:
        return "Basic"

    @property
    def description(self) -> str:
        return "Randomly shuffle your playlist while optionally keeping tracks in place at the top."

    @property
    def parameters(self) -> dict:
        return {
            "keep_first": {
                "type": "integer",
                "description": "Number of tracks to keep at start",
                "default": 0,
                "min": 0,
            }
        }

    @property
    def requires_features(self) -> bool:
        return False

    def shuffle(
        self,
        tracks: List[Dict[str, Any]],
        features: Optional[Dict[str, Dict[str, Any]]] = None,
        **kwargs
    ) -> List[str]:
        """
        Randomly shuffle tracks while optionally keeping some at the start.

        Args:
            tracks: List of track dictionaries from Spotify API
            features: Optional dictionary of track URIs to audio features (unused in basic shuffle)
            **kwargs: Additional parameters
                - keep_first: Number of tracks to keep at start

        Returns:
            List of shuffled track URIs
        """
        keep_first = kwargs.get("keep_first", 0)

        uris = extract_uris(tracks)

        if len(uris) <= 1 or keep_first >= len(uris):
            return uris

        kept_uris, to_shuffle = split_keep_first(uris, keep_first)
        random.shuffle(to_shuffle)
        return kept_uris + to_shuffle
