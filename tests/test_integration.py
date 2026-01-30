"""
Integration tests for Shuffify.

Tests cover the full flow from authentication through shuffle and undo.
These tests verify that all modules work together correctly.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from shuffify.spotify import (
    SpotifyCredentials,
    SpotifyAuthManager,
    SpotifyAPI,
    SpotifyClient,
    TokenInfo,
)
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    StateService,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def credentials():
    """Test credentials."""
    return SpotifyCredentials(
        client_id='test_client_id',
        client_secret='test_client_secret',
        redirect_uri='http://localhost:5000/callback'
    )


@pytest.fixture
def valid_token_data():
    """Valid token dictionary."""
    return {
        'access_token': 'test_access_token',
        'token_type': 'Bearer',
        'expires_at': time.time() + 3600,
        'expires_in': 3600,
        'refresh_token': 'test_refresh_token',
        'scope': 'playlist-read-private playlist-modify-public'
    }


@pytest.fixture
def sample_user():
    """Sample user data."""
    return {
        'id': 'user123',
        'display_name': 'Test User',
        'email': 'test@example.com'
    }


@pytest.fixture
def sample_playlist():
    """Sample playlist data."""
    return {
        'id': 'playlist123',
        'name': 'Test Playlist',
        'description': 'A test playlist',
        'owner': {'id': 'user123'},
        'tracks': {'total': 10},
        'images': [{'url': 'https://example.com/cover.jpg'}]
    }


@pytest.fixture
def sample_tracks():
    """Sample tracks for testing."""
    return [
        {
            'id': f'track{i}',
            'name': f'Track {i}',
            'uri': f'spotify:track:track{i}',
            'duration_ms': 180000 + (i * 1000),
            'is_local': False,
            'artists': [{'name': f'Artist {i}'}],
            'album': {'name': f'Album {i}', 'images': [{'url': f'https://example.com/album{i}.jpg'}]},
            'external_urls': {'spotify': f'https://open.spotify.com/track/track{i}'}
        }
        for i in range(10)
    ]


@pytest.fixture
def mock_session():
    """Mock Flask session."""
    class MockSession(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.modified = False

    return MockSession()


# =============================================================================
# Auth Flow Integration Tests
# =============================================================================

class TestAuthFlowIntegration:
    """Tests for the complete authentication flow."""

    def test_auth_url_generation(self, credentials):
        """Should generate a valid Spotify auth URL."""
        auth_manager = SpotifyAuthManager(credentials)

        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.get_authorize_url.return_value = (
                'https://accounts.spotify.com/authorize?client_id=test&...'
            )

            url = auth_manager.get_auth_url()

            assert url.startswith('https://accounts.spotify.com/authorize')

    def test_token_exchange_and_client_creation(self, credentials, valid_token_data, sample_user):
        """Should exchange code for token and create working client."""
        auth_manager = SpotifyAuthManager(credentials)

        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.get_access_token.return_value = valid_token_data

            # Exchange code for token
            token_info = auth_manager.exchange_code('test_code')

            assert isinstance(token_info, TokenInfo)
            assert token_info.access_token == valid_token_data['access_token']

        # Create API client with token
        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify:
            mock_sp = Mock()
            mock_sp.current_user.return_value = sample_user
            mock_spotify.return_value = mock_sp

            api = SpotifyAPI(token_info, auth_manager)
            user = api.get_current_user()

            assert user['id'] == 'user123'


# =============================================================================
# Playlist Service Integration Tests
# =============================================================================

class TestPlaylistServiceIntegration:
    """Tests for playlist operations flow."""

    def test_get_and_update_playlist(self, valid_token_data, sample_playlist, sample_tracks):
        """Should get playlist, shuffle, and update."""
        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify:
            mock_sp = Mock()
            mock_sp.playlist.return_value = sample_playlist
            mock_sp.playlist_items.return_value = {
                'items': [{'track': t} for t in sample_tracks],
                'next': None
            }
            mock_sp.current_user.return_value = {'id': 'user123'}
            mock_sp.current_user_playlists.return_value = {
                'items': [sample_playlist],
                'next': None
            }
            mock_spotify.return_value = mock_sp

            # Create SpotifyClient
            credentials_dict = {
                'client_id': 'test_id',
                'client_secret': 'test_secret',
                'redirect_uri': 'http://localhost/callback'
            }
            client = SpotifyClient(token=valid_token_data, credentials=credentials_dict)

            # Use PlaylistService
            playlist_service = PlaylistService(client)

            # Get playlist
            playlist = playlist_service.get_playlist('playlist123')
            assert playlist.name == 'Test Playlist'
            assert len(playlist.tracks) == 10


# =============================================================================
# Shuffle Service Integration Tests
# =============================================================================

class TestShuffleServiceIntegration:
    """Tests for shuffle operations with all algorithms."""

    @pytest.fixture
    def sample_tracks_for_shuffle(self):
        """Tracks in format expected by shuffle algorithms."""
        return [
            {'uri': f'spotify:track:track{i}', 'name': f'Track {i}'}
            for i in range(20)
        ]

    def test_basic_shuffle_integration(self, sample_tracks_for_shuffle):
        """Should execute BasicShuffle and return valid URIs."""
        result = ShuffleService.execute(
            'BasicShuffle',
            sample_tracks_for_shuffle,
            params={'keep_first': 3}
        )

        # All URIs preserved
        original_uris = {t['uri'] for t in sample_tracks_for_shuffle}
        assert set(result) == original_uris

        # First 3 preserved in order
        first_3 = [t['uri'] for t in sample_tracks_for_shuffle[:3]]
        assert result[:3] == first_3

    def test_balanced_shuffle_integration(self, sample_tracks_for_shuffle):
        """Should execute BalancedShuffle with sections."""
        result = ShuffleService.execute(
            'BalancedShuffle',
            sample_tracks_for_shuffle,
            params={'section_count': 4, 'keep_first': 0}
        )

        original_uris = {t['uri'] for t in sample_tracks_for_shuffle}
        assert set(result) == original_uris
        assert len(result) == 20

    def test_percentage_shuffle_integration(self, sample_tracks_for_shuffle):
        """Should execute PercentageShuffle with front/back location."""
        result = ShuffleService.execute(
            'PercentageShuffle',
            sample_tracks_for_shuffle,
            params={'shuffle_percentage': 50.0, 'shuffle_location': 'back'}
        )

        original_uris = {t['uri'] for t in sample_tracks_for_shuffle}
        assert set(result) == original_uris

        # First 10 should be in original order
        first_10 = [t['uri'] for t in sample_tracks_for_shuffle[:10]]
        assert result[:10] == first_10

    def test_stratified_shuffle_integration(self, sample_tracks_for_shuffle):
        """Should execute StratifiedShuffle maintaining section order."""
        result = ShuffleService.execute(
            'StratifiedShuffle',
            sample_tracks_for_shuffle,
            params={'section_count': 5, 'keep_first': 0}
        )

        original_uris = {t['uri'] for t in sample_tracks_for_shuffle}
        assert set(result) == original_uris
        assert len(result) == 20


# =============================================================================
# State Service Integration Tests
# =============================================================================

class TestStateServiceIntegration:
    """Tests for state management with undo/redo."""

    def test_full_shuffle_undo_workflow(self, mock_session, sample_tracks):
        """Should handle complete shuffle and undo workflow."""
        playlist_id = 'playlist123'
        track_uris = [t['uri'] for t in sample_tracks]

        # 1. Initialize state
        StateService.initialize_playlist_state(mock_session, playlist_id, track_uris)

        # Verify initial state
        current = StateService.get_current_uris(mock_session, playlist_id)
        assert current == track_uris
        assert not StateService.can_undo(mock_session, playlist_id)

        # 2. Shuffle and record new state
        shuffled_uris = list(reversed(track_uris))
        StateService.record_new_state(mock_session, playlist_id, shuffled_uris)

        # Verify shuffled state
        current = StateService.get_current_uris(mock_session, playlist_id)
        assert current == shuffled_uris
        assert StateService.can_undo(mock_session, playlist_id)

        # 3. Undo shuffle
        restored_uris = StateService.undo(mock_session, playlist_id)

        assert restored_uris == track_uris
        assert not StateService.can_undo(mock_session, playlist_id)

    def test_multiple_shuffles_undo(self, mock_session, sample_tracks):
        """Should handle multiple shuffles and step-by-step undo."""
        playlist_id = 'playlist123'
        track_uris = [t['uri'] for t in sample_tracks]

        # Initialize
        StateService.initialize_playlist_state(mock_session, playlist_id, track_uris)

        # Multiple shuffles
        state1 = list(reversed(track_uris))
        StateService.record_new_state(mock_session, playlist_id, state1)

        state2 = track_uris[5:] + track_uris[:5]  # Rotate
        StateService.record_new_state(mock_session, playlist_id, state2)

        state3 = sorted(track_uris)  # Sort
        StateService.record_new_state(mock_session, playlist_id, state3)

        # Current should be state3
        assert StateService.get_current_uris(mock_session, playlist_id) == state3

        # Undo to state2
        restored = StateService.undo(mock_session, playlist_id)
        assert restored == state2

        # Undo to state1
        restored = StateService.undo(mock_session, playlist_id)
        assert restored == state1

        # Undo to original
        restored = StateService.undo(mock_session, playlist_id)
        assert restored == track_uris


# =============================================================================
# Full Flow Integration Test
# =============================================================================

class TestFullFlowIntegration:
    """Tests for the complete Shuffify flow."""

    def test_complete_shuffle_flow(
        self,
        valid_token_data,
        sample_playlist,
        sample_tracks,
        mock_session
    ):
        """Test the complete flow: auth -> get playlist -> shuffle -> undo."""
        # Setup mock Spotify API
        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify:
            mock_sp = Mock()
            mock_sp.current_user.return_value = {'id': 'user123', 'display_name': 'Test'}
            mock_sp.playlist.return_value = sample_playlist
            mock_sp.playlist_items.return_value = {
                'items': [{'track': t} for t in sample_tracks],
                'next': None
            }
            mock_sp.playlist_replace_items.return_value = None
            mock_spotify.return_value = mock_sp

            # 1. Create authenticated client
            credentials_dict = {
                'client_id': 'test_id',
                'client_secret': 'test_secret',
                'redirect_uri': 'http://localhost/callback'
            }
            client = SpotifyClient(token=valid_token_data, credentials=credentials_dict)

            # 2. Get playlist
            playlist_service = PlaylistService(client)
            playlist = playlist_service.get_playlist('playlist123')

            assert playlist.name == 'Test Playlist'
            assert len(playlist.tracks) == 10

            # 3. Get current URIs
            current_uris = playlist.get_track_uris()
            assert len(current_uris) == 10

            # 4. Initialize state
            StateService.initialize_playlist_state(mock_session, 'playlist123', current_uris)

            # 5. Execute shuffle
            shuffled_uris = ShuffleService.execute(
                'BasicShuffle',
                playlist.tracks,
                params={'keep_first': 2}
            )

            # Verify shuffle worked
            assert len(shuffled_uris) == 10
            assert shuffled_uris[:2] == current_uris[:2]  # First 2 preserved

            # 6. Check if order changed
            changed = ShuffleService.shuffle_changed_order(current_uris, shuffled_uris)
            # May or may not change depending on random seed

            # 7. Record new state
            StateService.record_new_state(mock_session, 'playlist123', shuffled_uris)

            # 8. Verify can undo
            assert StateService.can_undo(mock_session, 'playlist123')

            # 9. Undo
            restored = StateService.undo(mock_session, 'playlist123')
            assert restored == current_uris

            # 10. Verify cannot undo further
            assert not StateService.can_undo(mock_session, 'playlist123')


# =============================================================================
# Spotify Module Integration Tests
# =============================================================================

class TestSpotifyModuleIntegration:
    """Tests for Spotify module components working together."""

    def test_credentials_to_auth_manager_to_api(self, credentials, valid_token_data, sample_user):
        """Test flow from credentials through auth manager to API."""
        # Create auth manager from credentials
        auth_manager = SpotifyAuthManager(credentials)

        # Mock OAuth exchange
        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.get_access_token.return_value = valid_token_data

            token_info = auth_manager.exchange_code('test_code')

        # Create API with token and auth manager
        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify:
            mock_sp = Mock()
            mock_sp.current_user.return_value = sample_user
            mock_spotify.return_value = mock_sp

            api = SpotifyAPI(token_info, auth_manager)

            # Verify API works
            user = api.get_current_user()
            assert user['display_name'] == 'Test User'

    def test_client_facade_backward_compatibility(self, valid_token_data, sample_user):
        """Test that SpotifyClient facade maintains backward compatibility."""
        credentials_dict = {
            'client_id': 'test_id',
            'client_secret': 'test_secret',
            'redirect_uri': 'http://localhost/callback'
        }

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify:
            mock_sp = Mock()
            mock_sp.current_user.return_value = sample_user
            mock_spotify.return_value = mock_sp

            # Old usage pattern
            client = SpotifyClient(token=valid_token_data, credentials=credentials_dict)

            # Old methods still work
            user = client.get_current_user()
            assert user['id'] == 'user123'
