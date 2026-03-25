"""
Tests for NewestFirstShuffle algorithm.

Tests cover date-based sorting, jitter behavior, and edge cases.
"""

import pytest
import random

from shuffify.shuffle_algorithms.newest_first import NewestFirstShuffle


class TestNewestFirstShuffleProperties:
    """Tests for NewestFirstShuffle metadata properties."""

    def test_name_is_newest_first(self):
        """Should return 'Newest First' as name."""
        algo = NewestFirstShuffle()
        assert algo.name == "Newest First"

    def test_description_is_present(self):
        """Should have a meaningful description."""
        algo = NewestFirstShuffle()
        assert algo.description
        assert len(algo.description) > 10

    def test_parameters_includes_jitter(self):
        """Should include jitter parameter."""
        algo = NewestFirstShuffle()
        params = algo.parameters

        assert "jitter" in params
        assert params["jitter"]["type"] == "integer"
        assert params["jitter"]["default"] == 5
        assert params["jitter"]["min"] == 1
        assert params["jitter"]["max"] == 50

    def test_requires_features_is_false(self):
        """Should not require audio features."""
        algo = NewestFirstShuffle()
        assert algo.requires_features is False


class TestNewestFirstShuffleSorting:
    """Tests for date-based sorting behavior."""

    @pytest.fixture
    def algorithm(self):
        """NewestFirstShuffle instance."""
        return NewestFirstShuffle()

    @pytest.fixture
    def dated_tracks(self):
        """Sample tracks with added_at timestamps in known order."""
        return [
            {
                "uri": "spotify:track:oldest",
                "name": "Oldest Song",
                "added_at": "2025-01-01T00:00:00Z",
            },
            {
                "uri": "spotify:track:middle",
                "name": "Middle Song",
                "added_at": "2025-06-15T12:00:00Z",
            },
            {
                "uri": "spotify:track:newest",
                "name": "Newest Song",
                "added_at": "2026-03-20T18:30:00Z",
            },
        ]

    def test_exact_sort_with_jitter_one(self, algorithm, dated_tracks):
        """jitter=1 should produce exact descending date sort."""
        result = algorithm.shuffle(dated_tracks, jitter=1)

        assert result == [
            "spotify:track:newest",
            "spotify:track:middle",
            "spotify:track:oldest",
        ]

    def test_exact_sort_is_deterministic(self, algorithm, dated_tracks):
        """jitter=1 should always produce the same order."""
        results = [
            algorithm.shuffle(dated_tracks, jitter=1) for _ in range(10)
        ]
        assert all(r == results[0] for r in results)

    def test_all_uris_present(self, algorithm, dated_tracks):
        """Should return all URIs from input tracks."""
        result = algorithm.shuffle(dated_tracks)
        assert set(result) == {t["uri"] for t in dated_tracks}

    def test_preserves_count(self, algorithm, dated_tracks):
        """Should preserve the number of tracks."""
        result = algorithm.shuffle(dated_tracks)
        assert len(result) == len(dated_tracks)

    def test_returns_list_of_strings(self, algorithm, dated_tracks):
        """Should return list of URI strings."""
        result = algorithm.shuffle(dated_tracks)
        assert isinstance(result, list)
        assert all(isinstance(uri, str) for uri in result)

    def test_many_tracks_sorted_newest_first(self, algorithm):
        """Larger track list should sort newest at top with jitter=1."""
        tracks = [
            {
                "uri": f"spotify:track:{i}",
                "name": f"Track {i}",
                "added_at": f"2025-{i+1:02d}-15T00:00:00Z",
            }
            for i in range(12)
        ]
        result = algorithm.shuffle(tracks, jitter=1)

        # Should be in reverse order (December first, January last)
        expected = [f"spotify:track:{i}" for i in range(11, -1, -1)]
        assert result == expected


class TestNewestFirstShuffleJitter:
    """Tests for jitter parameter behavior."""

    @pytest.fixture
    def algorithm(self):
        """NewestFirstShuffle instance."""
        return NewestFirstShuffle()

    @pytest.fixture
    def many_dated_tracks(self):
        """20 tracks with sequential dates."""
        return [
            {
                "uri": f"spotify:track:{i}",
                "name": f"Track {i}",
                "added_at": f"2025-01-{i+1:02d}T00:00:00Z",
            }
            for i in range(20)
        ]

    def test_default_jitter_returns_all_uris(
        self, algorithm, many_dated_tracks
    ):
        """Default jitter should still return all URIs."""
        result = algorithm.shuffle(many_dated_tracks)
        assert set(result) == {t["uri"] for t in many_dated_tracks}
        assert len(result) == len(many_dated_tracks)

    def test_jitter_adds_variation(self, algorithm, many_dated_tracks):
        """Jitter > 1 should produce variation from exact sort."""
        exact = algorithm.shuffle(many_dated_tracks, jitter=1)
        # Run multiple times — at least one should differ
        different = False
        for _ in range(20):
            jittered = algorithm.shuffle(many_dated_tracks, jitter=5)
            if jittered != exact:
                different = True
                break
        assert different, "Jitter should produce variation from exact sort"

    def test_large_jitter_still_returns_all(
        self, algorithm, many_dated_tracks
    ):
        """Large jitter should still contain all tracks."""
        result = algorithm.shuffle(many_dated_tracks, jitter=50)
        assert set(result) == {t["uri"] for t in many_dated_tracks}
        assert len(result) == len(many_dated_tracks)

    def test_jitter_preserves_macro_order(self, algorithm):
        """With moderate jitter, newest tracks should trend toward the top."""
        tracks = [
            {
                "uri": f"spotify:track:{i}",
                "name": f"Track {i}",
                "added_at": f"2025-{(i // 3) + 1:02d}-01T00:00:00Z",
            }
            for i in range(30)
        ]

        # With jitter=3, the newest third should mostly be in top third
        top_third_counts = 0
        runs = 50
        for _ in range(runs):
            result = algorithm.shuffle(tracks, jitter=3)
            top_third = set(result[:10])
            # Track indices 27-29 are the newest (month 10)
            newest_uris = {f"spotify:track:{i}" for i in range(27, 30)}
            if newest_uris & top_third:
                top_third_counts += 1

        # Newest tracks should appear in top third most of the time
        assert top_third_counts > runs * 0.5


class TestNewestFirstShuffleEdgeCases:
    """Edge case tests for NewestFirstShuffle."""

    @pytest.fixture
    def algorithm(self):
        """NewestFirstShuffle instance."""
        return NewestFirstShuffle()

    def test_empty_list(self, algorithm):
        """Should handle empty track list."""
        result = algorithm.shuffle([])
        assert result == []

    def test_single_track(self, algorithm):
        """Should handle single track."""
        tracks = [
            {
                "uri": "spotify:track:only",
                "added_at": "2025-06-01T00:00:00Z",
            }
        ]
        result = algorithm.shuffle(tracks)
        assert result == ["spotify:track:only"]

    def test_missing_added_at_placed_at_end(self, algorithm):
        """Tracks without added_at should sort to the end."""
        tracks = [
            {"uri": "spotify:track:no_date", "name": "No Date"},
            {
                "uri": "spotify:track:has_date",
                "name": "Has Date",
                "added_at": "2026-01-01T00:00:00Z",
            },
        ]
        result = algorithm.shuffle(tracks, jitter=1)
        assert result[0] == "spotify:track:has_date"
        assert result[1] == "spotify:track:no_date"

    def test_all_missing_added_at(self, algorithm):
        """Should handle all tracks missing added_at."""
        tracks = [
            {"uri": f"spotify:track:{i}", "name": f"Track {i}"}
            for i in range(5)
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 5
        assert set(result) == {t["uri"] for t in tracks}

    def test_malformed_added_at(self, algorithm):
        """Should handle malformed added_at strings gracefully."""
        tracks = [
            {
                "uri": "spotify:track:bad",
                "added_at": "not-a-date",
            },
            {
                "uri": "spotify:track:good",
                "added_at": "2026-01-15T00:00:00Z",
            },
        ]
        result = algorithm.shuffle(tracks, jitter=1)
        assert result[0] == "spotify:track:good"
        assert result[1] == "spotify:track:bad"

    def test_null_added_at(self, algorithm):
        """Should handle None added_at value."""
        tracks = [
            {"uri": "spotify:track:null", "added_at": None},
            {
                "uri": "spotify:track:valid",
                "added_at": "2025-12-01T00:00:00Z",
            },
        ]
        result = algorithm.shuffle(tracks, jitter=1)
        assert result[0] == "spotify:track:valid"
        assert result[1] == "spotify:track:null"

    def test_all_same_added_at(self, algorithm):
        """Should handle all tracks with identical added_at."""
        tracks = [
            {
                "uri": f"spotify:track:{i}",
                "added_at": "2025-06-01T00:00:00Z",
            }
            for i in range(5)
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 5
        assert set(result) == {t["uri"] for t in tracks}

    def test_tracks_without_uri_skipped(self, algorithm):
        """Should skip tracks without URI."""
        tracks = [
            {
                "uri": "spotify:track:1",
                "added_at": "2025-01-01T00:00:00Z",
            },
            {"name": "No URI", "added_at": "2026-01-01T00:00:00Z"},
            {
                "uri": "spotify:track:2",
                "added_at": "2025-06-01T00:00:00Z",
            },
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 2

    def test_features_parameter_is_ignored(self, algorithm):
        """Should ignore features parameter."""
        tracks = [
            {
                "uri": f"spotify:track:{i}",
                "added_at": f"2025-0{i+1}-01T00:00:00Z",
            }
            for i in range(5)
        ]
        features = {"track0": {"tempo": 120}}
        result = algorithm.shuffle(tracks, features=features)
        assert len(result) == 5

    def test_mixed_timezone_formats(self, algorithm):
        """Should handle both Z and +00:00 timezone formats."""
        tracks = [
            {
                "uri": "spotify:track:z_format",
                "added_at": "2025-01-01T00:00:00Z",
            },
            {
                "uri": "spotify:track:offset_format",
                "added_at": "2025-06-01T00:00:00+00:00",
            },
        ]
        result = algorithm.shuffle(tracks, jitter=1)
        assert result[0] == "spotify:track:offset_format"
        assert result[1] == "spotify:track:z_format"
