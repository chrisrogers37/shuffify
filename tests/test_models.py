"""
Tests for the Playlist data model.

Tests construction, validation, track operations, feature statistics,
and serialization without requiring Spotify API access.
"""

import pytest

from shuffify.models.playlist import Playlist


class TestPlaylistConstruction:
    """Test Playlist creation and validation."""

    def test_create_minimal_playlist(self):
        playlist = Playlist(id="abc123", name="My Playlist", owner_id="user1")
        assert playlist.id == "abc123"
        assert playlist.name == "My Playlist"
        assert playlist.owner_id == "user1"
        assert playlist.tracks == []
        assert playlist.audio_features == {}
        assert playlist.description is None

    def test_create_playlist_with_all_fields(self):
        tracks = [{"id": "t1", "uri": "spotify:track:t1", "name": "Track 1"}]
        features = {"t1": {"tempo": 120.0, "energy": 0.8}}
        playlist = Playlist(
            id="abc123",
            name="Full Playlist",
            owner_id="user1",
            description="A test playlist",
            tracks=tracks,
            audio_features=features,
        )
        assert playlist.description == "A test playlist"
        assert len(playlist.tracks) == 1
        assert "t1" in playlist.audio_features

    def test_empty_id_raises_value_error(self):
        with pytest.raises(ValueError, match="Playlist ID is required"):
            Playlist(id="", name="Test", owner_id="user1")

    def test_none_id_raises_error(self):
        with pytest.raises((ValueError, TypeError)):
            Playlist(id=None, name="Test", owner_id="user1")


class TestPlaylistTrackOperations:
    """Test track-related methods."""

    @pytest.fixture
    def playlist_with_tracks(self):
        tracks = [
            {"id": "t1", "uri": "spotify:track:t1", "name": "Track 1"},
            {"id": "t2", "uri": "spotify:track:t2", "name": "Track 2"},
            {"id": "t3", "uri": "spotify:track:t3", "name": "Track 3"},
        ]
        return Playlist(id="p1", name="Test", owner_id="u1", tracks=tracks)

    def test_get_track_uris(self, playlist_with_tracks):
        uris = playlist_with_tracks.get_track_uris()
        assert uris == [
            "spotify:track:t1",
            "spotify:track:t2",
            "spotify:track:t3",
        ]

    def test_get_track_uris_skips_missing_uri(self):
        tracks = [
            {"id": "t1", "uri": "spotify:track:t1", "name": "Track 1"},
            {"id": "t2", "name": "Track 2"},  # No URI
        ]
        playlist = Playlist(id="p1", name="Test", owner_id="u1", tracks=tracks)
        uris = playlist.get_track_uris()
        assert uris == ["spotify:track:t1"]

    def test_get_track_uris_empty_playlist(self):
        playlist = Playlist(id="p1", name="Test", owner_id="u1")
        assert playlist.get_track_uris() == []

    def test_len_returns_track_count(self, playlist_with_tracks):
        assert len(playlist_with_tracks) == 3

    def test_len_empty_playlist(self):
        playlist = Playlist(id="p1", name="Test", owner_id="u1")
        assert len(playlist) == 0

    def test_getitem_returns_track(self, playlist_with_tracks):
        track = playlist_with_tracks[0]
        assert track["name"] == "Track 1"

    def test_getitem_out_of_range_raises(self, playlist_with_tracks):
        with pytest.raises(IndexError):
            _ = playlist_with_tracks[10]

    def test_iter_yields_tracks(self, playlist_with_tracks):
        names = [t["name"] for t in playlist_with_tracks]
        assert names == ["Track 1", "Track 2", "Track 3"]


class TestPlaylistFeatures:
    """Test audio feature methods."""

    def test_has_features_true(self):
        playlist = Playlist(
            id="p1",
            name="Test",
            owner_id="u1",
            audio_features={"t1": {"tempo": 120.0}},
        )
        assert playlist.has_features() is True

    def test_has_features_false(self):
        playlist = Playlist(id="p1", name="Test", owner_id="u1")
        assert playlist.has_features() is False

    def test_get_feature_stats_computes_correctly(self):
        features = {
            "t1": {"tempo": 100.0, "energy": 0.5, "valence": 0.3, "danceability": 0.6},
            "t2": {"tempo": 140.0, "energy": 0.9, "valence": 0.7, "danceability": 0.8},
        }
        playlist = Playlist(
            id="p1", name="Test", owner_id="u1", audio_features=features
        )
        stats = playlist.get_feature_stats()

        assert stats["tempo"]["min"] == 100.0
        assert stats["tempo"]["max"] == 140.0
        assert stats["tempo"]["avg"] == 120.0
        assert stats["energy"]["min"] == 0.5
        assert stats["energy"]["max"] == 0.9
        assert stats["energy"]["avg"] == pytest.approx(0.7)

    def test_get_feature_stats_empty_features(self):
        playlist = Playlist(id="p1", name="Test", owner_id="u1")
        assert playlist.get_feature_stats() == {}

    def test_get_feature_stats_partial_features(self):
        """Test when some tracks have features and others don't."""
        features = {
            "t1": {"tempo": 120.0, "energy": 0.5},
            # Missing valence and danceability
        }
        playlist = Playlist(
            id="p1", name="Test", owner_id="u1", audio_features=features
        )
        stats = playlist.get_feature_stats()
        assert "tempo" in stats
        assert stats["tempo"]["avg"] == 120.0


class TestPlaylistSerialization:
    """Test to_dict and string representation."""

    def test_to_dict_contains_all_fields(self):
        tracks = [{"id": "t1", "uri": "spotify:track:t1", "name": "Track 1"}]
        playlist = Playlist(
            id="p1",
            name="Test Playlist",
            owner_id="u1",
            description="desc",
            tracks=tracks,
            audio_features={"t1": {"tempo": 120}},
        )
        d = playlist.to_dict()
        assert d["id"] == "p1"
        assert d["name"] == "Test Playlist"
        assert d["owner_id"] == "u1"
        assert d["description"] == "desc"
        assert len(d["tracks"]) == 1
        assert "t1" in d["audio_features"]

    def test_to_dict_with_defaults(self):
        playlist = Playlist(id="p1", name="Test", owner_id="u1")
        d = playlist.to_dict()
        assert d["description"] is None
        assert d["tracks"] == []
        assert d["audio_features"] == {}

    def test_str_representation(self):
        tracks = [{"id": f"t{i}", "uri": f"uri:{i}", "name": f"T{i}"} for i in range(5)]
        playlist = Playlist(id="p1", name="My Mix", owner_id="u1", tracks=tracks)
        s = str(playlist)
        assert "My Mix" in s
        assert "p1" in s
        assert "5 tracks" in s
