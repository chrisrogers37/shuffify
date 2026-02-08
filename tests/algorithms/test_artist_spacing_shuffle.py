"""
Tests for ArtistSpacingShuffle algorithm.

Tests cover artist spacing enforcement, edge cases, and fallback behavior.
"""

import pytest
import random

from shuffify.shuffle_algorithms.artist_spacing import ArtistSpacingShuffle


class TestArtistSpacingShuffleProperties:
    """Tests for ArtistSpacingShuffle metadata properties."""

    def test_name_is_artist_spacing(self):
        """Should return 'Artist Spacing' as name."""
        algo = ArtistSpacingShuffle()
        assert algo.name == "Artist Spacing"

    def test_description_is_present(self):
        """Should have a meaningful description."""
        algo = ArtistSpacingShuffle()
        assert algo.description
        assert len(algo.description) > 10

    def test_parameters_includes_min_spacing(self):
        """Should include min_spacing parameter."""
        algo = ArtistSpacingShuffle()
        params = algo.parameters

        assert "min_spacing" in params
        assert params["min_spacing"]["type"] == "integer"
        assert params["min_spacing"]["default"] == 1
        assert params["min_spacing"]["min"] == 1

    def test_requires_features_is_false(self):
        """Should not require audio features."""
        algo = ArtistSpacingShuffle()
        assert algo.requires_features is False


class TestArtistSpacingShuffleShuffle:
    """Tests for ArtistSpacingShuffle.shuffle method."""

    @pytest.fixture
    def algorithm(self):
        """ArtistSpacingShuffle instance."""
        return ArtistSpacingShuffle()

    @pytest.fixture
    def sample_tracks(self):
        """Sample tracks with varied artists."""
        return [
            {
                "uri": "spotify:track:1",
                "name": "Song A1",
                "artists": [{"name": "Artist A"}],
            },
            {
                "uri": "spotify:track:2",
                "name": "Song A2",
                "artists": [{"name": "Artist A"}],
            },
            {
                "uri": "spotify:track:3",
                "name": "Song B1",
                "artists": [{"name": "Artist B"}],
            },
            {
                "uri": "spotify:track:4",
                "name": "Song B2",
                "artists": [{"name": "Artist B"}],
            },
            {
                "uri": "spotify:track:5",
                "name": "Song C1",
                "artists": [{"name": "Artist C"}],
            },
            {
                "uri": "spotify:track:6",
                "name": "Song C2",
                "artists": [{"name": "Artist C"}],
            },
        ]

    def test_shuffle_returns_all_uris(self, algorithm, sample_tracks):
        """Should return all URIs from input tracks."""
        result = algorithm.shuffle(sample_tracks)

        original_uris = {t["uri"] for t in sample_tracks}
        result_uris = set(result)

        assert original_uris == result_uris

    def test_shuffle_returns_list_of_strings(self, algorithm, sample_tracks):
        """Should return list of URI strings."""
        result = algorithm.shuffle(sample_tracks)

        assert isinstance(result, list)
        assert all(isinstance(uri, str) for uri in result)

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
        tracks = [{"uri": "spotify:track:single", "artists": [{"name": "Solo"}]}]
        result = algorithm.shuffle(tracks)
        assert result == ["spotify:track:single"]

    def test_no_back_to_back_same_artist(self, algorithm, sample_tracks):
        """With min_spacing=1, same artist should never appear back-to-back."""
        # Run multiple times to account for randomness
        for _ in range(20):
            result = algorithm.shuffle(sample_tracks, min_spacing=1)
            uri_to_artist = {t["uri"]: t["artists"][0]["name"] for t in sample_tracks}

            for i in range(len(result) - 1):
                artist_current = uri_to_artist[result[i]]
                artist_next = uri_to_artist[result[i + 1]]
                assert artist_current != artist_next, (
                    f"Back-to-back artist found: {artist_current} "
                    f"at positions {i} and {i + 1}"
                )


class TestArtistSpacingShuffleSpacing:
    """Tests for min_spacing parameter behavior."""

    @pytest.fixture
    def algorithm(self):
        """ArtistSpacingShuffle instance."""
        return ArtistSpacingShuffle()

    @pytest.fixture
    def many_artists_tracks(self):
        """Tracks with enough diversity for larger spacing."""
        tracks = []
        artists = ["A", "B", "C", "D", "E"]
        for i, artist in enumerate(artists):
            for j in range(3):
                idx = i * 3 + j
                tracks.append(
                    {
                        "uri": f"spotify:track:{idx}",
                        "name": f"Song {artist}{j}",
                        "artists": [{"name": f"Artist {artist}"}],
                    }
                )
        return tracks

    def test_min_spacing_default(self, algorithm, many_artists_tracks):
        """Default min_spacing=1 should prevent back-to-back."""
        for _ in range(10):
            result = algorithm.shuffle(many_artists_tracks)
            uri_to_artist = {
                t["uri"]: t["artists"][0]["name"] for t in many_artists_tracks
            }
            for i in range(len(result) - 1):
                assert uri_to_artist[result[i]] != uri_to_artist[result[i + 1]]

    def test_min_spacing_greater_than_one(self, algorithm, many_artists_tracks):
        """min_spacing=2 should enforce at least 2 tracks between same artist."""
        for _ in range(10):
            result = algorithm.shuffle(many_artists_tracks, min_spacing=2)
            uri_to_artist = {
                t["uri"]: t["artists"][0]["name"] for t in many_artists_tracks
            }
            for i in range(len(result)):
                for j in range(i + 1, min(i + 3, len(result))):
                    if uri_to_artist[result[i]] == uri_to_artist[result[j]]:
                        gap = j - i
                        assert gap > 2, (
                            f"Artist {uri_to_artist[result[i]]} appeared "
                            f"with only {gap - 1} tracks spacing"
                        )

    def test_impossible_spacing_still_works(self, algorithm):
        """When spacing is impossible (too few unique artists), should still return all tracks."""
        # 2 artists, 5 tracks each, min_spacing=5 — impossible to satisfy fully
        tracks = [
            {
                "uri": f"spotify:track:{i}",
                "artists": [{"name": f"Artist {'A' if i < 5 else 'B'}"}],
            }
            for i in range(10)
        ]
        result = algorithm.shuffle(tracks, min_spacing=5)
        assert len(result) == 10
        assert set(result) == {t["uri"] for t in tracks}


class TestArtistSpacingShuffleEdgeCases:
    """Edge case tests for ArtistSpacingShuffle."""

    @pytest.fixture
    def algorithm(self):
        """ArtistSpacingShuffle instance."""
        return ArtistSpacingShuffle()

    def test_all_same_artist(self, algorithm):
        """Should handle all tracks from same artist."""
        tracks = [
            {"uri": f"spotify:track:{i}", "artists": [{"name": "Same Artist"}]}
            for i in range(5)
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 5
        assert set(result) == {t["uri"] for t in tracks}

    def test_tracks_without_artists_key(self, algorithm):
        """Should handle tracks missing artists key."""
        tracks = [
            {"uri": "spotify:track:1"},
            {"uri": "spotify:track:2", "artists": [{"name": "Artist A"}]},
            {"uri": "spotify:track:3"},
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 3

    def test_tracks_with_empty_artists(self, algorithm):
        """Should handle tracks with empty artists list."""
        tracks = [
            {"uri": "spotify:track:1", "artists": []},
            {"uri": "spotify:track:2", "artists": [{"name": "Artist A"}]},
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 2

    def test_tracks_without_uri_skipped(self, algorithm):
        """Should skip tracks without URI."""
        tracks = [
            {"uri": "spotify:track:1", "artists": [{"name": "A"}]},
            {"name": "No URI", "artists": [{"name": "B"}]},
            {"uri": "spotify:track:2", "artists": [{"name": "C"}]},
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 2

    def test_features_parameter_is_ignored(self, algorithm):
        """Should ignore features parameter."""
        tracks = [
            {"uri": f"spotify:track:{i}", "artists": [{"name": f"Artist {i}"}]}
            for i in range(5)
        ]
        features = {"track0": {"tempo": 120}}
        result = algorithm.shuffle(tracks, features=features)
        assert len(result) == 5

    def test_shuffle_is_random(self, algorithm):
        """Should produce different results on different runs."""
        tracks = [
            {"uri": f"spotify:track:{i}", "artists": [{"name": f"Artist {i}"}]}
            for i in range(10)
        ]
        results = [tuple(algorithm.shuffle(tracks)) for _ in range(10)]
        unique_results = set(results)
        assert len(unique_results) > 1
