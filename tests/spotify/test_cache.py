"""Tests for Spotify API caching functionality."""

import hashlib
import json
from unittest.mock import Mock

import pytest
import redis

from shuffify.spotify.cache import SpotifyCache


class TestSpotifyCacheInit:
    """Test SpotifyCache initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default settings."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        assert cache._redis is mock_redis
        assert cache._prefix == 'shuffify:cache:'
        assert cache._default_ttl == 300
        assert cache._playlist_ttl == 60
        assert cache._user_ttl == 600

    def test_init_with_custom_settings(self):
        """Test initialization with custom settings."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(
            mock_redis,
            key_prefix='custom:',
            default_ttl=100,
            playlist_ttl=30,
            user_ttl=300
        )

        assert cache._prefix == 'custom:'
        assert cache._default_ttl == 100
        assert cache._playlist_ttl == 30
        assert cache._user_ttl == 300


class TestSpotifyCacheKeyGeneration:
    """Test cache key generation."""

    def test_make_key_single_part(self):
        """Test key generation with single part."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        key = cache._make_key('user', 'user123')
        assert key == 'shuffify:cache:user:user123'

    def test_make_key_multiple_parts(self):
        """Test key generation with multiple parts."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        key = cache._make_key('tracks', 'track1', 'v2')
        assert key == 'shuffify:cache:tracks:track1:v2'


class TestSpotifyCacheSerialization:
    """Test serialization and deserialization."""

    def test_serialize_dict(self):
        """Test serializing a dictionary."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        data = {'id': 'test', 'name': 'Test'}
        result = cache._serialize(data)

        assert isinstance(result, bytes)
        assert json.loads(result.decode('utf-8')) == data

    def test_deserialize_bytes(self):
        """Test deserializing bytes."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        data = {'id': 'test', 'name': 'Test'}
        serialized = json.dumps(data).encode('utf-8')
        result = cache._deserialize(serialized)

        assert result == data

    def test_deserialize_none(self):
        """Test deserializing None returns None."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        result = cache._deserialize(None)
        assert result is None


class TestSpotifyCacheUserOperations:
    """Test user cache operations."""

    def test_get_user_cache_hit(self):
        """Test getting cached user data."""
        mock_redis = Mock(spec=redis.Redis)
        user_data = {'id': 'user123', 'display_name': 'Test User'}
        mock_redis.get.return_value = json.dumps(user_data).encode('utf-8')

        cache = SpotifyCache(mock_redis)
        result = cache.get_user('user123')

        assert result == user_data
        mock_redis.get.assert_called_once_with('shuffify:cache:user:user123')

    def test_get_user_cache_miss(self):
        """Test cache miss for user data."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.get.return_value = None

        cache = SpotifyCache(mock_redis)
        result = cache.get_user('user123')

        assert result is None

    def test_get_user_redis_error(self):
        """Test handling Redis error on get."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.get.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.get_user('user123')

        assert result is None

    def test_set_user_success(self):
        """Test setting user cache."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        user_data = {'id': 'user123', 'display_name': 'Test User'}
        result = cache.set_user('user123', user_data)

        assert result is True
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == 'shuffify:cache:user:user123'
        assert call_args[0][1] == 600  # user_ttl

    def test_set_user_custom_ttl(self):
        """Test setting user cache with custom TTL."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        user_data = {'id': 'user123'}
        cache.set_user('user123', user_data, ttl=120)

        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 120

    def test_set_user_redis_error(self):
        """Test handling Redis error on set."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.setex.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.set_user('user123', {'id': 'user123'})

        assert result is False


class TestSpotifyCachePlaylistOperations:
    """Test playlist cache operations."""

    def test_get_playlists_cache_hit(self):
        """Test getting cached playlists."""
        mock_redis = Mock(spec=redis.Redis)
        playlists = [{'id': 'pl1'}, {'id': 'pl2'}]
        mock_redis.get.return_value = json.dumps(playlists).encode('utf-8')

        cache = SpotifyCache(mock_redis)
        result = cache.get_playlists('user123')

        assert result == playlists
        mock_redis.get.assert_called_once_with('shuffify:cache:playlists:user123')

    def test_get_playlists_cache_miss(self):
        """Test cache miss for playlists."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.get.return_value = None

        cache = SpotifyCache(mock_redis)
        result = cache.get_playlists('user123')

        assert result is None

    def test_set_playlists_success(self):
        """Test setting playlists cache."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        playlists = [{'id': 'pl1'}, {'id': 'pl2'}]
        result = cache.set_playlists('user123', playlists)

        assert result is True
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 60  # playlist_ttl

    def test_get_playlist_single(self):
        """Test getting single cached playlist."""
        mock_redis = Mock(spec=redis.Redis)
        playlist = {'id': 'pl1', 'name': 'Test Playlist'}
        mock_redis.get.return_value = json.dumps(playlist).encode('utf-8')

        cache = SpotifyCache(mock_redis)
        result = cache.get_playlist('pl1', user_id='user123')

        assert result == playlist
        mock_redis.get.assert_called_once_with(
            'shuffify:cache:playlist_account_v2:'
            f'{hashlib.sha256(b"pl1").hexdigest()}:'
            f'{hashlib.sha256(b"user123").hexdigest()}'
        )

    def test_set_playlist_single(self):
        """Test setting single playlist cache."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        playlist = {'id': 'pl1', 'name': 'Test Playlist'}
        result = cache.set_playlist('pl1', playlist, user_id='user123')

        assert result is True


class TestSpotifyCacheTracksOperations:
    """Test playlist tracks cache operations."""

    def test_get_playlist_tracks_cache_hit(self):
        """Test getting cached playlist tracks."""
        mock_redis = Mock(spec=redis.Redis)
        tracks = [{'id': 't1', 'name': 'Track 1'}, {'id': 't2', 'name': 'Track 2'}]
        mock_redis.get.return_value = json.dumps(tracks).encode('utf-8')

        cache = SpotifyCache(mock_redis)
        result = cache.get_playlist_tracks('pl1', user_id='user123')

        assert result == tracks
        mock_redis.get.assert_called_once_with(
            'shuffify:cache:tracks_account_v2:'
            f'{hashlib.sha256(b"pl1").hexdigest()}:'
            f'{hashlib.sha256(b"user123").hexdigest()}'
        )

    def test_set_playlist_tracks_success(self):
        """Test setting playlist tracks cache."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        tracks = [{'id': 't1'}, {'id': 't2'}]
        result = cache.set_playlist_tracks('pl1', tracks, user_id='user123')

        assert result is True


class TestSpotifyCacheInvalidation:
    """Test cache invalidation operations."""

    def test_invalidate_playlist(self):
        """Test invalidating playlist cache."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.scan.return_value = (0, [])
        cache = SpotifyCache(mock_redis)

        result = cache.invalidate_playlist('pl1')

        assert result is True
        mock_redis.delete.assert_called_once()
        call_args = mock_redis.delete.call_args[0]
        assert 'shuffify:cache:playlist:pl1' in call_args
        assert 'shuffify:cache:tracks:pl1' in call_args

    def test_invalidate_playlist_scans_every_page_and_bounds_deletes(self):
        """Redis COUNT is a hint; even a larger result must use bounded deletes."""
        mock_redis = Mock(spec=redis.Redis)
        scoped_keys = [f'scoped:{i}'.encode() for i in range(205)]
        mock_redis.scan.side_effect = [
            (7, scoped_keys), (0, [b'last-metadata']), (0, [b'track-copy']),
        ]
        cache = SpotifyCache(mock_redis)

        assert cache.invalidate_playlist('pl1') is True

        assert [call.args[0] for call in mock_redis.scan.call_args_list] == [0, 7, 0]
        assert all(call.kwargs['count'] == 100 for call in mock_redis.scan.call_args_list)
        assert all(len(call.args) <= 100 for call in mock_redis.delete.call_args_list)
        deleted = [key for call in mock_redis.delete.call_args_list[1:]
                   for key in call.args]
        assert deleted == scoped_keys + [b'last-metadata', b'track-copy']

    def test_invalidate_playlist_escapes_configured_prefix(self):
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.scan.return_value = (0, [])
        cache = SpotifyCache(mock_redis, key_prefix='cache[*]?\\:')

        assert cache.invalidate_playlist('playlist[*]') is True

        for call in mock_redis.scan.call_args_list:
            assert call.kwargs['match'].startswith('cache\\[\\*\\]\\?\\\\:')
            assert call.kwargs['match'].endswith(':*')

    def test_invalidate_playlist_scan_error_returns_false(self):
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.scan.side_effect = redis.RedisError('Unavailable')

        assert SpotifyCache(mock_redis).invalidate_playlist('pl1') is False

    @pytest.mark.parametrize('user_id', [None, '', 123])
    def test_playlist_cache_rejects_missing_or_invalid_identity(self, user_id):
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        with pytest.raises(ValueError, match='Spotify account ID'):
            cache.get_playlist('pl1', user_id=user_id)

        mock_redis.get.assert_not_called()

    def test_invalidate_user_playlists(self):
        """Test invalidating user playlists cache."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        result = cache.invalidate_user_playlists('user123')

        assert result is True
        mock_redis.delete.assert_called_once_with('shuffify:cache:playlists:user123')

    def test_invalidate_redis_error(self):
        """Test handling Redis error on invalidation."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.delete.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.invalidate_playlist('pl1')

        assert result is False


# =============================================================================
# Additional Edge Case Tests for Full Coverage
# =============================================================================

class TestSpotifyCachePlaylistEdgeCases:
    """Edge case tests for playlist operations."""

    def test_get_playlists_redis_error(self):
        """Test handling Redis error on get playlists."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.get.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.get_playlists('user123')

        assert result is None

    def test_set_playlists_redis_error(self):
        """Test handling Redis error on set playlists."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.setex.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.set_playlists('user123', [{'id': 'pl1'}])

        assert result is False

    def test_get_playlist_redis_error(self):
        """Test handling Redis error on get single playlist."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.get.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.get_playlist('pl1', user_id='user123')

        assert result is None

    def test_set_playlist_redis_error(self):
        """Test handling Redis error on set single playlist."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.setex.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.set_playlist('pl1', {'id': 'pl1'}, user_id='user123')

        assert result is False

    def test_set_playlists_custom_ttl(self):
        """Test setting playlists with custom TTL."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        cache.set_playlists('user123', [{'id': 'pl1'}], ttl=120)

        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 120

    def test_set_playlist_custom_ttl(self):
        """Test setting single playlist with custom TTL."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        cache.set_playlist('pl1', {'id': 'pl1'}, ttl=90, user_id='user123')

        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 90


class TestSpotifyCacheTracksEdgeCases:
    """Edge case tests for playlist tracks operations."""

    def test_get_playlist_tracks_redis_error(self):
        """Test handling Redis error on get tracks."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.get.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.get_playlist_tracks('pl1', user_id='user123')

        assert result is None

    def test_set_playlist_tracks_redis_error(self):
        """Test handling Redis error on set tracks."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.setex.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.set_playlist_tracks('pl1', [{'id': 't1'}], user_id='user123')

        assert result is False

    def test_get_playlist_tracks_cache_miss(self):
        """Test cache miss for playlist tracks."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.get.return_value = None

        cache = SpotifyCache(mock_redis)
        result = cache.get_playlist_tracks('pl1', user_id='user123')

        assert result is None

    def test_set_playlist_tracks_custom_ttl(self):
        """Test setting playlist tracks with custom TTL."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        cache.set_playlist_tracks('pl1', [{'id': 't1'}], ttl=45, user_id='user123')

        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == 45


class TestSpotifyCacheInvalidationEdgeCases:
    """Edge case tests for cache invalidation."""

    def test_invalidate_user_playlists_redis_error(self):
        """Test handling Redis error on invalidate user playlists."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.delete.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.invalidate_user_playlists('user123')

        assert result is False
