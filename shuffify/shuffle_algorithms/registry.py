from typing import Dict, List, Type

from . import ShuffleAlgorithm
from .album_sequence import AlbumSequenceShuffle
from .artist_spacing import ArtistSpacingShuffle
from .balanced import BalancedShuffle
from .basic import BasicShuffle
from .newest_first import NewestFirstShuffle
from .percentage import PercentageShuffle
from .stratified import StratifiedShuffle


class ShuffleRegistry:
    """Registry for shuffle algorithms."""

    # In display order: list_algorithms() returns them in this order.
    _algorithms: Dict[str, Type[ShuffleAlgorithm]] = {
        "BasicShuffle": BasicShuffle,
        "PercentageShuffle": PercentageShuffle,
        "BalancedShuffle": BalancedShuffle,
        "StratifiedShuffle": StratifiedShuffle,
        "ArtistSpacingShuffle": ArtistSpacingShuffle,
        "AlbumSequenceShuffle": AlbumSequenceShuffle,
        "NewestFirstShuffle": NewestFirstShuffle,
    }

    @classmethod
    def get_algorithm(cls, name: str) -> Type[ShuffleAlgorithm]:
        """Get a shuffle algorithm by name."""
        if name not in cls._algorithms:
            raise ValueError(f"Unknown shuffle algorithm: {name}")
        return cls._algorithms[name]

    @classmethod
    def get_available_algorithms(cls) -> Dict[str, Type[ShuffleAlgorithm]]:
        """Get all available shuffle algorithms."""
        return cls._algorithms.copy()

    @classmethod
    def list_algorithms(cls) -> List[dict]:
        """List all algorithms with their metadata, in display order."""
        result = []
        for class_name, algo_class in cls._algorithms.items():
            algo = algo_class()
            result.append(
                {
                    "name": algo.name,
                    "class_name": class_name,
                    "description": algo.description,
                    "parameters": algo.parameters,
                }
            )
        return result
