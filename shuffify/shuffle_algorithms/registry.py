from typing import Dict, Type, List
from . import ShuffleAlgorithm
from .basic import BasicShuffle
from .balanced import BalancedShuffle
from .percentage import PercentageShuffle
from .stratified import StratifiedShuffle


class ShuffleRegistry:
    """Registry for shuffle algorithms."""

    _algorithms: Dict[str, Type[ShuffleAlgorithm]] = {
        "BasicShuffle": BasicShuffle,
        "BalancedShuffle": BalancedShuffle,
        "PercentageShuffle": PercentageShuffle,
        "StratifiedShuffle": StratifiedShuffle,
    }
    _hidden_algorithms = set()  # Empty set since we want all algorithms visible

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
        """List all visible algorithms with their metadata."""
        result = []
        # Filter out hidden algorithms first
        visible_algorithms = {
            name: algo_class
            for name, algo_class in cls._algorithms.items()
            if name not in cls._hidden_algorithms
        }

        # Define the desired order of algorithms
        desired_order = [
            BasicShuffle,
            PercentageShuffle,
            BalancedShuffle,
            StratifiedShuffle,
        ]

        # Create metadata for visible algorithms in the specified order
        for algo_class in desired_order:
            if algo_class.__name__ in visible_algorithms:
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


# Register all algorithms
ShuffleRegistry.register(BasicShuffle)
ShuffleRegistry.register(BalancedShuffle)
ShuffleRegistry.register(PercentageShuffle)
ShuffleRegistry.register(StratifiedShuffle)
