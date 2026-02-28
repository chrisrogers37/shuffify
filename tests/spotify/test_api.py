"""
Tests for SpotifyAPI.

Tests cover all data operations: user, playlists, tracks, and audio features.
Also includes tests for caching integration and error handler decorator.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.error_handling import api_error_handler
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.cache import SpotifyCache
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
    SpotifyRateLimitError,
    SpotifyTokenExpiredError,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def credentials():
    """Valid SpotifyCredentials for testing."""
    return SpotifyCredentials(
        client_id='test_client_id',
        client_secret='test_client_secret',
        redirect_uri='http://localhost:5000/callback'
    )


@pytest.fixture
def auth_manager(credentials):
    """SpotifyAuthManager instance."""
    return SpotifyAuthManager(credentials)


@pytest.fixture
def valid_token_info():
    """Valid TokenInfo for testing."""
    return TokenInfo(
        access_token='test_access_token',
        token_type='Bearer',
        expires_at=time.time() + 3600,
        refresh_token='test_refresh_token'
    )


@pytest.fixture
def expired_token_info():
    """Expired TokenInfo for testing."""
    return TokenInfo(
        access_token='expired_token',
        token_type='Bearer',
        expires_at=time.time() - 100,
        refresh_token='test_refresh_token'
    )


@pytest.fixture
def sample_user():
    """Sample user data."""
    return {
        'id': 'user123',
        'display_name': 'Test User',
        'email': 'test@example.com'
    }


@pytest.fixture
def sample_playlists():
    """Sample playlists data."""
    return [
        {
            'id': 'playlist1',
            'name': 'Playlist 1',
            'owner': {'id': 'user123'},
        },
        {
            'id': 'playlist2',
            'name': 'Playlist 2',
            'owner': {'id': 'user123'},
        },
    ]


@pytest.fixture
def sample_tracks():
    """Sample tracks data."""
    return [
        {'uri': 'spotify:track:track1', 'name': 'Track 1'},
        {'uri': 'spotify:track:track2', 'name': 'Track 2'},
        {'uri': 'spotify:track:track3', 'name': 'Track 3'},
    ]


# =============================================================================
# API Error Handler Tests
# =============================================================================

class TestApiErrorHandler:
    """Tests for the api_error_handler decorator."""

    def test_passes_through_successful_calls(self):
        """Should return result for successful calls."""
        @api_error_handler
        def success_func():
            return 'success'

        result = success_func()
        assert result == 'success'

    def test_passes_through_not_found_error(self):
        """Should pass through SpotifyNotFoundError."""
        @api_error_handler
        def not_found_func():
            raise SpotifyNotFoundError("Not found")

        with pytest.raises(SpotifyNotFoundError):
            not_found_func()

    def test_passes_through_rate_limit_error(self):
        """Should pass through SpotifyRateLimitError."""
        @api_error_handler
        def rate_limit_func():
            raise SpotifyRateLimitError(
                "Rate limited", retry_after=30
            )

        with pytest.raises(SpotifyRateLimitError):
            rate_limit_func()

    def test_passes_through_token_expired_error(self):
        """Should pass through SpotifyTokenExpiredError."""
        @api_error_handler
        def token_expired_func():
            raise SpotifyTokenExpiredError("Token expired")

        with pytest.raises(SpotifyTokenExpiredError):
            token_expired_func()

    def test_passes_through_api_error(self):
        """Should pass through SpotifyAPIError."""
        @api_error_handler
        def api_error_func():
            raise SpotifyAPIError("API error")

        with pytest.raises(SpotifyAPIError):
            api_error_func()

    def test_wraps_unexpected_exceptions(self):
        """Should wrap unexpected exceptions in SpotifyAPIError."""
        @api_error_handler
        def unexpected_func():
            raise ValueError('Unexpected error')

        with pytest.raises(SpotifyAPIError, match="Unexpected error"):
            unexpected_func()


# =============================================================================
# SpotifyAPI Initialization Tests
# =============================================================================

class TestSpotifyAPIInit:
    """Tests for SpotifyAPI initialization."""

    def test_init_with_valid_token(
        self, valid_token_info, auth_manager
    ):
        """Should initialize with valid token."""
        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            api = SpotifyAPI(valid_token_info, auth_manager)

            assert api._token_info == valid_token_info
            assert api._auth_manager == auth_manager

    def test_init_with_expired_token_auto_refresh(
        self, expired_token_info, auth_manager
    ):
        """Should auto-refresh expired token when enabled."""
        new_token = TokenInfo(
            access_token='new_token',
            token_type='Bearer',
            expires_at=time.time() + 3600,
            refresh_token='new_refresh'
        )

        with patch.object(
            auth_manager, 'ensure_valid_token',
            return_value=new_token,
        ):
            with patch('shuffify.spotify.api.SpotifyHTTPClient'):
                api = SpotifyAPI(
                    expired_token_info, auth_manager,
                    auto_refresh=True,
                )
                assert api._token_info == new_token

    def test_init_with_expired_token_no_auto_refresh(
        self, expired_token_info, auth_manager
    ):
        """Should raise error for expired token when auto_refresh disabled."""
        with pytest.raises(SpotifyTokenExpiredError):
            SpotifyAPI(
                expired_token_info, auth_manager,
                auto_refresh=False,
            )

    def test_token_info_property(
        self, valid_token_info, auth_manager
    ):
        """Should expose token_info property."""
        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            api = SpotifyAPI(valid_token_info, auth_manager)

            assert api.token_info == valid_token_info


# =============================================================================
# SpotifyAPI User Operations Tests
# =============================================================================

class TestSpotifyAPIUserOperations:
    """Tests for user-related API operations."""

    def test_get_current_user_success(
        self, valid_token_info, auth_manager, sample_user
    ):
        """Should return current user data."""
        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = sample_user

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_current_user()

            assert result == sample_user
            mock_http.get.assert_called_with("/me")


# =============================================================================
# SpotifyAPI Playlist Operations Tests
# =============================================================================

class TestSpotifyAPIPlaylistOperations:
    """Tests for playlist-related API operations."""

    def test_get_user_playlists_success(
        self, valid_token_info, auth_manager,
        sample_playlists, sample_user,
    ):
        """Should return user's editable playlists."""
        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = sample_user
            mock_http.get_all_pages.return_value = (
                sample_playlists
            )

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_user_playlists()

            assert len(result) == 2
            assert result[0]['id'] == 'playlist1'

    def test_get_user_playlists_filters_non_owned(
        self, valid_token_info, auth_manager, sample_user,
    ):
        """Should filter out playlists not owned by user."""
        playlists = [
            {
                'id': 'owned', 'name': 'Owned',
                'owner': {'id': 'user123'},
            },
            {
                'id': 'other', 'name': 'Other User',
                'owner': {'id': 'other_user'},
            },
            {
                'id': 'collab', 'name': 'Collab',
                'owner': {'id': 'other_user'},
                'collaborative': True,
            },
        ]

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = sample_user
            mock_http.get_all_pages.return_value = playlists

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_user_playlists()

            assert len(result) == 2
            ids = [p['id'] for p in result]
            assert 'owned' in ids
            assert 'collab' in ids
            assert 'other' not in ids

    def test_get_playlist_success(
        self, valid_token_info, auth_manager
    ):
        """Should return single playlist."""
        playlist = {
            'id': 'playlist123', 'name': 'Test Playlist',
        }

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = playlist

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_playlist('playlist123')

            assert result == playlist
            mock_http.get.assert_called_with(
                '/playlists/playlist123'
            )

    def test_get_playlist_tracks_success(
        self, valid_token_info, auth_manager, sample_tracks
    ):
        """Should return all playlist tracks."""
        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get_all_pages.return_value = [
                {'item': t} for t in sample_tracks
            ]

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_playlist_tracks('playlist123')

            assert len(result) == 3
            assert result[0]['uri'] == 'spotify:track:track1'

    def test_get_playlist_tracks_filters_none_tracks(
        self, valid_token_info, auth_manager, sample_tracks
    ):
        """Should filter out None tracks."""
        items = [
            {'item': sample_tracks[0]},
            {'item': None},
            {'item': sample_tracks[1]},
        ]

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get_all_pages.return_value = items

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_playlist_tracks('playlist123')

            assert len(result) == 2

    def test_update_playlist_tracks_success(
        self, valid_token_info, auth_manager
    ):
        """Should update playlist tracks."""
        track_uris = ['spotify:track:1', 'spotify:track:2']

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.return_value = None

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.update_playlist_tracks(
                'playlist123', track_uris
            )

            assert result is True
            mock_http.put.assert_called_once()

    def test_update_playlist_tracks_empty_list(
        self, valid_token_info, auth_manager
    ):
        """Should clear playlist when given empty list."""
        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.return_value = None

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.update_playlist_tracks(
                'playlist123', []
            )

            assert result is True
            mock_http.put.assert_called_with(
                '/playlists/playlist123/items',
                json={'uris': []},
            )

    def test_update_playlist_tracks_batches_large_lists(
        self, valid_token_info, auth_manager
    ):
        """Should batch large track lists (>100)."""
        track_uris = [
            f'spotify:track:{i}' for i in range(150)
        ]

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.return_value = None
            mock_http.post.return_value = None

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.update_playlist_tracks(
                'playlist123', track_uris
            )

            assert result is True
            assert mock_http.put.call_count == 1
            assert mock_http.post.call_count == 1


# =============================================================================
# SpotifyAPI Audio Features Tests
# =============================================================================

class TestSpotifyAPIAudioFeatures:
    """Tests for audio features operations."""

    def test_get_audio_features_success(
        self, valid_token_info, auth_manager
    ):
        """Should return audio features for tracks."""
        features = [
            {'id': 'track1', 'tempo': 120},
            {'id': 'track2', 'tempo': 130},
        ]

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = {
                'audio_features': features
            }

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_audio_features(
                ['track1', 'track2']
            )

            assert len(result) == 2
            assert 'track1' in result
            assert 'track2' in result

    def test_get_audio_features_handles_uris(
        self, valid_token_info, auth_manager
    ):
        """Should extract IDs from URIs."""
        features = [{'id': 'track1', 'tempo': 120}]

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = {
                'audio_features': features
            }

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_audio_features(
                ['spotify:track:track1']
            )

            assert 'track1' in result

    def test_get_audio_features_filters_none_results(
        self, valid_token_info, auth_manager
    ):
        """Should filter out None feature results."""
        features = [
            {'id': 'track1', 'tempo': 120},
            None,
        ]

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = {
                'audio_features': features
            }

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_audio_features(
                ['track1', 'track2']
            )

            assert len(result) == 1
            assert 'track1' in result

    def test_get_audio_features_batches_large_lists(
        self, valid_token_info, auth_manager
    ):
        """Should batch requests for >50 tracks."""
        track_ids = [f'track{i}' for i in range(60)]
        features_batch1 = [
            {'id': f'track{i}', 'tempo': 120}
            for i in range(50)
        ]
        features_batch2 = [
            {'id': f'track{i}', 'tempo': 120}
            for i in range(50, 60)
        ]

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.side_effect = [
                {'audio_features': features_batch1},
                {'audio_features': features_batch2},
            ]

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_audio_features(track_ids)

            assert mock_http.get.call_count == 2
            assert len(result) == 60


# =============================================================================
# Caching Integration Tests
# =============================================================================

@pytest.fixture
def mock_cache():
    """Mock SpotifyCache for testing."""
    cache = Mock(spec=SpotifyCache)
    cache.get_user.return_value = None
    cache.get_playlists.return_value = None
    cache.get_playlist.return_value = None
    cache.get_playlist_tracks.return_value = None
    cache.get_audio_features.return_value = {}
    cache.set_user.return_value = True
    cache.set_playlists.return_value = True
    cache.set_playlist.return_value = True
    cache.set_playlist_tracks.return_value = True
    cache.set_audio_features.return_value = True
    cache.invalidate_playlist.return_value = True
    cache.invalidate_user_playlists.return_value = True
    return cache


class TestSpotifyAPICaching:
    """Tests for SpotifyAPI caching integration."""

    def test_init_with_cache(
        self, valid_token_info, auth_manager, mock_cache
    ):
        """Should initialize with cache enabled."""
        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            api = SpotifyAPI(
                valid_token_info, auth_manager,
                cache=mock_cache,
            )

            assert api._cache is mock_cache
            assert api.cache is mock_cache

    def test_get_user_playlists_uses_cache(
        self, valid_token_info, auth_manager,
        mock_cache, sample_playlists,
    ):
        """Should return cached playlists when available."""
        mock_cache.get_playlists.return_value = (
            sample_playlists
        )

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = {'id': 'user123'}

            api = SpotifyAPI(
                valid_token_info, auth_manager,
                cache=mock_cache,
            )
            result = api.get_user_playlists()

            assert result == sample_playlists
            mock_http.get_all_pages.assert_not_called()

    def test_get_user_playlists_caches_result(
        self, valid_token_info, auth_manager,
        mock_cache, sample_playlists, sample_user,
    ):
        """Should cache playlists on API fetch."""
        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = sample_user
            mock_http.get_all_pages.return_value = (
                sample_playlists
            )

            api = SpotifyAPI(
                valid_token_info, auth_manager,
                cache=mock_cache,
            )
            api.get_user_playlists()

            mock_cache.set_playlists.assert_called_once()

    def test_get_user_playlists_skip_cache(
        self, valid_token_info, auth_manager,
        mock_cache, sample_playlists, sample_user,
    ):
        """Should skip cache when skip_cache=True."""
        mock_cache.get_playlists.return_value = (
            sample_playlists
        )

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = sample_user
            mock_http.get_all_pages.return_value = (
                sample_playlists
            )

            api = SpotifyAPI(
                valid_token_info, auth_manager,
                cache=mock_cache,
            )
            api.get_user_playlists(skip_cache=True)

            mock_http.get_all_pages.assert_called_once()

    def test_get_playlist_uses_cache(
        self, valid_token_info, auth_manager, mock_cache
    ):
        """Should return cached playlist when available."""
        cached_playlist = {
            'id': 'pl1', 'name': 'Cached Playlist',
        }
        mock_cache.get_playlist.return_value = cached_playlist

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value

            api = SpotifyAPI(
                valid_token_info, auth_manager,
                cache=mock_cache,
            )
            result = api.get_playlist('pl1')

            assert result == cached_playlist

    def test_get_playlist_tracks_uses_cache(
        self, valid_token_info, auth_manager,
        mock_cache, sample_tracks,
    ):
        """Should return cached tracks when available."""
        mock_cache.get_playlist_tracks.return_value = (
            sample_tracks
        )

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value

            api = SpotifyAPI(
                valid_token_info, auth_manager,
                cache=mock_cache,
            )
            result = api.get_playlist_tracks('pl1')

            assert result == sample_tracks
            mock_http.get_all_pages.assert_not_called()

    def test_update_playlist_invalidates_cache(
        self, valid_token_info, auth_manager, mock_cache
    ):
        """Should invalidate cache after updating playlist."""
        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.return_value = None

            api = SpotifyAPI(
                valid_token_info, auth_manager,
                cache=mock_cache,
            )
            api._user_id = 'user123'
            api.update_playlist_tracks(
                'pl1', ['spotify:track:1']
            )

            mock_cache.invalidate_playlist \
                .assert_called_once_with('pl1')
            mock_cache.invalidate_user_playlists \
                .assert_called_once_with('user123')

    def test_get_audio_features_uses_cache(
        self, valid_token_info, auth_manager, mock_cache
    ):
        """Should use cached audio features when available."""
        cached_features = {
            'track1': {'id': 'track1', 'tempo': 120},
            'track2': {'id': 'track2', 'tempo': 130},
        }
        mock_cache.get_audio_features.return_value = (
            cached_features
        )

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value

            api = SpotifyAPI(
                valid_token_info, auth_manager,
                cache=mock_cache,
            )
            result = api.get_audio_features(
                ['track1', 'track2']
            )

            assert result == cached_features
            mock_http.get.assert_not_called()

    def test_get_audio_features_partial_cache(
        self, valid_token_info, auth_manager, mock_cache
    ):
        """Should fetch only uncached audio features."""
        cached_features = {
            'track1': {'id': 'track1', 'tempo': 120},
        }
        mock_cache.get_audio_features.return_value = (
            cached_features
        )

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = {
                'audio_features': [
                    {'id': 'track2', 'tempo': 130}
                ]
            }

            api = SpotifyAPI(
                valid_token_info, auth_manager,
                cache=mock_cache,
            )
            result = api.get_audio_features(
                ['track1', 'track2']
            )

            mock_http.get.assert_called_once()
            assert 'track1' in result
            assert 'track2' in result

    def test_get_audio_features_caches_fetched(
        self, valid_token_info, auth_manager, mock_cache
    ):
        """Should cache newly fetched audio features."""
        mock_cache.get_audio_features.return_value = {}

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = {
                'audio_features': [
                    {'id': 'track1', 'tempo': 120},
                    {'id': 'track2', 'tempo': 130},
                ]
            }

            api = SpotifyAPI(
                valid_token_info, auth_manager,
                cache=mock_cache,
            )
            api.get_audio_features(['track1', 'track2'])

            mock_cache.set_audio_features \
                .assert_called_once()
            cached_data = (
                mock_cache.set_audio_features
                .call_args[0][0]
            )
            assert 'track1' in cached_data
            assert 'track2' in cached_data
