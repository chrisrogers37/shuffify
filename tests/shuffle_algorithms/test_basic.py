"""
Tests for BasicShuffle algorithm.

BasicShuffle randomly shuffles a playlist while optionally keeping
a specified number of tracks at the start in their original position.
"""

import pytest
from shuffify.shuffle_algorithms.basic import BasicShuffle


class TestBasicShuffleProperties:
    """Test BasicShuffle metadata properties."""

    def test_name(self):
        """Algorithm name should be 'Basic'."""
        algorithm = BasicShuffle()
        assert algorithm.name == "Basic"

    def test_description(self):
        """Description should explain the algorithm."""
        algorithm = BasicShuffle()
        assert "shuffle" in algorithm.description.lower()
        assert "optionally keeping" in algorithm.description.lower()

    def test_requires_features(self):
        """BasicShuffle should not require audio features."""
        algorithm = BasicShuffle()
        assert algorithm.requires_features is False

    def test_parameters(self):
        """Parameters should include keep_first."""
        algorithm = BasicShuffle()
        params = algorithm.parameters
        assert 'keep_first' in params
        assert params['keep_first']['type'] == 'integer'
        assert params['keep_first']['default'] == 0
        assert params['keep_first']['min'] == 0


class TestBasicShuffleBasicFunctionality:
    """Test basic shuffling functionality."""

    def test_shuffle_returns_all_tracks(self, sample_tracks):
        """Shuffle should return all track URIs."""
        algorithm = BasicShuffle()
        result = algorithm.shuffle(sample_tracks)

        original_uris = {t['uri'] for t in sample_tracks}
        result_uris = set(result)

        assert len(result) == len(sample_tracks)
        assert result_uris == original_uris

    def test_shuffle_returns_list_of_uris(self, sample_tracks):
        """Shuffle should return a list of URI strings."""
        algorithm = BasicShuffle()
        result = algorithm.shuffle(sample_tracks)

        assert isinstance(result, list)
        assert all(isinstance(uri, str) for uri in result)
        assert all(uri.startswith('spotify:track:') for uri in result)

    def test_shuffle_changes_order(self, sample_tracks):
        """Shuffle should change track order (statistically)."""
        algorithm = BasicShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        # Run multiple times to ensure shuffling actually happens
        different_order_count = 0
        for _ in range(10):
            result = algorithm.shuffle(sample_tracks)
            if result != original_uris:
                different_order_count += 1

        # Should get different order at least once in 10 tries
        assert different_order_count > 0

    def test_shuffle_is_deterministic_with_seed(self, sample_tracks):
        """Shuffle with same random seed should produce same result."""
        import random
        algorithm = BasicShuffle()

        random.seed(42)
        result1 = algorithm.shuffle(sample_tracks)

        random.seed(42)
        result2 = algorithm.shuffle(sample_tracks)

        assert result1 == result2


class TestBasicShuffleKeepFirst:
    """Test keep_first parameter functionality."""

    def test_keep_first_zero(self, sample_tracks):
        """keep_first=0 should shuffle all tracks."""
        algorithm = BasicShuffle()
        result = algorithm.shuffle(sample_tracks, keep_first=0)

        assert len(result) == len(sample_tracks)

    def test_keep_first_preserves_tracks(self, sample_tracks):
        """keep_first=N should preserve first N tracks in order."""
        algorithm = BasicShuffle()
        keep_count = 5
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, keep_first=keep_count)

        # First N tracks should be unchanged
        assert result[:keep_count] == original_uris[:keep_count]

        # Remaining tracks should all be present
        remaining_original = set(original_uris[keep_count:])
        remaining_result = set(result[keep_count:])
        assert remaining_result == remaining_original

    def test_keep_first_one(self, sample_tracks):
        """keep_first=1 should keep only the first track fixed."""
        algorithm = BasicShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, keep_first=1)

        assert result[0] == original_uris[0]
        assert len(result) == len(original_uris)

    def test_keep_first_all(self, sample_tracks):
        """keep_first >= total tracks should return original order."""
        algorithm = BasicShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, keep_first=len(sample_tracks))

        assert result == original_uris

    def test_keep_first_exceeds_total(self, sample_tracks):
        """keep_first > total tracks should return original order."""
        algorithm = BasicShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, keep_first=len(sample_tracks) + 100)

        assert result == original_uris


class TestBasicShuffleEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_tracks(self, empty_tracks):
        """Empty track list should return empty list."""
        algorithm = BasicShuffle()
        result = algorithm.shuffle(empty_tracks)

        assert result == []

    def test_single_track(self, single_track):
        """Single track should return unchanged."""
        algorithm = BasicShuffle()
        result = algorithm.shuffle(single_track)

        assert result == [single_track[0]['uri']]

    def test_two_tracks(self):
        """Two tracks should shuffle correctly."""
        algorithm = BasicShuffle()
        tracks = [
            {'uri': 'spotify:track:a'},
            {'uri': 'spotify:track:b'}
        ]

        result = algorithm.shuffle(tracks)

        assert len(result) == 2
        assert set(result) == {'spotify:track:a', 'spotify:track:b'}

    def test_tracks_with_missing_uri(self, tracks_with_missing_uri):
        """Tracks without valid URIs should be filtered out."""
        algorithm = BasicShuffle()
        result = algorithm.shuffle(tracks_with_missing_uri)

        # Should only include tracks with valid URIs
        assert len(result) == 3
        assert all(uri.startswith('spotify:track:valid') for uri in result)

    def test_features_parameter_ignored(self, sample_tracks, sample_audio_features):
        """Features parameter should be accepted but ignored."""
        algorithm = BasicShuffle()

        # Should not raise, features are ignored
        result = algorithm.shuffle(sample_tracks, features=sample_audio_features)

        assert len(result) == len(sample_tracks)


class TestBasicShuffleDefaultParameters:
    """Test default parameter behavior."""

    def test_default_keep_first_is_zero(self, sample_tracks):
        """Default keep_first should be 0 (shuffle all)."""
        algorithm = BasicShuffle()

        # Call without keep_first parameter
        result = algorithm.shuffle(sample_tracks)

        # Should shuffle all tracks (different order is expected)
        assert len(result) == len(sample_tracks)

    def test_kwargs_passed_correctly(self, sample_tracks):
        """Additional kwargs should not break the function."""
        algorithm = BasicShuffle()

        # Should accept and ignore unknown parameters
        result = algorithm.shuffle(sample_tracks, unknown_param=True, another=123)

        assert len(result) == len(sample_tracks)
