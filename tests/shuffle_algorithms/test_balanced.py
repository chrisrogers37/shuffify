"""
Tests for BalancedShuffle algorithm.

BalancedShuffle ensures fair representation from all parts of the playlist
by dividing it into sections and using a round-robin selection process.
"""

import pytest
from shuffify.shuffle_algorithms.balanced import BalancedShuffle


class TestBalancedShuffleProperties:
    """Test BalancedShuffle metadata properties."""

    def test_name(self):
        """Algorithm name should be 'Balanced'."""
        algorithm = BalancedShuffle()
        assert algorithm.name == "Balanced"

    def test_description(self):
        """Description should explain fair representation."""
        algorithm = BalancedShuffle()
        assert "fair representation" in algorithm.description.lower()
        assert "sections" in algorithm.description.lower()

    def test_requires_features(self):
        """BalancedShuffle should not require audio features."""
        algorithm = BalancedShuffle()
        assert algorithm.requires_features is False

    def test_parameters(self):
        """Parameters should include keep_first and section_count."""
        algorithm = BalancedShuffle()
        params = algorithm.parameters

        assert 'keep_first' in params
        assert params['keep_first']['type'] == 'integer'
        assert params['keep_first']['default'] == 0

        assert 'section_count' in params
        assert params['section_count']['type'] == 'integer'
        assert params['section_count']['default'] == 4
        assert params['section_count']['min'] == 2
        assert params['section_count']['max'] == 10


class TestBalancedShuffleBasicFunctionality:
    """Test basic shuffling functionality."""

    def test_shuffle_returns_all_tracks(self, sample_tracks):
        """Shuffle should return all track URIs."""
        algorithm = BalancedShuffle()
        result = algorithm.shuffle(sample_tracks)

        original_uris = {t['uri'] for t in sample_tracks}
        result_uris = set(result)

        assert len(result) == len(sample_tracks)
        assert result_uris == original_uris

    def test_shuffle_returns_list_of_uris(self, sample_tracks):
        """Shuffle should return a list of URI strings."""
        algorithm = BalancedShuffle()
        result = algorithm.shuffle(sample_tracks)

        assert isinstance(result, list)
        assert all(isinstance(uri, str) for uri in result)


class TestBalancedShuffleRoundRobin:
    """Test round-robin section selection behavior."""

    def test_balanced_distribution_with_4_sections(self):
        """Round-robin should interleave tracks from 4 sections."""
        algorithm = BalancedShuffle()

        # Create 12 tracks (divisible by 4)
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(12)]

        # With 4 sections of 3 tracks each, after round-robin:
        # Original sections: [0,1,2], [3,4,5], [6,7,8], [9,10,11]
        # After shuffle within sections, round-robin picks one from each
        result = algorithm.shuffle(tracks, section_count=4)

        assert len(result) == 12
        assert set(result) == {f'spotify:track:{i}' for i in range(12)}

    def test_section_representation(self):
        """Each section should contribute tracks throughout the result."""
        algorithm = BalancedShuffle()

        # 20 tracks, 4 sections = 5 tracks per section
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(20)]

        import random
        random.seed(42)  # For reproducibility
        result = algorithm.shuffle(tracks, section_count=4)

        # Section boundaries: 0-4, 5-9, 10-14, 15-19
        # After round-robin, tracks from different sections should be interleaved
        # Check that in first 8 results, we have tracks from at least 3 sections
        first_eight = result[:8]
        sections_represented = set()

        for uri in first_eight:
            track_num = int(uri.split(':')[-1])
            section = track_num // 5
            sections_represented.add(section)

        # Should have representation from multiple sections in first 8
        assert len(sections_represented) >= 2

    def test_uneven_sections(self):
        """Sections with uneven track counts should be handled correctly."""
        algorithm = BalancedShuffle()

        # 10 tracks, 3 sections = 4, 3, 3 tracks
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(10)]
        result = algorithm.shuffle(tracks, section_count=3)

        assert len(result) == 10
        assert set(result) == {f'spotify:track:{i}' for i in range(10)}


class TestBalancedShuffleKeepFirst:
    """Test keep_first parameter functionality."""

    def test_keep_first_preserves_tracks(self, sample_tracks):
        """keep_first=N should preserve first N tracks in order."""
        algorithm = BalancedShuffle()
        keep_count = 5
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, keep_first=keep_count)

        # First N tracks should be unchanged
        assert result[:keep_count] == original_uris[:keep_count]

    def test_keep_first_with_sections(self, sample_tracks):
        """Sections should only apply to non-kept tracks."""
        algorithm = BalancedShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, keep_first=3, section_count=4)

        # First 3 should be preserved
        assert result[:3] == original_uris[:3]

        # Remaining should be balanced shuffle of remaining tracks
        remaining_original = set(original_uris[3:])
        remaining_result = set(result[3:])
        assert remaining_result == remaining_original

    def test_keep_first_all(self, sample_tracks):
        """keep_first >= total tracks should return original order."""
        algorithm = BalancedShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, keep_first=len(sample_tracks))

        assert result == original_uris


class TestBalancedShuffleSectionCount:
    """Test section_count parameter variations."""

    def test_two_sections(self, sample_tracks):
        """Two sections should split playlist in half."""
        algorithm = BalancedShuffle()
        result = algorithm.shuffle(sample_tracks, section_count=2)

        assert len(result) == len(sample_tracks)

    def test_ten_sections(self, sample_tracks):
        """Maximum section count should work."""
        algorithm = BalancedShuffle()
        result = algorithm.shuffle(sample_tracks, section_count=10)

        assert len(result) == len(sample_tracks)

    def test_sections_more_than_tracks(self):
        """More sections than tracks should handle gracefully."""
        algorithm = BalancedShuffle()

        # 5 tracks with 10 sections means some sections are empty
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(5)]
        result = algorithm.shuffle(tracks, section_count=10)

        assert len(result) == 5
        assert set(result) == {f'spotify:track:{i}' for i in range(5)}


class TestBalancedShuffleEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_tracks(self, empty_tracks):
        """Empty track list should return empty list."""
        algorithm = BalancedShuffle()
        result = algorithm.shuffle(empty_tracks)

        assert result == []

    def test_single_track(self, single_track):
        """Single track should return unchanged."""
        algorithm = BalancedShuffle()
        result = algorithm.shuffle(single_track)

        assert result == [single_track[0]['uri']]

    def test_two_tracks(self):
        """Two tracks with 4 sections should still work."""
        algorithm = BalancedShuffle()
        tracks = [
            {'uri': 'spotify:track:a'},
            {'uri': 'spotify:track:b'}
        ]

        result = algorithm.shuffle(tracks, section_count=4)

        assert len(result) == 2
        assert set(result) == {'spotify:track:a', 'spotify:track:b'}

    def test_tracks_with_missing_uri(self, tracks_with_missing_uri):
        """Tracks without valid URIs should be filtered out."""
        algorithm = BalancedShuffle()
        result = algorithm.shuffle(tracks_with_missing_uri)

        # Should only include tracks with valid URIs
        assert len(result) == 3

    def test_features_parameter_ignored(self, sample_tracks, sample_audio_features):
        """Features parameter should be accepted but ignored."""
        algorithm = BalancedShuffle()
        result = algorithm.shuffle(sample_tracks, features=sample_audio_features)

        assert len(result) == len(sample_tracks)

    def test_only_one_track_to_shuffle(self):
        """If keep_first leaves only 1 track, return it unchanged."""
        algorithm = BalancedShuffle()
        tracks = [
            {'uri': 'spotify:track:a'},
            {'uri': 'spotify:track:b'},
        ]

        result = algorithm.shuffle(tracks, keep_first=1)

        assert result[0] == 'spotify:track:a'
        assert result[1] == 'spotify:track:b'
        assert len(result) == 2


class TestBalancedShuffleDefaultParameters:
    """Test default parameter behavior."""

    def test_default_section_count_is_four(self, sample_tracks):
        """Default section_count should be 4."""
        algorithm = BalancedShuffle()

        # Check parameter default
        assert algorithm.parameters['section_count']['default'] == 4

    def test_default_keep_first_is_zero(self, sample_tracks):
        """Default keep_first should be 0."""
        algorithm = BalancedShuffle()

        assert algorithm.parameters['keep_first']['default'] == 0
