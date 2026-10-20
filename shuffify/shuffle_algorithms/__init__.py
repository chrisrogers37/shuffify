import logging
from typing import Any, Dict, List, Protocol

logger = logging.getLogger(__name__)


class ShuffleAlgorithm(Protocol):
    """Interface for shuffle algorithms."""

    @property
    def name(self) -> str:
        """Name of the algorithm."""
        ...

    @property
    def description(self) -> str:
        """Description of what the algorithm does."""
        ...

    @property
    def parameters(self) -> dict:
        """Parameters that can be configured for this algorithm."""
        ...

    def shuffle(
        self,
        tracks: List[Dict[str, Any]],
        **kwargs
    ) -> List[str]:
        """
        Shuffle the tracks according to the algorithm.

        Args:
            tracks: List of track dictionaries from Spotify API
            **kwargs: Additional parameters specific to the algorithm

        Returns:
            List of shuffled track URIs
        """
        ...
