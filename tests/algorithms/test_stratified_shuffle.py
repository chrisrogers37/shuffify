"""
Tests for StratifiedShuffle algorithm.

Tests cover section-based shuffling while maintaining section order.
"""

import pytest
import random

from shuffify.shuffle_algorithms.stratified import StratifiedShuffle


class TestStratifiedShuffleProperties:
    """Tests for StratifiedShuffle metadata properties."""

    def test_name_is_stratified(self):
        """Should return 'Stratified' as name."""
        algo = StratifiedShuffle()
        assert algo.name == 'Stratified'

    def test_description_mentions_sections(self):
        """Should describe section-based shuffling."""
        algo = StratifiedShuffle()
        assert 'section' in algo.description.lower()

    def test_parameters_includes_keep_first(self):
        """Should include keep_first parameter."""
        algo = StratifiedShuffle()
        params = algo.parameters

        assert 'keep_first' in params
        assert params['keep_first']['type'] == 'integer'
        assert params['keep_first']['default'] == 0

    def test_parameters_includes_section_count(self):
        """Should include section_count parameter."""
        algo = StratifiedShuffle()
        params = algo.parameters

        assert 'section_count' in params
        assert params['section_count']['type'] == 'integer'
        assert params['section_count']['default'] == 5
        assert params['section_count']['min'] == 1
        assert params['section_count']['max'] == 20

    def test_requires_features_is_false(self):
        """Should not require audio features."""
        algo = StratifiedShuffle()
        assert algo.requires_features is False


class TestStratifiedShuffleShuffle:
    """Tests for StratifiedShuffle.shuffle method."""

    @pytest.fixture
    def sample_tracks(self):
        """15 sample tracks for section division testing."""
        return [
            {'uri': f'spotify:track:track{i}', 'name': f'Track {i}'}
            for i in range(15)
        ]

    @pytest.fixture
    def algorithm(self):
        """StratifiedShuffle instance."""
        return StratifiedShuffle()

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


class TestStratifiedShuffleSections:
    """Tests for section-based shuffling behavior."""

    @pytest.fixture
    def algorithm(self):
        """StratifiedShuffle instance."""
        return StratifiedShuffle()

    def test_sections_are_shuffled_independently(self, algorithm):
        """Each section should be shuffled independently."""
        # 12 tracks, 3 sections of 4 each
        tracks = [{'uri': f'spotify:track:t{i}'} for i in range(12)]

        random.seed(42)
        result = algorithm.shuffle(tracks, section_count=3)

        # Each section should contain the same tracks, possibly reordered
        section1_original = [f'spotify:track:t{i}' for i in range(4)]
        section2_original = [f'spotify:track:t{i}' for i in range(4, 8)]
        section3_original = [f'spotify:track:t{i}' for i in range(8, 12)]

        section1_result = result[:4]
        section2_result = result[4:8]
        section3_result = result[8:12]

        # Each result section contains same tracks as original section
        assert set(section1_result) == set(section1_original)
        assert set(section2_result) == set(section2_original)
        assert set(section3_result) == set(section3_original)

    def test_sections_maintain_order(self, algorithm):
        """Section order should be maintained (section 1 before section 2)."""
        tracks = [{'uri': f'spotify:track:t{i}'} for i in range(12)]

        result = algorithm.shuffle(tracks, section_count=3)

        # Tracks from section 1 should all appear before tracks from section 3
        section1_tracks = set(f'spotify:track:t{i}' for i in range(4))
        section3_tracks = set(f'spotify:track:t{i}' for i in range(8, 12))

        last_section1_index = max(i for i, uri in enumerate(result) if uri in section1_tracks)
        first_section3_index = min(i for i, uri in enumerate(result) if uri in section3_tracks)

        assert last_section1_index < first_section3_index

    def test_different_from_balanced_shuffle(self, algorithm):
        """Should differ from balanced shuffle (no round-robin)."""
        # Stratified keeps sections intact; balanced interleaves
        tracks = [{'uri': f'spotify:track:t{i}'} for i in range(8)]

        random.seed(42)
        result = algorithm.shuffle(tracks, section_count=2)

        # First half of result should only contain first-half tracks
        section1_original = set(f'spotify:track:t{i}' for i in range(4))
        section1_result = set(result[:4])

        assert section1_result == section1_original


class TestStratifiedShuffleKeepFirst:
    """Tests for keep_first parameter."""

    @pytest.fixture
    def sample_tracks(self):
        """15 sample tracks."""
        return [
            {'uri': f'spotify:track:track{i}', 'name': f'Track {i}'}
            for i in range(15)
        ]

    @pytest.fixture
    def algorithm(self):
        """StratifiedShuffle instance."""
        return StratifiedShuffle()

    def test_keep_first_preserves_beginning(self, algorithm, sample_tracks):
        """keep_first should preserve first N tracks in order."""
        result = algorithm.shuffle(sample_tracks, keep_first=5)

        original_first_5 = [t['uri'] for t in sample_tracks[:5]]
        assert result[:5] == original_first_5

    def test_keep_first_applies_stratified_to_rest(self, algorithm, sample_tracks):
        """keep_first should apply stratified shuffle to remaining tracks."""
        random.seed(42)
        result = algorithm.shuffle(sample_tracks, keep_first=5, section_count=5)

        # First 5 preserved
        original_first_5 = [t['uri'] for t in sample_tracks[:5]]
        assert result[:5] == original_first_5

        # Remaining 10 are stratified shuffled (each section of 2 stays together)
        remaining = result[5:]
        original_remaining = [t['uri'] for t in sample_tracks[5:]]
        assert set(remaining) == set(original_remaining)

    def test_keep_first_equals_length(self, algorithm, sample_tracks):
        """keep_first >= length should return original order."""
        result = algorithm.shuffle(sample_tracks, keep_first=15)
        original = [t['uri'] for t in sample_tracks]
        assert result == original


class TestStratifiedShuffleSectionCount:
    """Tests for section_count parameter."""

    @pytest.fixture
    def sample_tracks(self):
        """20 sample tracks for flexible section testing."""
        return [
            {'uri': f'spotify:track:track{i}', 'name': f'Track {i}'}
            for i in range(20)
        ]

    @pytest.fixture
    def algorithm(self):
        """StratifiedShuffle instance."""
        return StratifiedShuffle()

    def test_section_count_1(self, algorithm, sample_tracks):
        """Should work with 1 section (shuffles entire list)."""
        random.seed(42)
        result = algorithm.shuffle(sample_tracks, section_count=1)

        assert len(result) == 20
        assert set(result) == {t['uri'] for t in sample_tracks}
        # With 1 section, it's just a basic shuffle
        original = [t['uri'] for t in sample_tracks]
        assert result != original  # Should be shuffled

    def test_section_count_20(self, algorithm, sample_tracks):
        """Should work with section_count = track count (each track is a section)."""
        result = algorithm.shuffle(sample_tracks, section_count=20)

        # With 1 track per section, and only internal shuffling, order stays same
        original = [t['uri'] for t in sample_tracks]
        assert result == original

    def test_section_count_larger_than_tracks(self, algorithm):
        """Should handle section_count > number of tracks."""
        tracks = [{'uri': f'spotify:track:t{i}'} for i in range(5)]
        result = algorithm.shuffle(tracks, section_count=10)

        # Each track becomes its own section, order preserved
        original = [t['uri'] for t in tracks]
        assert result == original

    def test_uneven_section_distribution(self, algorithm):
        """Should handle uneven section sizes."""
        # 11 tracks with 5 sections = sections of varying sizes
        tracks = [{'uri': f'spotify:track:t{i}'} for i in range(11)]

        result = algorithm.shuffle(tracks, section_count=5)

        assert len(result) == 11
        assert set(result) == {t['uri'] for t in tracks}


class TestStratifiedShuffleEdgeCases:
    """Edge case tests for StratifiedShuffle."""

    @pytest.fixture
    def algorithm(self):
        """StratifiedShuffle instance."""
        return StratifiedShuffle()

    def test_tracks_without_uri_are_skipped(self, algorithm):
        """Should skip tracks without URI."""
        tracks = [
            {'uri': 'spotify:track:1'},
            {'name': 'No URI'},
            {'uri': 'spotify:track:2'},
        ]

        result = algorithm.shuffle(tracks)

        assert len(result) == 2

    def test_two_tracks_two_sections(self, algorithm):
        """Should handle minimal case: 2 tracks, 2 sections."""
        tracks = [{'uri': 'a'}, {'uri': 'b'}]
        result = algorithm.shuffle(tracks, section_count=2)

        # Each track in its own section, order preserved
        assert result == ['a', 'b']

    def test_randomness_within_sections(self, algorithm):
        """Should produce different results within sections."""
        tracks = [{'uri': f'spotify:track:t{i}'} for i in range(20)]

        results = []
        for seed in range(5):
            random.seed(seed)
            results.append(tuple(algorithm.shuffle(tracks, section_count=4)))

        # Should have some variation
        unique_results = set(results)
        assert len(unique_results) > 1


class TestStratifiedVsBalancedComparison:
    """Tests comparing Stratified and Balanced shuffle behaviors."""

    def test_stratified_keeps_sections_intact(self):
        """Stratified should keep section tracks together, unlike Balanced."""
        from shuffify.shuffle_algorithms.balanced import BalancedShuffle

        tracks = [{'uri': f't{i}'} for i in range(8)]

        stratified = StratifiedShuffle()
        balanced = BalancedShuffle()

        random.seed(42)
        strat_result = stratified.shuffle(tracks, section_count=2)

        random.seed(42)
        bal_result = balanced.shuffle(tracks, section_count=2)

        # Stratified: first 4 are from first section
        strat_first_4 = set(strat_result[:4])
        original_first_4 = set(f't{i}' for i in range(4))
        assert strat_first_4 == original_first_4

        # Balanced: first 4 are interleaved (may have tracks from both sections)
        bal_first_4 = set(bal_result[:4])
        # Balanced interleaves, so first 4 should have mix
        # (can't assert exact behavior, but it's different from stratified)
