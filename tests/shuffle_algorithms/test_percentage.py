"""
Tests for PercentageShuffle algorithm.

PercentageShuffle shuffles a specified percentage of the playlist
while keeping the rest in their original order.
"""

import pytest
from shuffify.shuffle_algorithms.percentage import PercentageShuffle


class TestPercentageShuffleProperties:
    """Test PercentageShuffle metadata properties."""

    def test_name(self):
        """Algorithm name should be 'Percentage'."""
        algorithm = PercentageShuffle()
        assert algorithm.name == "Percentage"

    def test_description(self):
        """Description should explain percentage shuffling."""
        algorithm = PercentageShuffle()
        assert "portion" in algorithm.description.lower()
        assert "keeping the rest" in algorithm.description.lower()

    def test_requires_features(self):
        """PercentageShuffle should not require audio features."""
        algorithm = PercentageShuffle()
        assert algorithm.requires_features is False

    def test_parameters(self):
        """Parameters should include shuffle_percentage and shuffle_location."""
        algorithm = PercentageShuffle()
        params = algorithm.parameters

        assert 'shuffle_percentage' in params
        assert params['shuffle_percentage']['type'] == 'float'
        assert params['shuffle_percentage']['default'] == 50.0
        assert params['shuffle_percentage']['min'] == 0.0
        assert params['shuffle_percentage']['max'] == 100.0

        assert 'shuffle_location' in params
        assert params['shuffle_location']['type'] == 'string'
        assert params['shuffle_location']['default'] == 'front'
        assert 'front' in params['shuffle_location']['options']
        assert 'back' in params['shuffle_location']['options']


class TestPercentageShuffleBasicFunctionality:
    """Test basic shuffling functionality."""

    def test_shuffle_returns_all_tracks(self, sample_tracks):
        """Shuffle should return all track URIs."""
        algorithm = PercentageShuffle()
        result = algorithm.shuffle(sample_tracks)

        original_uris = {t['uri'] for t in sample_tracks}
        result_uris = set(result)

        assert len(result) == len(sample_tracks)
        assert result_uris == original_uris

    def test_shuffle_returns_list_of_uris(self, sample_tracks):
        """Shuffle should return a list of URI strings."""
        algorithm = PercentageShuffle()
        result = algorithm.shuffle(sample_tracks)

        assert isinstance(result, list)
        assert all(isinstance(uri, str) for uri in result)


class TestPercentageShufflePercentages:
    """Test shuffle_percentage parameter variations."""

    def test_shuffle_50_percent_front(self, sample_tracks):
        """50% shuffle from front should shuffle half the tracks."""
        algorithm = PercentageShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, shuffle_percentage=50.0, shuffle_location='front')

        # Second half should be unchanged
        shuffle_count = int(len(original_uris) * 0.5)
        assert result[shuffle_count:] == original_uris[shuffle_count:]

        # First half should contain same tracks
        assert set(result[:shuffle_count]) == set(original_uris[:shuffle_count])

    def test_shuffle_50_percent_back(self, sample_tracks):
        """50% shuffle from back should shuffle the last half."""
        algorithm = PercentageShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, shuffle_percentage=50.0, shuffle_location='back')

        shuffle_count = int(len(original_uris) * 0.5)
        kept_count = len(original_uris) - shuffle_count

        # First half should be unchanged
        assert result[:kept_count] == original_uris[:kept_count]

        # Second half should contain same tracks
        assert set(result[kept_count:]) == set(original_uris[kept_count:])

    def test_shuffle_100_percent(self, sample_tracks):
        """100% should shuffle all tracks."""
        algorithm = PercentageShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, shuffle_percentage=100.0)

        assert len(result) == len(original_uris)
        assert set(result) == set(original_uris)

    def test_shuffle_0_percent(self, sample_tracks):
        """0% should not shuffle any tracks."""
        algorithm = PercentageShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, shuffle_percentage=0.0)

        # Should return original order
        assert result == original_uris

    def test_shuffle_25_percent(self, sample_tracks):
        """25% should shuffle only a quarter of tracks."""
        algorithm = PercentageShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, shuffle_percentage=25.0, shuffle_location='front')

        shuffle_count = int(len(original_uris) * 0.25)

        # After the shuffled portion, tracks should be unchanged
        assert result[shuffle_count:] == original_uris[shuffle_count:]

    def test_shuffle_75_percent(self, sample_tracks):
        """75% should shuffle three quarters of tracks."""
        algorithm = PercentageShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, shuffle_percentage=75.0, shuffle_location='front')

        shuffle_count = int(len(original_uris) * 0.75)

        # After the shuffled portion, tracks should be unchanged
        assert result[shuffle_count:] == original_uris[shuffle_count:]


class TestPercentageShuffleLocation:
    """Test shuffle_location parameter behavior."""

    def test_front_location_default(self, sample_tracks):
        """Default location should be 'front'."""
        algorithm = PercentageShuffle()
        assert algorithm.parameters['shuffle_location']['default'] == 'front'

    def test_front_preserves_back(self):
        """Front shuffle should preserve back tracks in order."""
        algorithm = PercentageShuffle()
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(10)]
        original_uris = [t['uri'] for t in tracks]

        result = algorithm.shuffle(tracks, shuffle_percentage=50.0, shuffle_location='front')

        # Last 5 should be unchanged
        assert result[5:] == original_uris[5:]

    def test_back_preserves_front(self):
        """Back shuffle should preserve front tracks in order."""
        algorithm = PercentageShuffle()
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(10)]
        original_uris = [t['uri'] for t in tracks]

        result = algorithm.shuffle(tracks, shuffle_percentage=50.0, shuffle_location='back')

        # First 5 should be unchanged
        assert result[:5] == original_uris[:5]


class TestPercentageShuffleEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_tracks(self, empty_tracks):
        """Empty track list should return empty list."""
        algorithm = PercentageShuffle()
        result = algorithm.shuffle(empty_tracks)

        assert result == []

    def test_single_track(self, single_track):
        """Single track should return unchanged."""
        algorithm = PercentageShuffle()
        result = algorithm.shuffle(single_track)

        assert result == [single_track[0]['uri']]

    def test_two_tracks_50_percent(self):
        """Two tracks at 50% should shuffle 1 track."""
        algorithm = PercentageShuffle()
        tracks = [
            {'uri': 'spotify:track:a'},
            {'uri': 'spotify:track:b'}
        ]

        result = algorithm.shuffle(tracks, shuffle_percentage=50.0, shuffle_location='front')

        assert len(result) == 2
        # First track shuffled, second unchanged
        assert result[1] == 'spotify:track:b'

    def test_tracks_with_missing_uri(self, tracks_with_missing_uri):
        """Tracks without valid URIs should be filtered out."""
        algorithm = PercentageShuffle()
        result = algorithm.shuffle(tracks_with_missing_uri)

        # Should only include tracks with valid URIs
        assert len(result) == 3

    def test_features_parameter_ignored(self, sample_tracks, sample_audio_features):
        """Features parameter should be accepted but ignored."""
        algorithm = PercentageShuffle()
        result = algorithm.shuffle(sample_tracks, features=sample_audio_features)

        assert len(result) == len(sample_tracks)

    def test_small_percentage_rounds_to_zero(self):
        """Very small percentage on small list might round to 0."""
        algorithm = PercentageShuffle()
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(5)]
        original_uris = [t['uri'] for t in tracks]

        # 1% of 5 = 0.05, rounds to 0
        result = algorithm.shuffle(tracks, shuffle_percentage=1.0)

        # With 0 tracks to shuffle, should return original
        assert result == original_uris

    def test_percentage_boundary_calculation(self):
        """Verify percentage calculation is correct."""
        algorithm = PercentageShuffle()

        # 10 tracks, 30% = 3 tracks to shuffle
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(10)]
        original_uris = [t['uri'] for t in tracks]

        result = algorithm.shuffle(tracks, shuffle_percentage=30.0, shuffle_location='front')

        # Last 7 should be preserved
        assert result[3:] == original_uris[3:]


class TestPercentageShuffleDefaultParameters:
    """Test default parameter behavior."""

    def test_default_percentage_is_50(self):
        """Default shuffle_percentage should be 50.0."""
        algorithm = PercentageShuffle()
        assert algorithm.parameters['shuffle_percentage']['default'] == 50.0

    def test_default_location_is_front(self):
        """Default shuffle_location should be 'front'."""
        algorithm = PercentageShuffle()
        assert algorithm.parameters['shuffle_location']['default'] == 'front'

    def test_shuffle_with_defaults(self, sample_tracks):
        """Shuffle with no parameters should use defaults."""
        algorithm = PercentageShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks)

        # Default is 50% from front
        shuffle_count = int(len(original_uris) * 0.5)

        # Back portion should be unchanged
        assert result[shuffle_count:] == original_uris[shuffle_count:]
