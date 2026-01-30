"""
Tests for BasicShuffle algorithm.

Tests cover basic shuffling, keep_first parameter, and edge cases.
"""

import pytest
import random
from collections import Counter

from shuffify.shuffle_algorithms.basic import BasicShuffle


class TestBasicShuffleProperties:
    """Tests for BasicShuffle metadata properties."""

    def test_name_is_basic(self):
        """Should return 'Basic' as name."""
        algo = BasicShuffle()
        assert algo.name == 'Basic'

    def test_description_is_present(self):
        """Should have a description."""
        algo = BasicShuffle()
        assert algo.description
        assert len(algo.description) > 10

    def test_parameters_includes_keep_first(self):
        """Should include keep_first parameter."""
        algo = BasicShuffle()
        params = algo.parameters

        assert 'keep_first' in params
        assert params['keep_first']['type'] == 'integer'
        assert params['keep_first']['default'] == 0
        assert params['keep_first']['min'] == 0

    def test_requires_features_is_false(self):
        """Should not require audio features."""
        algo = BasicShuffle()
        assert algo.requires_features is False


class TestBasicShuffleShuffle:
    """Tests for BasicShuffle.shuffle method."""

    @pytest.fixture
    def sample_tracks(self):
        """Sample tracks for testing."""
        return [
            {'uri': f'spotify:track:track{i}', 'name': f'Track {i}'}
            for i in range(10)
        ]

    @pytest.fixture
    def algorithm(self):
        """BasicShuffle instance."""
        return BasicShuffle()

    def test_shuffle_returns_all_uris(self, algorithm, sample_tracks):
        """Should return all URIs from input tracks."""
        result = algorithm.shuffle(sample_tracks)

        original_uris = {t['uri'] for t in sample_tracks}
        result_uris = set(result)

        assert original_uris == result_uris

    def test_shuffle_returns_list_of_strings(self, algorithm, sample_tracks):
        """Should return list of URI strings."""
        result = algorithm.shuffle(sample_tracks)

        assert isinstance(result, list)
        assert all(isinstance(uri, str) for uri in result)

    def test_shuffle_changes_order(self, algorithm, sample_tracks):
        """Should change the order of tracks (with high probability)."""
        random.seed(42)  # For reproducibility

        original_order = [t['uri'] for t in sample_tracks]
        result = algorithm.shuffle(sample_tracks)

        # With 10 tracks, probability of same order is 1/10! = very small
        assert result != original_order

    def test_shuffle_preserves_count(self, algorithm, sample_tracks):
        """Should preserve the number of tracks."""
        result = algorithm.shuffle(sample_tracks)
        assert len(result) == len(sample_tracks)

    def test_shuffle_empty_list(self, algorithm):
        """Should handle empty track list."""
        result = algorithm.shuffle([])
        assert result == []

    def test_shuffle_single_track(self, algorithm):
        """Should handle single track (no shuffle possible)."""
        tracks = [{'uri': 'spotify:track:single'}]
        result = algorithm.shuffle(tracks)
        assert result == ['spotify:track:single']


class TestBasicShuffleKeepFirst:
    """Tests for keep_first parameter."""

    @pytest.fixture
    def sample_tracks(self):
        """Sample tracks for testing."""
        return [
            {'uri': f'spotify:track:track{i}', 'name': f'Track {i}'}
            for i in range(10)
        ]

    @pytest.fixture
    def algorithm(self):
        """BasicShuffle instance."""
        return BasicShuffle()

    def test_keep_first_zero_shuffles_all(self, algorithm, sample_tracks):
        """keep_first=0 should shuffle all tracks."""
        random.seed(42)
        result = algorithm.shuffle(sample_tracks, keep_first=0)

        original = [t['uri'] for t in sample_tracks]
        assert result != original

    def test_keep_first_preserves_beginning(self, algorithm, sample_tracks):
        """keep_first should preserve first N tracks in order."""
        result = algorithm.shuffle(sample_tracks, keep_first=3)

        original_first_3 = [t['uri'] for t in sample_tracks[:3]]
        assert result[:3] == original_first_3

    def test_keep_first_shuffles_remainder(self, algorithm, sample_tracks):
        """keep_first should shuffle remaining tracks."""
        random.seed(42)
        result = algorithm.shuffle(sample_tracks, keep_first=3)

        original_rest = [t['uri'] for t in sample_tracks[3:]]
        result_rest = result[3:]

        # Remaining tracks should be shuffled (different order)
        assert set(result_rest) == set(original_rest)
        # High probability they're in different order
        assert result_rest != original_rest

    def test_keep_first_equals_length(self, algorithm, sample_tracks):
        """keep_first >= length should return original order."""
        result = algorithm.shuffle(sample_tracks, keep_first=10)
        original = [t['uri'] for t in sample_tracks]
        assert result == original

    def test_keep_first_greater_than_length(self, algorithm, sample_tracks):
        """keep_first > length should return original order."""
        result = algorithm.shuffle(sample_tracks, keep_first=20)
        original = [t['uri'] for t in sample_tracks]
        assert result == original

    def test_keep_first_all_but_one(self, algorithm, sample_tracks):
        """keep_first = length-1 should only move last track."""
        result = algorithm.shuffle(sample_tracks, keep_first=9)

        original_first_9 = [t['uri'] for t in sample_tracks[:9]]
        assert result[:9] == original_first_9
        # Last track is the only one that can move (but it's alone, so stays)
        assert result[9] == sample_tracks[9]['uri']


class TestBasicShuffleEdgeCases:
    """Edge case tests for BasicShuffle."""

    @pytest.fixture
    def algorithm(self):
        """BasicShuffle instance."""
        return BasicShuffle()

    def test_tracks_without_uri_are_skipped(self, algorithm):
        """Should skip tracks without URI."""
        tracks = [
            {'uri': 'spotify:track:1'},
            {'name': 'No URI'},  # Missing URI
            {'uri': 'spotify:track:2'},
        ]

        result = algorithm.shuffle(tracks)

        assert len(result) == 2
        assert 'spotify:track:1' in result
        assert 'spotify:track:2' in result

    def test_tracks_with_none_uri_are_skipped(self, algorithm):
        """Should skip tracks with None URI."""
        tracks = [
            {'uri': 'spotify:track:1'},
            {'uri': None},
            {'uri': 'spotify:track:2'},
        ]

        result = algorithm.shuffle(tracks)

        assert len(result) == 2

    def test_shuffle_is_random(self, algorithm):
        """Should produce different results on different runs."""
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(20)]

        results = [tuple(algorithm.shuffle(tracks)) for _ in range(10)]

        # Should have at least some unique orderings
        unique_results = set(results)
        assert len(unique_results) > 1

    def test_features_parameter_is_ignored(self, algorithm):
        """Should ignore features parameter (not used by basic shuffle)."""
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(5)]
        features = {'track0': {'tempo': 120}}

        result = algorithm.shuffle(tracks, features=features)

        assert len(result) == 5
