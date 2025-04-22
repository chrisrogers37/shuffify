from typing import Dict, Type, List
from . import ShuffleAlgorithm
from .basic import BasicShuffle
from .vibe_based import VibeShuffle
from .balanced import BalancedShuffle
from .percentage import PercentageShuffle
from .stratified import StratifiedSample

class ShuffleRegistry:
    """Registry for shuffle algorithms."""
    
    _algorithms: Dict[str, Type[ShuffleAlgorithm]] = {}
    _hidden_algorithms = {'VibeShuffle'}  # Add algorithms to hide from UI here
    
    @classmethod
    def register(cls, algorithm_class: Type[ShuffleAlgorithm]) -> None:
        """Register a new shuffle algorithm."""
        cls._algorithms[algorithm_class.__name__] = algorithm_class
    
    @classmethod
    def get_algorithm(cls, name: str) -> ShuffleAlgorithm:
        """Get an instance of a registered algorithm."""
        if name not in cls._algorithms:
            raise ValueError(f"Unknown shuffle algorithm: {name}")
        return cls._algorithms[name]()
    
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
        desired_order = [BasicShuffle, PercentageShuffle, BalancedShuffle, StratifiedSample]
        
        # Create metadata for visible algorithms in the specified order
        for algo_class in desired_order:
            if algo_class.__name__ in visible_algorithms:
                algo = algo_class()
                result.append({
                    'name': algo.name,
                    'class_name': algo_class.__name__,
                    'description': algo.description,
                    'parameters': algo.parameters
                })
        return result

# Register all algorithms
ShuffleRegistry.register(BasicShuffle)
ShuffleRegistry.register(VibeShuffle)  # Still registered but hidden from UI
ShuffleRegistry.register(BalancedShuffle)
ShuffleRegistry.register(PercentageShuffle)
ShuffleRegistry.register(StratifiedSample) 