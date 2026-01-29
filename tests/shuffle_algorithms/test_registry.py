"""
Tests for ShuffleRegistry.

ShuffleRegistry maintains a collection of shuffle algorithms and provides
methods to register, retrieve, and list available algorithms.
"""

import pytest
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.shuffle_algorithms.basic import BasicShuffle
from shuffify.shuffle_algorithms.balanced import BalancedShuffle
from shuffify.shuffle_algorithms.percentage import PercentageShuffle
from shuffify.shuffle_algorithms.stratified import StratifiedShuffle
from shuffify.shuffle_algorithms import ShuffleAlgorithm


class TestShuffleRegistryGetAlgorithm:
    """Test getting algorithms by name."""

    def test_get_basic_shuffle(self):
        """Should return BasicShuffle class."""
        algo_class = ShuffleRegistry.get_algorithm('BasicShuffle')
        assert algo_class is BasicShuffle

    def test_get_balanced_shuffle(self):
        """Should return BalancedShuffle class."""
        algo_class = ShuffleRegistry.get_algorithm('BalancedShuffle')
        assert algo_class is BalancedShuffle

    def test_get_percentage_shuffle(self):
        """Should return PercentageShuffle class."""
        algo_class = ShuffleRegistry.get_algorithm('PercentageShuffle')
        assert algo_class is PercentageShuffle

    def test_get_stratified_shuffle(self):
        """Should return StratifiedShuffle class."""
        algo_class = ShuffleRegistry.get_algorithm('StratifiedShuffle')
        assert algo_class is StratifiedShuffle

    def test_get_unknown_algorithm_raises(self):
        """Should raise ValueError for unknown algorithm."""
        with pytest.raises(ValueError) as exc_info:
            ShuffleRegistry.get_algorithm('UnknownShuffle')

        assert "Unknown shuffle algorithm" in str(exc_info.value)
        assert "UnknownShuffle" in str(exc_info.value)

    def test_algorithm_class_is_instantiable(self):
        """Retrieved algorithm class should be instantiable."""
        algo_class = ShuffleRegistry.get_algorithm('BasicShuffle')
        instance = algo_class()

        assert isinstance(instance, BasicShuffle)
        assert hasattr(instance, 'shuffle')


class TestShuffleRegistryGetAvailableAlgorithms:
    """Test getting all available algorithms."""

    def test_returns_dict(self):
        """Should return a dictionary."""
        algorithms = ShuffleRegistry.get_available_algorithms()
        assert isinstance(algorithms, dict)

    def test_contains_all_algorithms(self):
        """Should contain all four default algorithms."""
        algorithms = ShuffleRegistry.get_available_algorithms()

        assert 'BasicShuffle' in algorithms
        assert 'BalancedShuffle' in algorithms
        assert 'PercentageShuffle' in algorithms
        assert 'StratifiedShuffle' in algorithms

    def test_returns_copy(self):
        """Should return a copy, not the internal dict."""
        algorithms1 = ShuffleRegistry.get_available_algorithms()
        algorithms2 = ShuffleRegistry.get_available_algorithms()

        # Modifying one shouldn't affect the other
        algorithms1['test'] = 'value'
        assert 'test' not in algorithms2

    def test_values_are_classes(self):
        """Dictionary values should be algorithm classes."""
        algorithms = ShuffleRegistry.get_available_algorithms()

        for name, algo_class in algorithms.items():
            assert isinstance(algo_class, type)
            # Each class should be instantiable and have required methods
            instance = algo_class()
            assert hasattr(instance, 'shuffle')
            assert hasattr(instance, 'name')


class TestShuffleRegistryListAlgorithms:
    """Test listing algorithms with metadata."""

    def test_returns_list(self):
        """Should return a list."""
        algorithms = ShuffleRegistry.list_algorithms()
        assert isinstance(algorithms, list)

    def test_list_has_correct_length(self):
        """Should list all visible algorithms."""
        algorithms = ShuffleRegistry.list_algorithms()
        # All 4 algorithms should be visible by default
        assert len(algorithms) == 4

    def test_list_contains_metadata(self):
        """Each entry should have required metadata fields."""
        algorithms = ShuffleRegistry.list_algorithms()

        for algo in algorithms:
            assert 'name' in algo
            assert 'class_name' in algo
            assert 'description' in algo
            assert 'parameters' in algo

    def test_list_order(self):
        """Algorithms should be in the defined display order."""
        algorithms = ShuffleRegistry.list_algorithms()

        # Check the expected order: Basic, Percentage, Balanced, Stratified
        class_names = [algo['class_name'] for algo in algorithms]

        assert class_names[0] == 'BasicShuffle'
        assert class_names[1] == 'PercentageShuffle'
        assert class_names[2] == 'BalancedShuffle'
        assert class_names[3] == 'StratifiedShuffle'

    def test_metadata_accuracy(self):
        """Metadata should match the actual algorithm properties."""
        algorithms = ShuffleRegistry.list_algorithms()

        # Find BasicShuffle entry
        basic = next(a for a in algorithms if a['class_name'] == 'BasicShuffle')

        assert basic['name'] == 'Basic'
        assert 'keep_first' in basic['parameters']


class TestShuffleRegistryRegister:
    """Test registering new algorithms."""

    def test_register_new_algorithm(self):
        """Should be able to register a new algorithm."""

        # Create a mock algorithm class
        class TestShuffle:
            @property
            def name(self):
                return "Test"

            @property
            def description(self):
                return "Test algorithm"

            @property
            def parameters(self):
                return {}

            @property
            def requires_features(self):
                return False

            def shuffle(self, tracks, features=None, **kwargs):
                return [t['uri'] for t in tracks]

        # Register it
        ShuffleRegistry.register(TestShuffle)

        # Should be retrievable
        algo_class = ShuffleRegistry.get_algorithm('TestShuffle')
        assert algo_class is TestShuffle

        # Clean up: remove from registry to not affect other tests
        del ShuffleRegistry._algorithms['TestShuffle']

    def test_register_uses_class_name(self):
        """Registration should use __name__ as the key."""

        class CustomNameShuffle:
            @property
            def name(self):
                return "Custom Display Name"

            @property
            def description(self):
                return "A custom algorithm"

            @property
            def parameters(self):
                return {}

            @property
            def requires_features(self):
                return False

            def shuffle(self, tracks, features=None, **kwargs):
                return [t['uri'] for t in tracks]

        ShuffleRegistry.register(CustomNameShuffle)

        # Should use class name, not display name
        algo_class = ShuffleRegistry.get_algorithm('CustomNameShuffle')
        assert algo_class is CustomNameShuffle

        # Clean up
        del ShuffleRegistry._algorithms['CustomNameShuffle']


class TestShuffleRegistryProtocol:
    """Test that registered algorithms follow the ShuffleAlgorithm protocol."""

    def test_all_algorithms_have_name(self):
        """All algorithms should have a name property."""
        algorithms = ShuffleRegistry.get_available_algorithms()

        for name, algo_class in algorithms.items():
            instance = algo_class()
            assert hasattr(instance, 'name')
            assert isinstance(instance.name, str)
            assert len(instance.name) > 0

    def test_all_algorithms_have_description(self):
        """All algorithms should have a description property."""
        algorithms = ShuffleRegistry.get_available_algorithms()

        for name, algo_class in algorithms.items():
            instance = algo_class()
            assert hasattr(instance, 'description')
            assert isinstance(instance.description, str)
            assert len(instance.description) > 0

    def test_all_algorithms_have_parameters(self):
        """All algorithms should have a parameters property."""
        algorithms = ShuffleRegistry.get_available_algorithms()

        for name, algo_class in algorithms.items():
            instance = algo_class()
            assert hasattr(instance, 'parameters')
            assert isinstance(instance.parameters, dict)

    def test_all_algorithms_have_requires_features(self):
        """All algorithms should have a requires_features property."""
        algorithms = ShuffleRegistry.get_available_algorithms()

        for name, algo_class in algorithms.items():
            instance = algo_class()
            assert hasattr(instance, 'requires_features')
            assert isinstance(instance.requires_features, bool)

    def test_all_algorithms_have_shuffle_method(self):
        """All algorithms should have a shuffle method."""
        algorithms = ShuffleRegistry.get_available_algorithms()

        for name, algo_class in algorithms.items():
            instance = algo_class()
            assert hasattr(instance, 'shuffle')
            assert callable(instance.shuffle)

    def test_all_algorithms_shuffle_correctly(self, sample_tracks):
        """All algorithms should return valid shuffle results."""
        algorithms = ShuffleRegistry.get_available_algorithms()
        original_uris = {t['uri'] for t in sample_tracks}

        for name, algo_class in algorithms.items():
            instance = algo_class()
            result = instance.shuffle(sample_tracks)

            # Should return list of URIs
            assert isinstance(result, list)
            assert len(result) == len(sample_tracks)

            # Should contain all original URIs
            assert set(result) == original_uris
