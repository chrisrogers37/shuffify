"""
Tests for AlbumSequenceShuffle algorithm.

Tests cover album grouping, shuffle behavior, and edge cases.
"""

import pytest
import random

from shuffify.shuffle_algorithms.album_sequence import AlbumSequenceShuffle


class TestAlbumSequenceShuffleProperties:
    """Tests for AlbumSequenceShuffle metadata properties."""

    def test_name_is_album_sequence(self):
        """Should return 'Album Sequence' as name."""
        algo = AlbumSequenceShuffle()
        assert algo.name == "Album Sequence"

    def test_description_is_present(self):
        """Should have a meaningful description."""
        algo = AlbumSequenceShuffle()
        assert algo.description
        assert len(algo.description) > 10

    def test_parameters_includes_shuffle_within_albums(self):
        """Should include shuffle_within_albums parameter."""
        algo = AlbumSequenceShuffle()
        params = algo.parameters

        assert "shuffle_within_albums" in params
        assert params["shuffle_within_albums"]["type"] == "string"
        assert params["shuffle_within_albums"]["default"] == "no"
        assert "yes" in params["shuffle_within_albums"]["options"]
        assert "no" in params["shuffle_within_albums"]["options"]

    def test_requires_features_is_false(self):
        """Should not require audio features."""
        algo = AlbumSequenceShuffle()
        assert algo.requires_features is False


class TestAlbumSequenceShuffleShuffle:
    """Tests for AlbumSequenceShuffle.shuffle method."""

    @pytest.fixture
    def algorithm(self):
        """AlbumSequenceShuffle instance."""
        return AlbumSequenceShuffle()

    @pytest.fixture
    def sample_tracks(self):
        """Sample tracks from multiple albums."""
        return [
            # Album A - 3 tracks
            {
                "uri": "spotify:track:a1",
                "name": "A Track 1",
                "album": {"name": "Album A"},
            },
            {
                "uri": "spotify:track:a2",
                "name": "A Track 2",
                "album": {"name": "Album A"},
            },
            {
                "uri": "spotify:track:a3",
                "name": "A Track 3",
                "album": {"name": "Album A"},
            },
            # Album B - 2 tracks
            {
                "uri": "spotify:track:b1",
                "name": "B Track 1",
                "album": {"name": "Album B"},
            },
            {
                "uri": "spotify:track:b2",
                "name": "B Track 2",
                "album": {"name": "Album B"},
            },
            # Album C - 2 tracks
            {
                "uri": "spotify:track:c1",
                "name": "C Track 1",
                "album": {"name": "Album C"},
            },
            {
                "uri": "spotify:track:c2",
                "name": "C Track 2",
                "album": {"name": "Album C"},
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
        tracks = [{"uri": "spotify:track:single", "album": {"name": "Solo Album"}}]
        result = algorithm.shuffle(tracks)
        assert result == ["spotify:track:single"]

    def test_album_tracks_stay_together(self, algorithm, sample_tracks):
        """Tracks from the same album should be adjacent in result."""
        for _ in range(20):
            result = algorithm.shuffle(sample_tracks)

            # Build URI-to-album mapping
            uri_to_album = {t["uri"]: t["album"]["name"] for t in sample_tracks}

            # Check contiguity: find each album's tracks and verify they're adjacent
            album_a_uris = {"spotify:track:a1", "spotify:track:a2", "spotify:track:a3"}
            album_b_uris = {"spotify:track:b1", "spotify:track:b2"}
            album_c_uris = {"spotify:track:c1", "spotify:track:c2"}

            for album_uris in [album_a_uris, album_b_uris, album_c_uris]:
                positions = [i for i, uri in enumerate(result) if uri in album_uris]
                # Positions should be consecutive
                assert positions == list(
                    range(positions[0], positions[0] + len(positions))
                ), f"Album tracks not contiguous: positions {positions}"

    def test_album_internal_order_preserved_by_default(
        self, algorithm, sample_tracks
    ):
        """By default, track order within each album should be preserved."""
        for _ in range(20):
            result = algorithm.shuffle(sample_tracks)

            # Find Album A tracks in result
            album_a_in_result = [
                uri for uri in result if uri.startswith("spotify:track:a")
            ]
            assert album_a_in_result == [
                "spotify:track:a1",
                "spotify:track:a2",
                "spotify:track:a3",
            ]

    def test_album_order_is_shuffled(self, algorithm, sample_tracks):
        """Album order should change across multiple shuffles."""
        orders = set()
        for _ in range(30):
            result = algorithm.shuffle(sample_tracks)
            # Record which album is first
            uri_to_album = {t["uri"]: t["album"]["name"] for t in sample_tracks}
            first_album = uri_to_album[result[0]]
            orders.add(first_album)

        # With 3 albums, we should see more than one leading album
        assert len(orders) > 1


class TestAlbumSequenceShuffleWithinAlbums:
    """Tests for shuffle_within_albums parameter."""

    @pytest.fixture
    def algorithm(self):
        """AlbumSequenceShuffle instance."""
        return AlbumSequenceShuffle()

    @pytest.fixture
    def sample_tracks(self):
        """Sample tracks from multiple albums."""
        return [
            {
                "uri": f"spotify:track:a{i}",
                "name": f"A Track {i}",
                "album": {"name": "Album A"},
            }
            for i in range(1, 8)  # 7 tracks in one album for better shuffle detection
        ] + [
            {
                "uri": f"spotify:track:b{i}",
                "name": f"B Track {i}",
                "album": {"name": "Album B"},
            }
            for i in range(1, 6)  # 5 tracks in another
        ]

    def test_shuffle_within_albums_no(self, algorithm, sample_tracks):
        """shuffle_within_albums='no' should preserve internal album order."""
        for _ in range(10):
            result = algorithm.shuffle(sample_tracks, shuffle_within_albums="no")

            album_a = [u for u in result if u.startswith("spotify:track:a")]
            expected_a = [f"spotify:track:a{i}" for i in range(1, 8)]
            assert album_a == expected_a

    def test_shuffle_within_albums_yes(self, algorithm, sample_tracks):
        """shuffle_within_albums='yes' should shuffle tracks within albums."""
        random.seed(42)
        internal_orders = set()
        for _ in range(20):
            result = algorithm.shuffle(sample_tracks, shuffle_within_albums="yes")

            album_a = [u for u in result if u.startswith("spotify:track:a")]
            internal_orders.add(tuple(album_a))

        # Should see variation in internal order
        assert len(internal_orders) > 1


class TestAlbumSequenceShuffleEdgeCases:
    """Edge case tests for AlbumSequenceShuffle."""

    @pytest.fixture
    def algorithm(self):
        """AlbumSequenceShuffle instance."""
        return AlbumSequenceShuffle()

    def test_all_same_album(self, algorithm):
        """Should handle all tracks from same album."""
        tracks = [
            {"uri": f"spotify:track:{i}", "album": {"name": "Only Album"}}
            for i in range(5)
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 5
        assert set(result) == {t["uri"] for t in tracks}

    def test_each_track_different_album(self, algorithm):
        """Should handle each track from a different album."""
        tracks = [
            {"uri": f"spotify:track:{i}", "album": {"name": f"Album {i}"}}
            for i in range(5)
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 5

    def test_tracks_without_album_key(self, algorithm):
        """Should handle tracks missing album key."""
        tracks = [
            {"uri": "spotify:track:1"},
            {"uri": "spotify:track:2", "album": {"name": "Album A"}},
            {"uri": "spotify:track:3"},
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 3

    def test_tracks_with_album_name_field(self, algorithm):
        """Should handle tracks with album_name instead of album dict."""
        tracks = [
            {"uri": "spotify:track:1", "album_name": "Album A"},
            {"uri": "spotify:track:2", "album_name": "Album A"},
            {"uri": "spotify:track:3", "album_name": "Album B"},
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 3

    def test_tracks_without_uri_skipped(self, algorithm):
        """Should skip tracks without URI."""
        tracks = [
            {"uri": "spotify:track:1", "album": {"name": "Album A"}},
            {"name": "No URI", "album": {"name": "Album A"}},
            {"uri": "spotify:track:2", "album": {"name": "Album B"}},
        ]
        result = algorithm.shuffle(tracks)
        assert len(result) == 2

    def test_features_parameter_is_ignored(self, algorithm):
        """Should ignore features parameter."""
        tracks = [
            {"uri": f"spotify:track:{i}", "album": {"name": f"Album {i}"}}
            for i in range(5)
        ]
        features = {"track0": {"tempo": 120}}
        result = algorithm.shuffle(tracks, features=features)
        assert len(result) == 5
