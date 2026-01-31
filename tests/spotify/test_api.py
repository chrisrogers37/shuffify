"""
Tests for SpotifyAPI.

Tests cover all data operations: user, playlists, tracks, and audio features.
Also includes tests for caching integration.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

import spotipy

from requests.exceptions import ConnectionError, Timeout

from shuffify.spotify.api import SpotifyAPI, api_error_handler, _calculate_backoff_delay, MAX_RETRIES
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
def mock_spotipy():
    """Mock spotipy.Spotify instance."""
    mock = Mock(spec=spotipy.Spotify)
    return mock


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
        {'id': 'playlist1', 'name': 'Playlist 1', 'owner': {'id': 'user123'}},
        {'id': 'playlist2', 'name': 'Playlist 2', 'owner': {'id': 'user123'}},
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

    def test_converts_404_to_not_found_error(self):
        """Should convert 404 SpotifyException to SpotifyNotFoundError."""
        @api_error_handler
        def not_found_func():
            error = spotipy.SpotifyException(404, -1, 'Not found')
            raise error

        with pytest.raises(SpotifyNotFoundError):
            not_found_func()

    def test_converts_429_to_rate_limit_error(self):
        """Should convert 429 SpotifyException to SpotifyRateLimitError."""
        @api_error_handler
        def rate_limit_func():
            error = spotipy.SpotifyException(429, -1, 'Rate limited')
            error.headers = {'Retry-After': '30'}
            raise error

        with pytest.raises(SpotifyRateLimitError) as exc_info:
            rate_limit_func()
        assert exc_info.value.retry_after == 30

    def test_converts_401_to_token_expired_error(self):
        """Should convert 401 SpotifyException to SpotifyTokenExpiredError."""
        @api_error_handler
        def unauthorized_func():
            raise spotipy.SpotifyException(401, -1, 'Unauthorized')

        with pytest.raises(SpotifyTokenExpiredError):
            unauthorized_func()

    def test_converts_other_errors_to_api_error(self):
        """Should convert other SpotifyExceptions to SpotifyAPIError."""
        @api_error_handler
        def other_error_func():
            raise spotipy.SpotifyException(500, -1, 'Server error')

        with pytest.raises(SpotifyAPIError):
            other_error_func()

    def test_wraps_unexpected_exceptions(self):
        """Should wrap unexpected exceptions in SpotifyAPIError."""
        @api_error_handler
        def unexpected_func():
            raise ValueError('Unexpected error')

        with pytest.raises(SpotifyAPIError):
            unexpected_func()


# =============================================================================
# SpotifyAPI Initialization Tests
# =============================================================================

class TestSpotifyAPIInit:
    """Tests for SpotifyAPI initialization."""

    def test_init_with_valid_token(self, valid_token_info, auth_manager):
        """Should initialize with valid token."""
        with patch('shuffify.spotify.api.spotipy.Spotify'):
            api = SpotifyAPI(valid_token_info, auth_manager)

            assert api._token_info == valid_token_info
            assert api._auth_manager == auth_manager

    def test_init_with_expired_token_auto_refresh(self, expired_token_info, auth_manager):
        """Should auto-refresh expired token when enabled."""
        new_token = TokenInfo(
            access_token='new_token',
            token_type='Bearer',
            expires_at=time.time() + 3600,
            refresh_token='new_refresh'
        )

        with patch.object(auth_manager, 'ensure_valid_token', return_value=new_token):
            with patch('shuffify.spotify.api.spotipy.Spotify'):
                api = SpotifyAPI(expired_token_info, auth_manager, auto_refresh=True)

                assert api._token_info == new_token

    def test_init_with_expired_token_no_auto_refresh(self, expired_token_info, auth_manager):
        """Should raise error for expired token when auto_refresh disabled."""
        with pytest.raises(SpotifyTokenExpiredError):
            SpotifyAPI(expired_token_info, auth_manager, auto_refresh=False)

    def test_token_info_property(self, valid_token_info, auth_manager):
        """Should expose token_info property."""
        with patch('shuffify.spotify.api.spotipy.Spotify'):
            api = SpotifyAPI(valid_token_info, auth_manager)

            assert api.token_info == valid_token_info


# =============================================================================
# SpotifyAPI User Operations Tests
# =============================================================================

class TestSpotifyAPIUserOperations:
    """Tests for user-related API operations."""

    def test_get_current_user_success(self, valid_token_info, auth_manager, sample_user):
        """Should return current user data."""
        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.current_user.return_value = sample_user
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_current_user()

            assert result == sample_user
            mock_sp.current_user.assert_called_once()


# =============================================================================
# SpotifyAPI Playlist Operations Tests
# =============================================================================

class TestSpotifyAPIPlaylistOperations:
    """Tests for playlist-related API operations."""

    def test_get_user_playlists_success(self, valid_token_info, auth_manager, sample_playlists, sample_user):
        """Should return user's editable playlists."""
        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.current_user.return_value = sample_user
            mock_sp.current_user_playlists.return_value = {
                'items': sample_playlists,
                'next': None
            }
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_user_playlists()

            assert len(result) == 2
            assert result[0]['id'] == 'playlist1'

    def test_get_user_playlists_filters_non_owned(self, valid_token_info, auth_manager, sample_user):
        """Should filter out playlists not owned by user (unless collaborative)."""
        playlists = [
            {'id': 'owned', 'name': 'Owned', 'owner': {'id': 'user123'}},
            {'id': 'other', 'name': 'Other User', 'owner': {'id': 'other_user'}},
            {'id': 'collab', 'name': 'Collab', 'owner': {'id': 'other_user'}, 'collaborative': True},
        ]

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.current_user.return_value = sample_user
            mock_sp.current_user_playlists.return_value = {
                'items': playlists,
                'next': None
            }
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_user_playlists()

            assert len(result) == 2  # owned and collab only
            ids = [p['id'] for p in result]
            assert 'owned' in ids
            assert 'collab' in ids
            assert 'other' not in ids

    def test_get_playlist_success(self, valid_token_info, auth_manager):
        """Should return single playlist."""
        playlist = {'id': 'playlist123', 'name': 'Test Playlist'}

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.playlist.return_value = playlist
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_playlist('playlist123')

            assert result == playlist
            mock_sp.playlist.assert_called_with('playlist123')

    def test_get_playlist_tracks_success(self, valid_token_info, auth_manager, sample_tracks):
        """Should return all playlist tracks."""
        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.playlist_items.return_value = {
                'items': [{'track': t} for t in sample_tracks],
                'next': None
            }
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_playlist_tracks('playlist123')

            assert len(result) == 3
            assert result[0]['uri'] == 'spotify:track:track1'

    def test_get_playlist_tracks_filters_none_tracks(self, valid_token_info, auth_manager, sample_tracks):
        """Should filter out None tracks."""
        items = [
            {'track': sample_tracks[0]},
            {'track': None},  # None track (deleted or local)
            {'track': sample_tracks[1]},
        ]

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.playlist_items.return_value = {
                'items': items,
                'next': None
            }
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_playlist_tracks('playlist123')

            assert len(result) == 2

    def test_update_playlist_tracks_success(self, valid_token_info, auth_manager):
        """Should update playlist tracks."""
        track_uris = ['spotify:track:1', 'spotify:track:2']

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.update_playlist_tracks('playlist123', track_uris)

            assert result is True
            mock_sp.playlist_replace_items.assert_called_once()

    def test_update_playlist_tracks_empty_list(self, valid_token_info, auth_manager):
        """Should clear playlist when given empty list."""
        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.update_playlist_tracks('playlist123', [])

            assert result is True
            mock_sp.playlist_replace_items.assert_called_with('playlist123', [])

    def test_update_playlist_tracks_batches_large_lists(self, valid_token_info, auth_manager):
        """Should batch large track lists (>100)."""
        track_uris = [f'spotify:track:{i}' for i in range(150)]

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.update_playlist_tracks('playlist123', track_uris)

            assert result is True
            # Should call replace_items once and add_items once
            assert mock_sp.playlist_replace_items.call_count == 1
            assert mock_sp.playlist_add_items.call_count == 1


# =============================================================================
# SpotifyAPI Audio Features Tests
# =============================================================================

class TestSpotifyAPIAudioFeatures:
    """Tests for audio features operations."""

    def test_get_audio_features_success(self, valid_token_info, auth_manager):
        """Should return audio features for tracks."""
        features = [
            {'id': 'track1', 'tempo': 120},
            {'id': 'track2', 'tempo': 130},
        ]

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.audio_features.return_value = features
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_audio_features(['track1', 'track2'])

            assert len(result) == 2
            assert 'track1' in result
            assert 'track2' in result

    def test_get_audio_features_handles_uris(self, valid_token_info, auth_manager):
        """Should extract IDs from URIs."""
        features = [{'id': 'track1', 'tempo': 120}]

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.audio_features.return_value = features
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_audio_features(['spotify:track:track1'])

            assert 'track1' in result

    def test_get_audio_features_filters_none_results(self, valid_token_info, auth_manager):
        """Should filter out None feature results."""
        features = [
            {'id': 'track1', 'tempo': 120},
            None,  # Some tracks may not have features
        ]

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.audio_features.return_value = features
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_audio_features(['track1', 'track2'])

            assert len(result) == 1
            assert 'track1' in result

    def test_get_audio_features_batches_large_lists(self, valid_token_info, auth_manager):
        """Should batch requests for >50 tracks."""
        track_ids = [f'track{i}' for i in range(60)]
        features = [{'id': f'track{i}', 'tempo': 120} for i in range(60)]

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.audio_features.side_effect = [features[:50], features[50:]]
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager)
            result = api.get_audio_features(track_ids)

            assert mock_sp.audio_features.call_count == 2
            assert len(result) == 60


# =============================================================================
# Retry Logic Tests
# =============================================================================

class TestBackoffCalculation:
    """Tests for backoff delay calculation."""

    def test_first_attempt_returns_base_delay(self):
        """First retry should use base delay."""
        delay = _calculate_backoff_delay(0, base_delay=2)
        assert delay == 2

    def test_exponential_increase(self):
        """Delay should increase exponentially."""
        delay0 = _calculate_backoff_delay(0, base_delay=2)
        delay1 = _calculate_backoff_delay(1, base_delay=2)
        delay2 = _calculate_backoff_delay(2, base_delay=2)

        assert delay0 == 2
        assert delay1 == 4
        assert delay2 == 8

    def test_caps_at_max_delay(self):
        """Delay should cap at MAX_DELAY (16s)."""
        delay = _calculate_backoff_delay(10, base_delay=2)
        assert delay == 16


class TestApiErrorHandlerRetry:
    """Tests for retry logic in api_error_handler."""

    def test_retries_on_rate_limit(self):
        """Should retry on 429 rate limit errors."""
        call_count = 0

        @api_error_handler
        def rate_limited_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                error = spotipy.SpotifyException(429, -1, 'Rate limited')
                error.headers = {'Retry-After': '0'}  # Use 0 for test speed
                raise error
            return 'success'

        with patch('shuffify.spotify.api.time.sleep'):  # Don't actually sleep
            result = rate_limited_func()

        assert result == 'success'
        assert call_count == 3  # Failed twice, succeeded on third

    def test_retries_on_server_errors(self):
        """Should retry on 5xx server errors."""
        call_count = 0

        @api_error_handler
        def server_error_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise spotipy.SpotifyException(503, -1, 'Service unavailable')
            return 'success'

        with patch('shuffify.spotify.api.time.sleep'):
            result = server_error_func()

        assert result == 'success'
        assert call_count == 2

    def test_retries_on_connection_error(self):
        """Should retry on network connection errors."""
        call_count = 0

        @api_error_handler
        def connection_error_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError('Connection refused')
            return 'success'

        with patch('shuffify.spotify.api.time.sleep'):
            result = connection_error_func()

        assert result == 'success'
        assert call_count == 2

    def test_retries_on_timeout(self):
        """Should retry on timeout errors."""
        call_count = 0

        @api_error_handler
        def timeout_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Timeout('Request timed out')
            return 'success'

        with patch('shuffify.spotify.api.time.sleep'):
            result = timeout_func()

        assert result == 'success'
        assert call_count == 2

    def test_no_retry_on_404(self):
        """Should not retry on 404 errors."""
        call_count = 0

        @api_error_handler
        def not_found_func():
            nonlocal call_count
            call_count += 1
            raise spotipy.SpotifyException(404, -1, 'Not found')

        with pytest.raises(SpotifyNotFoundError):
            not_found_func()

        assert call_count == 1  # No retry

    def test_no_retry_on_401(self):
        """Should not retry on 401 errors."""
        call_count = 0

        @api_error_handler
        def unauthorized_func():
            nonlocal call_count
            call_count += 1
            raise spotipy.SpotifyException(401, -1, 'Unauthorized')

        with pytest.raises(SpotifyTokenExpiredError):
            unauthorized_func()

        assert call_count == 1  # No retry

    def test_max_retries_exceeded_rate_limit(self):
        """Should raise after max retries on rate limit."""
        @api_error_handler
        def always_rate_limited():
            error = spotipy.SpotifyException(429, -1, 'Rate limited')
            error.headers = {'Retry-After': '0'}
            raise error

        with patch('shuffify.spotify.api.time.sleep'):
            with pytest.raises(SpotifyRateLimitError):
                always_rate_limited()

    def test_max_retries_exceeded_server_error(self):
        """Should raise after max retries on server errors."""
        @api_error_handler
        def always_503():
            raise spotipy.SpotifyException(503, -1, 'Service unavailable')

        with patch('shuffify.spotify.api.time.sleep'):
            with pytest.raises(SpotifyAPIError):
                always_503()

    def test_max_retries_exceeded_network_error(self):
        """Should raise after max retries on network errors."""
        @api_error_handler
        def always_fails():
            raise ConnectionError('Connection refused')

        with patch('shuffify.spotify.api.time.sleep'):
            with pytest.raises(SpotifyAPIError):
                always_fails()


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

    def test_init_with_cache(self, valid_token_info, auth_manager, mock_cache):
        """Should initialize with cache enabled."""
        with patch('shuffify.spotify.api.spotipy.Spotify'):
            api = SpotifyAPI(valid_token_info, auth_manager, cache=mock_cache)

            assert api._cache is mock_cache
            assert api.cache is mock_cache

    def test_get_user_playlists_uses_cache(self, valid_token_info, auth_manager, mock_cache, sample_playlists):
        """Should return cached playlists when available."""
        mock_cache.get_playlists.return_value = sample_playlists

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.current_user.return_value = {'id': 'user123'}
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager, cache=mock_cache)
            result = api.get_user_playlists()

            assert result == sample_playlists
            mock_sp.current_user_playlists.assert_not_called()  # Cache hit, no API call

    def test_get_user_playlists_caches_result(self, valid_token_info, auth_manager, mock_cache, sample_playlists, sample_user):
        """Should cache playlists on API fetch."""
        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.current_user.return_value = sample_user
            mock_sp.current_user_playlists.return_value = {
                'items': sample_playlists,
                'next': None
            }
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager, cache=mock_cache)
            api.get_user_playlists()

            mock_cache.set_playlists.assert_called_once()

    def test_get_user_playlists_skip_cache(self, valid_token_info, auth_manager, mock_cache, sample_playlists, sample_user):
        """Should skip cache when skip_cache=True."""
        mock_cache.get_playlists.return_value = sample_playlists  # Cache has data

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.current_user.return_value = sample_user
            mock_sp.current_user_playlists.return_value = {
                'items': sample_playlists,
                'next': None
            }
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager, cache=mock_cache)
            api.get_user_playlists(skip_cache=True)

            # Should call API even though cache has data
            mock_sp.current_user_playlists.assert_called_once()

    def test_get_playlist_uses_cache(self, valid_token_info, auth_manager, mock_cache):
        """Should return cached playlist when available."""
        cached_playlist = {'id': 'pl1', 'name': 'Cached Playlist'}
        mock_cache.get_playlist.return_value = cached_playlist

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager, cache=mock_cache)
            result = api.get_playlist('pl1')

            assert result == cached_playlist
            mock_sp.playlist.assert_not_called()

    def test_get_playlist_tracks_uses_cache(self, valid_token_info, auth_manager, mock_cache, sample_tracks):
        """Should return cached tracks when available."""
        mock_cache.get_playlist_tracks.return_value = sample_tracks

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager, cache=mock_cache)
            result = api.get_playlist_tracks('pl1')

            assert result == sample_tracks
            mock_sp.playlist_items.assert_not_called()

    def test_update_playlist_invalidates_cache(self, valid_token_info, auth_manager, mock_cache):
        """Should invalidate cache after updating playlist."""
        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.current_user.return_value = {'id': 'user123'}
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager, cache=mock_cache)
            # Prime the user_id
            api._user_id = 'user123'
            api.update_playlist_tracks('pl1', ['spotify:track:1'])

            mock_cache.invalidate_playlist.assert_called_once_with('pl1')
            mock_cache.invalidate_user_playlists.assert_called_once_with('user123')

    def test_get_audio_features_uses_cache(self, valid_token_info, auth_manager, mock_cache):
        """Should use cached audio features when available."""
        cached_features = {
            'track1': {'id': 'track1', 'tempo': 120},
            'track2': {'id': 'track2', 'tempo': 130},
        }
        mock_cache.get_audio_features.return_value = cached_features

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager, cache=mock_cache)
            result = api.get_audio_features(['track1', 'track2'])

            assert result == cached_features
            mock_sp.audio_features.assert_not_called()

    def test_get_audio_features_partial_cache(self, valid_token_info, auth_manager, mock_cache):
        """Should fetch only uncached audio features."""
        # track1 is cached, track2 is not
        cached_features = {'track1': {'id': 'track1', 'tempo': 120}}
        mock_cache.get_audio_features.return_value = cached_features

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.audio_features.return_value = [{'id': 'track2', 'tempo': 130}]
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager, cache=mock_cache)
            result = api.get_audio_features(['track1', 'track2'])

            # Should only fetch track2
            mock_sp.audio_features.assert_called_once_with(['track2'])
            assert 'track1' in result
            assert 'track2' in result

    def test_get_audio_features_caches_fetched(self, valid_token_info, auth_manager, mock_cache):
        """Should cache newly fetched audio features."""
        mock_cache.get_audio_features.return_value = {}  # Nothing cached

        with patch('shuffify.spotify.api.spotipy.Spotify') as mock_spotify_class:
            mock_sp = Mock()
            mock_sp.audio_features.return_value = [
                {'id': 'track1', 'tempo': 120},
                {'id': 'track2', 'tempo': 130},
            ]
            mock_spotify_class.return_value = mock_sp

            api = SpotifyAPI(valid_token_info, auth_manager, cache=mock_cache)
            api.get_audio_features(['track1', 'track2'])

            mock_cache.set_audio_features.assert_called_once()
            cached_data = mock_cache.set_audio_features.call_args[0][0]
            assert 'track1' in cached_data
            assert 'track2' in cached_data
