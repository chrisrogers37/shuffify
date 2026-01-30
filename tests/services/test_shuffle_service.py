"""
Tests for ShuffleService.

Tests cover algorithm listing, parameter parsing, and shuffle execution.
"""

import pytest
from unittest.mock import Mock, patch

from shuffify.services import (
    ShuffleService,
    ShuffleError,
    InvalidAlgorithmError,
    ParameterValidationError,
    ShuffleExecutionError,
)


class TestShuffleServiceListAlgorithms:
    """Tests for list_algorithms method."""

    def test_list_algorithms_returns_list(self):
        """Should return a list of algorithm metadata."""
        result = ShuffleService.list_algorithms()

        assert isinstance(result, list)
        assert len(result) > 0

    def test_list_algorithms_contains_basic_shuffle(self):
        """Should include BasicShuffle in the list."""
        result = ShuffleService.list_algorithms()

        names = [algo['class_name'] for algo in result]
        assert 'BasicShuffle' in names

    def test_list_algorithms_metadata_structure(self):
        """Each algorithm should have required metadata fields."""
        result = ShuffleService.list_algorithms()

        for algo in result:
            assert 'name' in algo
            assert 'class_name' in algo
            assert 'description' in algo
            assert 'parameters' in algo


class TestShuffleServiceGetAlgorithm:
    """Tests for get_algorithm method."""

    def test_get_algorithm_basic_shuffle(self):
        """Should return BasicShuffle instance."""
        algorithm = ShuffleService.get_algorithm('BasicShuffle')

        assert algorithm.name == 'Basic'
        assert hasattr(algorithm, 'shuffle')

    def test_get_algorithm_balanced_shuffle(self):
        """Should return BalancedShuffle instance."""
        algorithm = ShuffleService.get_algorithm('BalancedShuffle')

        assert algorithm.name == 'Balanced'

    def test_get_algorithm_percentage_shuffle(self):
        """Should return PercentageShuffle instance."""
        algorithm = ShuffleService.get_algorithm('PercentageShuffle')

        assert algorithm.name == 'Percentage'

    def test_get_algorithm_stratified_shuffle(self):
        """Should return StratifiedShuffle instance."""
        algorithm = ShuffleService.get_algorithm('StratifiedShuffle')

        assert algorithm.name == 'Stratified'

    def test_get_algorithm_invalid_name(self):
        """Should raise InvalidAlgorithmError for unknown algorithm."""
        with pytest.raises(InvalidAlgorithmError) as exc_info:
            ShuffleService.get_algorithm('NonExistentShuffle')
        assert "Invalid algorithm" in str(exc_info.value)

    def test_get_algorithm_empty_name(self):
        """Should raise InvalidAlgorithmError for empty name."""
        with pytest.raises(InvalidAlgorithmError):
            ShuffleService.get_algorithm('')


class TestShuffleServiceExecute:
    """Tests for execute method."""

    def test_execute_basic_shuffle(self, sample_tracks):
        """Should execute BasicShuffle and return URIs."""
        result = ShuffleService.execute('BasicShuffle', sample_tracks)

        assert isinstance(result, list)
        assert len(result) == len(sample_tracks)
        # All URIs should be present
        original_uris = {t['uri'] for t in sample_tracks}
        assert set(result) == original_uris

    def test_execute_with_parameters(self, sample_tracks):
        """Should pass parameters to algorithm."""
        # Keep first 3 tracks fixed
        result = ShuffleService.execute(
            'BasicShuffle',
            sample_tracks,
            params={'keep_first': 3}
        )

        # First 3 should remain in order
        original_first_3 = [t['uri'] for t in sample_tracks[:3]]
        assert result[:3] == original_first_3

    def test_execute_invalid_algorithm(self, sample_tracks):
        """Should raise InvalidAlgorithmError for unknown algorithm."""
        with pytest.raises(InvalidAlgorithmError):
            ShuffleService.execute('FakeAlgorithm', sample_tracks)

    def test_execute_empty_tracks(self):
        """Should handle empty track list."""
        result = ShuffleService.execute('BasicShuffle', [])

        assert result == []

    def test_execute_single_track(self):
        """Should handle single track (no shuffle possible)."""
        tracks = [{'uri': 'spotify:track:single', 'id': 'single'}]

        result = ShuffleService.execute('BasicShuffle', tracks)

        assert result == ['spotify:track:single']

    def test_execute_passes_spotify_client(self, sample_tracks, mock_spotify_client):
        """Should pass Spotify client to algorithm as 'sp' parameter."""
        with patch.object(ShuffleService, 'get_algorithm') as mock_get:
            mock_algo = Mock()
            mock_algo.shuffle.return_value = [t['uri'] for t in sample_tracks]
            mock_get.return_value = mock_algo

            ShuffleService.execute(
                'BasicShuffle',
                sample_tracks,
                spotify_client=mock_spotify_client
            )

            # Verify 'sp' was passed to shuffle
            call_kwargs = mock_algo.shuffle.call_args[1]
            assert 'sp' in call_kwargs
            assert call_kwargs['sp'] == mock_spotify_client


class TestShuffleServiceShuffleChangedOrder:
    """Tests for shuffle_changed_order method."""

    def test_shuffle_changed_order_true(self):
        """Should return True when order changed."""
        original = ['a', 'b', 'c']
        shuffled = ['c', 'a', 'b']

        result = ShuffleService.shuffle_changed_order(original, shuffled)

        assert result is True

    def test_shuffle_changed_order_false_same_order(self):
        """Should return False when order unchanged."""
        original = ['a', 'b', 'c']
        shuffled = ['a', 'b', 'c']

        result = ShuffleService.shuffle_changed_order(original, shuffled)

        assert result is False

    def test_shuffle_changed_order_false_empty(self):
        """Should return False for empty shuffled list."""
        original = ['a', 'b', 'c']
        shuffled = []

        result = ShuffleService.shuffle_changed_order(original, shuffled)

        assert result is False

    def test_shuffle_changed_order_both_empty(self):
        """Should return False when both empty."""
        result = ShuffleService.shuffle_changed_order([], [])

        assert result is False


class TestShuffleServicePrepareTracksForShuffle:
    """Tests for prepare_tracks_for_shuffle method."""

    def test_prepare_tracks_maintains_uri_order(self, sample_tracks):
        """Should order tracks according to URI list."""
        # Reverse the URI order
        reversed_uris = [t['uri'] for t in reversed(sample_tracks)]

        result = ShuffleService.prepare_tracks_for_shuffle(sample_tracks, reversed_uris)

        # Result should be in reversed order
        result_uris = [t['uri'] for t in result]
        assert result_uris == reversed_uris

    def test_prepare_tracks_handles_missing_uris(self, sample_tracks):
        """Should skip URIs not found in tracks."""
        # Include a URI that doesn't exist
        uris_with_missing = [sample_tracks[0]['uri'], 'spotify:track:nonexistent']

        result = ShuffleService.prepare_tracks_for_shuffle(sample_tracks, uris_with_missing)

        assert len(result) == 1
        assert result[0]['uri'] == sample_tracks[0]['uri']

    def test_prepare_tracks_empty_uris(self, sample_tracks):
        """Should return empty list for empty URIs."""
        result = ShuffleService.prepare_tracks_for_shuffle(sample_tracks, [])

        assert result == []

    def test_prepare_tracks_empty_tracks(self):
        """Should return empty list for empty tracks."""
        result = ShuffleService.prepare_tracks_for_shuffle([], ['uri1', 'uri2'])

        assert result == []
