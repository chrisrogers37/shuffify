"""
Tests for SpotifyClient facade.

Tests cover client initialization, token handling, and caching integration.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from shuffify.spotify.client import SpotifyClient
from shuffify.spotify.cache import SpotifyCache
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.auth import TokenInfo


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def credentials_dict():
    """Valid credentials dictionary."""
    return {
        'client_id': 'test_client_id',
        'client_secret': 'test_client_secret',
        'redirect_uri': 'http://localhost:5000/callback'
    }


@pytest.fixture
def valid_token():
    """Valid token dictionary."""
    return {
        'access_token': 'test_access_token',
        'token_type': 'Bearer',
        'expires_at': time.time() + 3600,
        'expires_in': 3600,
        'refresh_token': 'test_refresh_token'
    }


@pytest.fixture
def expired_token():
    """Expired token dictionary."""
    return {
        'access_token': 'expired_token',
        'token_type': 'Bearer',
        'expires_at': time.time() - 100,
        'expires_in': 0,
        'refresh_token': 'test_refresh_token'
    }


@pytest.fixture
def mock_cache():
    """Mock SpotifyCache."""
    return Mock(spec=SpotifyCache)


@pytest.fixture
def sample_user():
    """Sample user data."""
    return {
        'id': 'user123',
        'display_name': 'Test User'
    }


@pytest.fixture
def sample_playlists():
    """Sample playlists."""
    return [
        {
            'id': 'pl1', 'name': 'Playlist 1',
            'owner': {'id': 'user123'},
        },
        {
            'id': 'pl2', 'name': 'Playlist 2',
            'owner': {'id': 'user123'},
        },
    ]


# =============================================================================
# Initialization Tests
# =============================================================================

class TestSpotifyClientInit:
    """Tests for SpotifyClient initialization."""

    def test_init_with_valid_token(
        self, credentials_dict, valid_token
    ):
        """Should initialize with valid token."""
        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
            )

            assert client.is_authenticated
            assert client.token_info is not None

    def test_init_without_token(self, credentials_dict):
        """Should initialize without token (for OAuth)."""
        client = SpotifyClient(
            credentials=credentials_dict
        )

        assert not client.is_authenticated
        assert client.token_info is None

    def test_init_with_cache(
        self, credentials_dict, valid_token, mock_cache
    ):
        """Should initialize with cache."""
        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
                cache=mock_cache,
            )

            assert client._cache is mock_cache

    def test_init_with_expired_token_raises(
        self, credentials_dict, expired_token
    ):
        """Should raise ValueError for expired token."""
        with pytest.raises(
            ValueError, match="Invalid or expired token"
        ):
            SpotifyClient(
                token=expired_token,
                credentials=credentials_dict,
            )


class TestSpotifyClientCacheIntegration:
    """Tests for SpotifyClient cache integration."""

    def test_cache_passed_to_api(
        self, credentials_dict, valid_token,
        mock_cache, sample_user,
    ):
        """Should pass cache to SpotifyAPI."""
        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = sample_user

            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
                cache=mock_cache,
            )

            assert client._api._cache is mock_cache

    def test_get_token_creates_api_with_cache(
        self, credentials_dict, valid_token, mock_cache
    ):
        """Should create API with cache after exchange."""
        client = SpotifyClient(
            credentials=credentials_dict,
            cache=mock_cache,
        )

        with patch.object(
            client._auth_manager, 'exchange_code'
        ) as mock_exchange:
            mock_token_info = TokenInfo(
                access_token='new_token',
                token_type='Bearer',
                expires_at=time.time() + 3600,
                refresh_token='new_refresh',
            )
            mock_exchange.return_value = mock_token_info

            with patch(
                'shuffify.spotify.api.SpotifyHTTPClient'
            ):
                client.get_token('auth_code')

                assert client._api._cache is mock_cache


class TestSpotifyClientTokenInfo:
    """Tests for token_info property."""

    def test_token_info_returns_current_token(
        self, credentials_dict, valid_token
    ):
        """Should return current token info."""
        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
            )

            token_info = client.token_info
            assert (
                token_info.access_token
                == valid_token['access_token']
            )

    def test_token_info_returns_none_not_authenticated(
        self, credentials_dict
    ):
        """Should return None when not authenticated."""
        client = SpotifyClient(
            credentials=credentials_dict
        )
        assert client.token_info is None


class TestSpotifyClientAuthMethods:
    """Tests for authentication methods."""

    def test_get_auth_url(self, credentials_dict):
        """Should return auth URL."""
        client = SpotifyClient(
            credentials=credentials_dict
        )

        url = client.get_auth_url()

        assert url.startswith(
            'https://accounts.spotify.com/authorize'
        )

    def test_get_token_success(self, credentials_dict):
        """Should exchange code for token."""
        client = SpotifyClient(
            credentials=credentials_dict
        )

        with patch.object(
            client._auth_manager, 'exchange_code'
        ) as mock_exchange:
            mock_token_info = TokenInfo(
                access_token='new_token',
                token_type='Bearer',
                expires_at=time.time() + 3600,
                refresh_token='new_refresh',
            )
            mock_exchange.return_value = mock_token_info

            with patch(
                'shuffify.spotify.api.SpotifyHTTPClient'
            ):
                token = client.get_token('auth_code')

                assert token['access_token'] == 'new_token'
                assert client.is_authenticated


class TestSpotifyClientUserMethods:
    """Tests for user-related methods."""

    def test_get_current_user(
        self, credentials_dict, valid_token, sample_user
    ):
        """Should return current user."""
        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = sample_user

            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
            )
            user = client.get_current_user()

            assert user['id'] == 'user123'

    def test_get_current_user_not_authenticated(
        self, credentials_dict
    ):
        """Should raise when not authenticated."""
        client = SpotifyClient(
            credentials=credentials_dict
        )

        with pytest.raises(
            RuntimeError, match="not initialized"
        ):
            client.get_current_user()


class TestSpotifyClientPlaylistMethods:
    """Tests for playlist methods."""

    def test_get_user_playlists(
        self, credentials_dict, valid_token,
        sample_playlists, sample_user,
    ):
        """Should return user playlists."""
        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = sample_user
            mock_http.get_all_pages.return_value = (
                sample_playlists
            )

            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
            )
            playlists = client.get_user_playlists()

            assert len(playlists) == 2

    def test_get_playlist(
        self, credentials_dict, valid_token
    ):
        """Should return single playlist."""
        playlist_data = {
            'id': 'pl1', 'name': 'Test Playlist',
        }

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get.return_value = playlist_data

            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
            )
            playlist = client.get_playlist('pl1')

            assert playlist['id'] == 'pl1'

    def test_get_playlist_tracks(
        self, credentials_dict, valid_token
    ):
        """Should return playlist tracks."""
        tracks = [
            {'uri': 'spotify:track:1', 'name': 'Track 1'},
            {'uri': 'spotify:track:2', 'name': 'Track 2'},
        ]

        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.get_all_pages.return_value = [
                {'item': t} for t in tracks
            ]

            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
            )
            result = client.get_playlist_tracks('pl1')

            assert len(result) == 2

    def test_update_playlist_tracks(
        self, credentials_dict, valid_token
    ):
        """Should update playlist tracks."""
        with patch(
            'shuffify.spotify.api.SpotifyHTTPClient'
        ) as MockHTTP:
            mock_http = MockHTTP.return_value
            mock_http.put.return_value = None

            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
            )
            result = client.update_playlist_tracks(
                'pl1', ['spotify:track:1']
            )

            assert result is True


class TestSpotifyClientAudioFeatures:
    """Tests for audio features methods."""

    def test_get_track_audio_features(
        self, credentials_dict, valid_token
    ):
        """Should return audio features."""
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

            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
            )
            result = client.get_track_audio_features(
                ['track1', 'track2']
            )

            assert 'track1' in result
            assert 'track2' in result


class TestSpotifyClientCredentialsResolution:
    """Tests for credentials resolution."""

    def test_resolve_credentials_from_dict(
        self, valid_token
    ):
        """Should resolve credentials from dictionary."""
        creds = {
            'client_id': 'dict_client_id',
            'client_secret': 'dict_secret',
            'redirect_uri': 'http://localhost/callback',
        }

        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            client = SpotifyClient(
                token=valid_token, credentials=creds
            )

            assert (
                client._credentials.client_id
                == 'dict_client_id'
            )

    def test_resolve_credentials_from_flask_config(
        self, valid_token
    ):
        """Should resolve from Flask config when needed."""
        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            with patch(
                'shuffify.spotify.client'
                '.SpotifyCredentials.from_flask_config'
            ) as mock_from_config:
                mock_creds = SpotifyCredentials(
                    client_id='flask_id',
                    client_secret='flask_secret',
                    redirect_uri=(
                        'http://localhost/callback'
                    ),
                )
                mock_from_config.return_value = mock_creds

                from flask import Flask
                app = Flask(__name__)
                app.config['SPOTIFY_CLIENT_ID'] = 'flask_id'
                app.config['SPOTIFY_CLIENT_SECRET'] = (
                    'flask_secret'
                )
                app.config['SPOTIFY_REDIRECT_URI'] = (
                    'http://localhost/callback'
                )

                with app.app_context():
                    client = SpotifyClient(
                        token=valid_token
                    )
                    assert (
                        client._credentials.client_id
                        == 'flask_id'
                    )

    def test_resolve_credentials_from_env(
        self, valid_token
    ):
        """Should resolve from env outside Flask."""
        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            with patch(
                'shuffify.spotify.client'
                '.SpotifyCredentials.from_env'
            ) as mock_from_env:
                mock_creds = SpotifyCredentials(
                    client_id='env_id',
                    client_secret='env_secret',
                    redirect_uri=(
                        'http://localhost/callback'
                    ),
                )
                mock_from_env.return_value = mock_creds

                with patch(
                    'shuffify.spotify.client'
                    '.SpotifyCredentials.from_flask_config'
                ) as mock_flask:
                    mock_flask.side_effect = RuntimeError(
                        "No Flask context"
                    )

                    client = SpotifyClient(
                        token=valid_token
                    )
                    assert (
                        client._credentials.client_id
                        == 'env_id'
                    )


class TestSpotifyClientEnsureAuthenticated:
    """Tests for _ensure_authenticated method."""

    def test_raises_when_not_authenticated(
        self, credentials_dict
    ):
        """Should raise RuntimeError."""
        client = SpotifyClient(
            credentials=credentials_dict
        )

        with pytest.raises(
            RuntimeError, match="not initialized"
        ):
            client._ensure_authenticated()

    def test_passes_when_authenticated(
        self, credentials_dict, valid_token
    ):
        """Should not raise when authenticated."""
        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
            )

            client._ensure_authenticated()


class TestSpotifyClientApiProperty:
    """Tests for the api property."""

    def test_api_returns_api_when_authenticated(
        self, credentials_dict, valid_token
    ):
        """Should return SpotifyAPI instance."""
        with patch('shuffify.spotify.api.SpotifyHTTPClient'):
            client = SpotifyClient(
                token=valid_token,
                credentials=credentials_dict,
            )

            assert client.api is not None
            assert client.api is client._api

    def test_api_returns_none_when_not_authenticated(
        self, credentials_dict
    ):
        """Should return None when not authenticated."""
        client = SpotifyClient(
            credentials=credentials_dict
        )

        assert client.api is None
