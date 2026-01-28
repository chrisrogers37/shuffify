"""
Tests for Playlist model.

Playlist encapsulates Spotify playlist data including tracks and
optional audio features, providing helper methods for track manipulation.
"""

import pytest
from unittest.mock import Mock
from shuffify.models.playlist import Playlist


class TestPlaylistInitialization:
    """Test Playlist dataclass initialization."""

    def test_basic_initialization(self):
        """Should initialize with required fields."""
        playlist = Playlist(
            id='playlist_123',
            name='Test Playlist',
            owner_id='user_456'
        )

        assert playlist.id == 'playlist_123'
        assert playlist.name == 'Test Playlist'
        assert playlist.owner_id == 'user_456'

    def test_optional_fields_defaults(self):
        """Optional fields should have sensible defaults."""
        playlist = Playlist(
            id='playlist_123',
            name='Test Playlist',
            owner_id='user_456'
        )

        assert playlist.description is None
        assert playlist.tracks == []
        assert playlist.audio_features == {}

    def test_with_description(self):
        """Should accept description field."""
        playlist = Playlist(
            id='playlist_123',
            name='Test Playlist',
            owner_id='user_456',
            description='A great playlist'
        )

        assert playlist.description == 'A great playlist'

    def test_with_tracks(self, sample_tracks):
        """Should accept tracks list."""
        playlist = Playlist(
            id='playlist_123',
            name='Test Playlist',
            owner_id='user_456',
            tracks=sample_tracks
        )

        assert playlist.tracks == sample_tracks
        assert len(playlist.tracks) == 20

    def test_with_audio_features(self, sample_audio_features):
        """Should accept audio features dict."""
        playlist = Playlist(
            id='playlist_123',
            name='Test Playlist',
            owner_id='user_456',
            audio_features=sample_audio_features
        )

        assert playlist.audio_features == sample_audio_features

    def test_empty_id_raises_error(self):
        """Empty ID should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            Playlist(
                id='',
                name='Test Playlist',
                owner_id='user_456'
            )

        assert "Playlist ID is required" in str(exc_info.value)


class TestPlaylistFromSpotify:
    """Test Playlist.from_spotify() factory method."""

    def test_from_spotify_basic(self, mock_spotify_client, sample_playlist_data, sample_tracks):
        """Should create Playlist from Spotify API data."""
        playlist = Playlist.from_spotify(mock_spotify_client, 'playlist_123')

        assert playlist.id == sample_playlist_data['id']
        assert playlist.name == sample_playlist_data['name']
        assert playlist.owner_id == sample_playlist_data['owner']['id']
        assert playlist.description == sample_playlist_data.get('description')

    def test_from_spotify_calls_client_methods(self, mock_spotify_client):
        """Should call appropriate SpotifyClient methods."""
        Playlist.from_spotify(mock_spotify_client, 'playlist_123')

        mock_spotify_client.get_playlist.assert_called_once_with('playlist_123')
        mock_spotify_client.get_playlist_tracks.assert_called_once_with('playlist_123')

    def test_from_spotify_without_features(self, mock_spotify_client):
        """By default, should not fetch audio features."""
        Playlist.from_spotify(mock_spotify_client, 'playlist_123', include_features=False)

        mock_spotify_client.get_track_audio_features.assert_not_called()

    def test_from_spotify_with_features(self, mock_spotify_client, sample_audio_features):
        """With include_features=True, should fetch audio features."""
        playlist = Playlist.from_spotify(mock_spotify_client, 'playlist_123', include_features=True)

        mock_spotify_client.get_track_audio_features.assert_called_once()
        assert playlist.audio_features == sample_audio_features

    def test_from_spotify_filters_invalid_tracks(self, mock_spotify_client):
        """Should filter out tracks without id or uri."""
        mock_spotify_client.get_playlist_tracks.return_value = [
            {'id': 'track_1', 'name': 'Valid', 'uri': 'spotify:track:1'},
            {'id': None, 'name': 'No ID', 'uri': 'spotify:track:2'},
            {'id': 'track_3', 'name': 'No URI'},
            {'id': 'track_4', 'name': 'Valid 2', 'uri': 'spotify:track:4'},
        ]

        playlist = Playlist.from_spotify(mock_spotify_client, 'playlist_123')

        # Should only have tracks with both id and uri
        assert len(playlist.tracks) == 2
        assert playlist.tracks[0]['id'] == 'track_1'
        assert playlist.tracks[1]['id'] == 'track_4'


class TestPlaylistTrackMethods:
    """Test track retrieval methods."""

    def test_get_track_uris(self, sample_tracks):
        """get_track_uris should return list of URIs."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks
        )

        uris = playlist.get_track_uris()

        assert isinstance(uris, list)
        assert len(uris) == 20
        assert all(uri.startswith('spotify:track:') for uri in uris)

    def test_get_track_uris_empty_playlist(self):
        """get_track_uris on empty playlist should return empty list."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456'
        )

        assert playlist.get_track_uris() == []

    def test_get_track_by_uri(self, sample_tracks):
        """get_track should find track by URI."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks
        )

        track = playlist.get_track('spotify:track:track_5')

        assert track is not None
        assert track['id'] == 'track_5'
        assert track['name'] == 'Track 5'

    def test_get_track_not_found(self, sample_tracks):
        """get_track should return None for unknown URI."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks
        )

        track = playlist.get_track('spotify:track:nonexistent')

        assert track is None

    def test_get_tracks_with_features(self, sample_tracks, sample_audio_features):
        """get_tracks_with_features should attach features to tracks."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks,
            audio_features=sample_audio_features
        )

        enriched = playlist.get_tracks_with_features()

        assert len(enriched) == 20
        for track in enriched:
            assert 'features' in track
            # Features should be present for tracks with matching IDs
            if track['id'] in sample_audio_features:
                assert track['features'] == sample_audio_features[track['id']]

    def test_get_tracks_with_features_no_features_loaded(self, sample_tracks):
        """get_tracks_with_features without features should return empty features dict."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks
        )

        enriched = playlist.get_tracks_with_features()

        for track in enriched:
            assert 'features' in track
            assert track['features'] == {}

    def test_get_track_with_features(self, sample_tracks, sample_audio_features):
        """get_track_with_features should return single track with features."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks,
            audio_features=sample_audio_features
        )

        track = playlist.get_track_with_features('spotify:track:track_3')

        assert track is not None
        assert track['id'] == 'track_3'
        assert 'features' in track
        assert track['features'] == sample_audio_features['track_3']

    def test_get_track_with_features_not_found(self, sample_tracks):
        """get_track_with_features should return None for unknown URI."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks
        )

        track = playlist.get_track_with_features('spotify:track:nonexistent')

        assert track is None


class TestPlaylistFeatureMethods:
    """Test audio feature related methods."""

    def test_has_features_true(self, sample_audio_features):
        """has_features should return True when features are loaded."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            audio_features=sample_audio_features
        )

        assert playlist.has_features() is True

    def test_has_features_false(self):
        """has_features should return False when no features."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456'
        )

        assert playlist.has_features() is False

    def test_get_feature_stats(self, sample_audio_features):
        """get_feature_stats should calculate aggregate statistics."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            audio_features=sample_audio_features
        )

        stats = playlist.get_feature_stats()

        assert 'tempo' in stats
        assert 'energy' in stats
        assert 'valence' in stats
        assert 'danceability' in stats

        # Each stat should have min, max, avg
        for key in ['tempo', 'energy', 'valence', 'danceability']:
            assert 'min' in stats[key]
            assert 'max' in stats[key]
            assert 'avg' in stats[key]

    def test_get_feature_stats_empty(self):
        """get_feature_stats with no features should return empty dict."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456'
        )

        stats = playlist.get_feature_stats()

        assert stats == {}

    def test_get_feature_stats_values(self):
        """get_feature_stats should calculate correct values."""
        features = {
            'track_1': {'tempo': 100, 'energy': 0.5, 'valence': 0.3, 'danceability': 0.6},
            'track_2': {'tempo': 120, 'energy': 0.7, 'valence': 0.5, 'danceability': 0.8},
            'track_3': {'tempo': 110, 'energy': 0.6, 'valence': 0.4, 'danceability': 0.7},
        }

        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            audio_features=features
        )

        stats = playlist.get_feature_stats()

        assert stats['tempo']['min'] == 100
        assert stats['tempo']['max'] == 120
        assert stats['tempo']['avg'] == 110

        assert stats['energy']['min'] == 0.5
        assert stats['energy']['max'] == 0.7


class TestPlaylistConversion:
    """Test conversion methods."""

    def test_to_dict(self, sample_tracks, sample_audio_features):
        """to_dict should return dictionary representation."""
        playlist = Playlist(
            id='playlist_123',
            name='Test Playlist',
            owner_id='user_456',
            description='A description',
            tracks=sample_tracks,
            audio_features=sample_audio_features
        )

        d = playlist.to_dict()

        assert d['id'] == 'playlist_123'
        assert d['name'] == 'Test Playlist'
        assert d['owner_id'] == 'user_456'
        assert d['description'] == 'A description'
        assert d['tracks'] == sample_tracks
        assert d['audio_features'] == sample_audio_features

    def test_str_representation(self, sample_tracks):
        """__str__ should return readable representation."""
        playlist = Playlist(
            id='playlist_123',
            name='My Playlist',
            owner_id='user_456',
            tracks=sample_tracks
        )

        s = str(playlist)

        assert 'My Playlist' in s
        assert 'playlist_123' in s
        assert '20 tracks' in s


class TestPlaylistDunderMethods:
    """Test Python dunder methods."""

    def test_len(self, sample_tracks):
        """__len__ should return number of tracks."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks
        )

        assert len(playlist) == 20

    def test_len_empty(self):
        """__len__ on empty playlist should return 0."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456'
        )

        assert len(playlist) == 0

    def test_getitem(self, sample_tracks):
        """__getitem__ should allow indexing tracks."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks
        )

        track = playlist[5]

        assert track == sample_tracks[5]

    def test_getitem_negative_index(self, sample_tracks):
        """__getitem__ should support negative indices."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks
        )

        track = playlist[-1]

        assert track == sample_tracks[-1]

    def test_iter(self, sample_tracks):
        """__iter__ should allow iteration over tracks."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks
        )

        tracks_list = list(playlist)

        assert tracks_list == sample_tracks

    def test_iter_in_loop(self, sample_tracks):
        """Should be iterable in for loop."""
        playlist = Playlist(
            id='playlist_123',
            name='Test',
            owner_id='user_456',
            tracks=sample_tracks
        )

        count = 0
        for track in playlist:
            count += 1
            assert 'id' in track
            assert 'uri' in track

        assert count == 20
