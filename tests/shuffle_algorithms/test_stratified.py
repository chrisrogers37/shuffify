"""
Tests for StratifiedShuffle algorithm.

StratifiedShuffle divides the playlist into sections, shuffles each section
independently, and reassembles them in the original section order.
"""

import pytest
from shuffify.shuffle_algorithms.stratified import StratifiedShuffle


class TestStratifiedShuffleProperties:
    """Test StratifiedShuffle metadata properties."""

    def test_name(self):
        """Algorithm name should be 'Stratified'."""
        algorithm = StratifiedShuffle()
        assert algorithm.name == "Stratified"

    def test_description(self):
        """Description should explain section-based shuffling."""
        algorithm = StratifiedShuffle()
        assert "sections" in algorithm.description.lower()
        assert "independently" in algorithm.description.lower()

    def test_requires_features(self):
        """StratifiedShuffle should not require audio features."""
        algorithm = StratifiedShuffle()
        assert algorithm.requires_features is False

    def test_parameters(self):
        """Parameters should include keep_first and section_count."""
        algorithm = StratifiedShuffle()
        params = algorithm.parameters

        assert 'keep_first' in params
        assert params['keep_first']['type'] == 'integer'
        assert params['keep_first']['default'] == 0

        assert 'section_count' in params
        assert params['section_count']['type'] == 'integer'
        assert params['section_count']['default'] == 5
        assert params['section_count']['min'] == 1
        assert params['section_count']['max'] == 20


class TestStratifiedShuffleBasicFunctionality:
    """Test basic shuffling functionality."""

    def test_shuffle_returns_all_tracks(self, sample_tracks):
        """Shuffle should return all track URIs."""
        algorithm = StratifiedShuffle()
        result = algorithm.shuffle(sample_tracks)

        original_uris = {t['uri'] for t in sample_tracks}
        result_uris = set(result)

        assert len(result) == len(sample_tracks)
        assert result_uris == original_uris

    def test_shuffle_returns_list_of_uris(self, sample_tracks):
        """Shuffle should return a list of URI strings."""
        algorithm = StratifiedShuffle()
        result = algorithm.shuffle(sample_tracks)

        assert isinstance(result, list)
        assert all(isinstance(uri, str) for uri in result)


class TestStratifiedShuffleSectionBehavior:
    """Test section-based shuffling behavior."""

    def test_sections_stay_in_order(self):
        """Tracks should stay within their original sections."""
        algorithm = StratifiedShuffle()

        # 12 tracks, 3 sections = 4 tracks per section
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(12)]

        import random
        random.seed(42)
        result = algorithm.shuffle(tracks, section_count=3)

        # Section 0 tracks (0-3) should be in positions 0-3
        section_0_tracks = {f'spotify:track:{i}' for i in range(4)}
        assert set(result[0:4]) == section_0_tracks

        # Section 1 tracks (4-7) should be in positions 4-7
        section_1_tracks = {f'spotify:track:{i}' for i in range(4, 8)}
        assert set(result[4:8]) == section_1_tracks

        # Section 2 tracks (8-11) should be in positions 8-11
        section_2_tracks = {f'spotify:track:{i}' for i in range(8, 12)}
        assert set(result[8:12]) == section_2_tracks

    def test_tracks_shuffled_within_sections(self):
        """Tracks should be shuffled within their sections."""
        algorithm = StratifiedShuffle()

        # 20 tracks, 4 sections = 5 tracks per section
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(20)]
        original_uris = [t['uri'] for t in tracks]

        # Run multiple times to verify shuffling happens
        different_orders = 0
        for seed in range(10):
            import random
            random.seed(seed)
            result = algorithm.shuffle(tracks, section_count=4)

            # Check if any section has different order
            if result != original_uris:
                different_orders += 1

        # Should have different orders in most runs
        assert different_orders > 0

    def test_uneven_section_distribution(self):
        """Uneven track counts should distribute extra tracks to first sections."""
        algorithm = StratifiedShuffle()

        # 10 tracks, 3 sections = 4, 3, 3 distribution
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(10)]

        import random
        random.seed(42)
        result = algorithm.shuffle(tracks, section_count=3)

        # Section 0 should have tracks 0-3 (4 tracks)
        section_0_tracks = {f'spotify:track:{i}' for i in range(4)}
        assert set(result[0:4]) == section_0_tracks

        # Section 1 should have tracks 4-6 (3 tracks)
        section_1_tracks = {f'spotify:track:{i}' for i in range(4, 7)}
        assert set(result[4:7]) == section_1_tracks

        # Section 2 should have tracks 7-9 (3 tracks)
        section_2_tracks = {f'spotify:track:{i}' for i in range(7, 10)}
        assert set(result[7:10]) == section_2_tracks


class TestStratifiedShuffleKeepFirst:
    """Test keep_first parameter functionality."""

    def test_keep_first_preserves_tracks(self, sample_tracks):
        """keep_first=N should preserve first N tracks in order."""
        algorithm = StratifiedShuffle()
        keep_count = 5
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, keep_first=keep_count)

        # First N tracks should be unchanged
        assert result[:keep_count] == original_uris[:keep_count]

    def test_keep_first_with_sections(self):
        """Sections should only apply to non-kept tracks."""
        algorithm = StratifiedShuffle()

        tracks = [{'uri': f'spotify:track:{i}'} for i in range(15)]
        original_uris = [t['uri'] for t in tracks]

        result = algorithm.shuffle(tracks, keep_first=3, section_count=3)

        # First 3 should be preserved exactly
        assert result[:3] == original_uris[:3]

        # Remaining 12 tracks should be stratified shuffled
        remaining_original = set(original_uris[3:])
        remaining_result = set(result[3:])
        assert remaining_result == remaining_original

    def test_keep_first_all(self, sample_tracks):
        """keep_first >= total tracks should return original order."""
        algorithm = StratifiedShuffle()
        original_uris = [t['uri'] for t in sample_tracks]

        result = algorithm.shuffle(sample_tracks, keep_first=len(sample_tracks))

        assert result == original_uris

    def test_keep_first_leaves_one(self):
        """If keep_first leaves only 1 track, return it at end."""
        algorithm = StratifiedShuffle()
        tracks = [
            {'uri': 'spotify:track:a'},
            {'uri': 'spotify:track:b'},
        ]

        result = algorithm.shuffle(tracks, keep_first=1)

        assert result[0] == 'spotify:track:a'
        assert result[1] == 'spotify:track:b'


class TestStratifiedShuffleSectionCount:
    """Test section_count parameter variations."""

    def test_one_section(self):
        """One section should shuffle all tracks like basic shuffle."""
        algorithm = StratifiedShuffle()

        tracks = [{'uri': f'spotify:track:{i}'} for i in range(10)]

        result = algorithm.shuffle(tracks, section_count=1)

        assert len(result) == 10
        assert set(result) == {f'spotify:track:{i}' for i in range(10)}

    def test_many_sections(self, sample_tracks):
        """Many sections (up to max) should work."""
        algorithm = StratifiedShuffle()
        result = algorithm.shuffle(sample_tracks, section_count=20)

        assert len(result) == len(sample_tracks)

    def test_sections_more_than_tracks(self):
        """More sections than tracks should handle gracefully."""
        algorithm = StratifiedShuffle()

        tracks = [{'uri': f'spotify:track:{i}'} for i in range(5)]
        result = algorithm.shuffle(tracks, section_count=20)

        assert len(result) == 5
        assert set(result) == {f'spotify:track:{i}' for i in range(5)}


class TestStratifiedShuffleEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_tracks(self, empty_tracks):
        """Empty track list should return empty list."""
        algorithm = StratifiedShuffle()
        result = algorithm.shuffle(empty_tracks)

        assert result == []

    def test_single_track(self, single_track):
        """Single track should return unchanged."""
        algorithm = StratifiedShuffle()
        result = algorithm.shuffle(single_track)

        assert result == [single_track[0]['uri']]

    def test_two_tracks_multiple_sections(self):
        """Two tracks with many sections should still work."""
        algorithm = StratifiedShuffle()
        tracks = [
            {'uri': 'spotify:track:a'},
            {'uri': 'spotify:track:b'}
        ]

        result = algorithm.shuffle(tracks, section_count=5)

        assert len(result) == 2
        assert set(result) == {'spotify:track:a', 'spotify:track:b'}

    def test_tracks_with_missing_uri(self, tracks_with_missing_uri):
        """Tracks without valid URIs should be filtered out."""
        algorithm = StratifiedShuffle()
        result = algorithm.shuffle(tracks_with_missing_uri)

        assert len(result) == 3

    def test_features_parameter_ignored(self, sample_tracks, sample_audio_features):
        """Features parameter should be accepted but ignored."""
        algorithm = StratifiedShuffle()
        result = algorithm.shuffle(sample_tracks, features=sample_audio_features)

        assert len(result) == len(sample_tracks)


class TestStratifiedShuffleDefaultParameters:
    """Test default parameter behavior."""

    def test_default_section_count_is_five(self):
        """Default section_count should be 5."""
        algorithm = StratifiedShuffle()
        assert algorithm.parameters['section_count']['default'] == 5

    def test_default_keep_first_is_zero(self):
        """Default keep_first should be 0."""
        algorithm = StratifiedShuffle()
        assert algorithm.parameters['keep_first']['default'] == 0


class TestStratifiedVsBalanced:
    """Test differences between Stratified and Balanced shuffle."""

    def test_stratified_maintains_section_boundaries(self):
        """Stratified keeps section boundaries intact (unlike Balanced)."""
        algorithm = StratifiedShuffle()

        # 12 tracks, 3 sections
        tracks = [{'uri': f'spotify:track:{i}'} for i in range(12)]

        import random
        random.seed(42)
        result = algorithm.shuffle(tracks, section_count=3)

        # Tracks from section 0 (0-3) must all be in first 4 positions
        first_section = set(result[0:4])
        expected_first = {f'spotify:track:{i}' for i in range(4)}
        assert first_section == expected_first

        # This is different from BalancedShuffle which interleaves
