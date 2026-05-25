from typing import Dict, Type, List
from . import ShuffleAlgorithm
from .basic import BasicShuffle
from .balanced import BalancedShuffle
from .percentage import PercentageShuffle
from .stratified import StratifiedShuffle
from .artist_spacing import ArtistSpacingShuffle
from .album_sequence import AlbumSequenceShuffle
from .newest_first import NewestFirstShuffle


class ShuffleRegistry:
    """Registry for shuffle algorithms."""

    _algorithms: Dict[str, Type[ShuffleAlgorithm]] = {
        "BasicShuffle": BasicShuffle,
        "BalancedShuffle": BalancedShuffle,
        "PercentageShuffle": PercentageShuffle,
        "StratifiedShuffle": StratifiedShuffle,
        "ArtistSpacingShuffle": ArtistSpacingShuffle,
        "AlbumSequenceShuffle": AlbumSequenceShuffle,
        "NewestFirstShuffle": NewestFirstShuffle,
    }

    @classmethod
    def register(cls, algorithm_class: Type[ShuffleAlgorithm]) -> None:
        """Register a new shuffle algorithm."""
        cls._algorithms[algorithm_class.__name__] = algorithm_class

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
        """List all algorithms with their metadata."""
        result = []
        desired_order = [
            BasicShuffle,
            PercentageShuffle,
            BalancedShuffle,
            StratifiedShuffle,
            ArtistSpacingShuffle,
            AlbumSequenceShuffle,
            NewestFirstShuffle,
        ]

        for algo_class in desired_order:
            if algo_class.__name__ in cls._algorithms:
                algo = algo_class()
                result.append(
                    {
                        "name": algo.name,
                        "class_name": algo_class.__name__,
                        "description": algo.description,
                        "parameters": algo.parameters,
                    }
                )
        return result
