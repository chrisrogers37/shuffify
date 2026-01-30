"""
Tests for PercentageShuffle algorithm.

Tests cover percentage-based partial shuffling with location options.
"""

import pytest
import random

from shuffify.shuffle_algorithms.percentage import PercentageShuffle


class TestPercentageShuffleProperties:
    """Tests for PercentageShuffle metadata properties."""

    def test_name_is_percentage(self):
        """Should return 'Percentage' as name."""
        algo = PercentageShuffle()
        assert algo.name == 'Percentage'

    def test_description_mentions_portion(self):
        """Should describe partial shuffling."""
        algo = PercentageShuffle()
        assert 'portion' in algo.description.lower() or 'shuffle' in algo.description.lower()

    def test_parameters_includes_shuffle_percentage(self):
        """Should include shuffle_percentage parameter."""
        algo = PercentageShuffle()
        params = algo.parameters

        assert 'shuffle_percentage' in params
        assert params['shuffle_percentage']['type'] == 'float'
        assert params['shuffle_percentage']['default'] == 50.0
        assert params['shuffle_percentage']['min'] == 0.0
        assert params['shuffle_percentage']['max'] == 100.0

    def test_parameters_includes_shuffle_location(self):
        """Should include shuffle_location parameter."""
        algo = PercentageShuffle()
        params = algo.parameters

        assert 'shuffle_location' in params
        assert params['shuffle_location']['type'] == 'string'
        assert params['shuffle_location']['default'] == 'front'
        assert 'front' in params['shuffle_location']['options']
        assert 'back' in params['shuffle_location']['options']

    def test_requires_features_is_false(self):
        """Should not require audio features."""
        algo = PercentageShuffle()
        assert algo.requires_features is False


class TestPercentageShuffleShuffle:
    """Tests for PercentageShuffle.shuffle method."""

    @pytest.fixture
    def sample_tracks(self):
        """10 sample tracks for percentage testing."""
        return [
            {'uri': f'spotify:track:track{i}', 'name': f'Track {i}'}
            for i in range(10)
        ]

    @pytest.fixture
    def algorithm(self):
        """PercentageShuffle instance."""
        return PercentageShuffle()

    def test_shuffle_returns_all_uris(self, algorithm, sample_tracks):
        """Should return all URIs from input tracks."""
        result = algorithm.shuffle(sample_tracks)

        original_uris = {t['uri'] for t in sample_tracks}
        result_uris = set(result)

        assert original_uris == result_uris

    def test_shuffle_preserves_count(self, algorithm, sample_tracks):
        """Should preserve the number of tracks."""
        result = algorithm.shuffle(sample_tracks)
        assert len(result) == len(sample_tracks)

    def test_shuffle_empty_list(self, algorithm):
        """Should handle empty track list."""
        result = algorithm.shuffle([])
        assert result == []

    def test_shuffle_single_track(self, algorithm):
        """Should handle single track."""
        tracks = [{'uri': 'spotify:track:single'}]
        result = algorithm.shuffle(tracks)
        assert result == ['spotify:track:single']


class TestPercentageShufflePercentage:
    """Tests for shuffle_percentage parameter."""

    @pytest.fixture
    def sample_tracks(self):
        """10 sample tracks."""
        return [
            {'uri': f'spotify:track:track{i}', 'name': f'Track {i}'}
            for i in range(10)
        ]

    @pytest.fixture
    def algorithm(self):
        """PercentageShuffle instance."""
        return PercentageShuffle()

    def test_50_percent_shuffles_half(self, algorithm, sample_tracks):
        """50% should shuffle 5 tracks and keep 5."""
        random.seed(42)
        result = algorithm.shuffle(sample_tracks, shuffle_percentage=50.0, shuffle_location='front')

        # First 5 should be shuffled, last 5 should be in order
        original_last_5 = [t['uri'] for t in sample_tracks[5:]]
        assert result[5:] == original_last_5

        # First 5 should be a shuffle of the original first 5
        original_first_5 = set(t['uri'] for t in sample_tracks[:5])
        assert set(result[:5]) == original_first_5

    def test_0_percent_no_shuffle(self, algorithm, sample_tracks):
        """0% should return original order."""
        result = algorithm.shuffle(sample_tracks, shuffle_percentage=0.0)
        original = [t['uri'] for t in sample_tracks]
        assert result == original

    def test_100_percent_shuffles_all(self, algorithm, sample_tracks):
        """100% should shuffle all tracks."""
        random.seed(42)
        result = algorithm.shuffle(sample_tracks, shuffle_percentage=100.0)

        original = [t['uri'] for t in sample_tracks]
        # All tracks shuffled (should be different order)
        assert set(result) == set(original)

    def test_30_percent_shuffles_3(self, algorithm, sample_tracks):
        """30% of 10 tracks = 3 shuffled."""
        result = algorithm.shuffle(sample_tracks, shuffle_percentage=30.0, shuffle_location='front')

        # Last 7 should be in original order
        original_last_7 = [t['uri'] for t in sample_tracks[3:]]
        assert result[3:] == original_last_7


class TestPercentageShuffleLocation:
    """Tests for shuffle_location parameter."""

    @pytest.fixture
    def sample_tracks(self):
        """10 sample tracks."""
        return [
            {'uri': f'spotify:track:track{i}', 'name': f'Track {i}'}
            for i in range(10)
        ]

    @pytest.fixture
    def algorithm(self):
        """PercentageShuffle instance."""
        return PercentageShuffle()

    def test_location_front_shuffles_beginning(self, algorithm, sample_tracks):
        """shuffle_location='front' should shuffle front portion."""
        random.seed(42)
        result = algorithm.shuffle(
            sample_tracks,
            shuffle_percentage=50.0,
            shuffle_location='front'
        )

        # Last 5 tracks should be in original order
        original_last_5 = [t['uri'] for t in sample_tracks[5:]]
        assert result[5:] == original_last_5

        # First 5 should be shuffled versions of original first 5
        original_first_5 = set(t['uri'] for t in sample_tracks[:5])
        assert set(result[:5]) == original_first_5

    def test_location_back_shuffles_end(self, algorithm, sample_tracks):
        """shuffle_location='back' should shuffle back portion."""
        random.seed(42)
        result = algorithm.shuffle(
            sample_tracks,
            shuffle_percentage=50.0,
            shuffle_location='back'
        )

        # First 5 tracks should be in original order
        original_first_5 = [t['uri'] for t in sample_tracks[:5]]
        assert result[:5] == original_first_5

        # Last 5 should be shuffled versions of original last 5
        original_last_5 = set(t['uri'] for t in sample_tracks[5:])
        assert set(result[5:]) == original_last_5

    def test_default_location_is_front(self, algorithm, sample_tracks):
        """Default shuffle_location should be 'front'."""
        random.seed(42)
        result = algorithm.shuffle(sample_tracks, shuffle_percentage=50.0)

        # Last 5 should be in original order (front was shuffled)
        original_last_5 = [t['uri'] for t in sample_tracks[5:]]
        assert result[5:] == original_last_5


class TestPercentageShuffleEdgeCases:
    """Edge case tests for PercentageShuffle."""

    @pytest.fixture
    def algorithm(self):
        """PercentageShuffle instance."""
        return PercentageShuffle()

    def test_tracks_without_uri_are_skipped(self, algorithm):
        """Should skip tracks without URI."""
        tracks = [
            {'uri': 'spotify:track:1'},
            {'name': 'No URI'},
            {'uri': 'spotify:track:2'},
        ]

        result = algorithm.shuffle(tracks)

        assert len(result) == 2

    def test_very_small_percentage(self, algorithm):
        """Should handle very small percentages (rounds to 0)."""
        tracks = [{'uri': f'spotify:track:t{i}'} for i in range(10)]
        result = algorithm.shuffle(tracks, shuffle_percentage=5.0)  # 0.5 tracks = 0

        # With 0 tracks to shuffle, original order is returned
        original = [t['uri'] for t in tracks]
        assert result == original

    def test_percentage_rounding(self, algorithm):
        """Should round percentage calculation to integer."""
        # 33% of 10 = 3.3, should round to 3
        tracks = [{'uri': f'spotify:track:t{i}'} for i in range(10)]
        result = algorithm.shuffle(tracks, shuffle_percentage=33.0, shuffle_location='front')

        # Last 7 should be preserved
        original_last_7 = [t['uri'] for t in tracks[3:]]
        assert result[3:] == original_last_7

    def test_back_shuffle_with_small_list(self, algorithm):
        """Should handle back shuffle with small lists."""
        tracks = [{'uri': 'a'}, {'uri': 'b'}]
        result = algorithm.shuffle(tracks, shuffle_percentage=50.0, shuffle_location='back')

        assert len(result) == 2
        assert set(result) == {'a', 'b'}
        # First track preserved
        assert result[0] == 'a'
