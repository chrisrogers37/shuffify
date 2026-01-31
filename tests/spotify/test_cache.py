"""Tests for Spotify API caching functionality."""

import json
import pytest
from unittest.mock import Mock, MagicMock, patch
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
        assert cache._audio_features_ttl == 86400

    def test_init_with_custom_settings(self):
        """Test initialization with custom settings."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(
            mock_redis,
            key_prefix='custom:',
            default_ttl=100,
            playlist_ttl=30,
            user_ttl=300,
            audio_features_ttl=3600
        )

        assert cache._prefix == 'custom:'
        assert cache._default_ttl == 100
        assert cache._playlist_ttl == 30
        assert cache._user_ttl == 300
        assert cache._audio_features_ttl == 3600


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

        key = cache._make_key('audio', 'track1', 'v2')
        assert key == 'shuffify:cache:audio:track1:v2'


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
        result = cache.get_playlist('pl1')

        assert result == playlist
        mock_redis.get.assert_called_once_with('shuffify:cache:playlist:pl1')

    def test_set_playlist_single(self):
        """Test setting single playlist cache."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        playlist = {'id': 'pl1', 'name': 'Test Playlist'}
        result = cache.set_playlist('pl1', playlist)

        assert result is True


class TestSpotifyCacheTracksOperations:
    """Test playlist tracks cache operations."""

    def test_get_playlist_tracks_cache_hit(self):
        """Test getting cached playlist tracks."""
        mock_redis = Mock(spec=redis.Redis)
        tracks = [{'id': 't1', 'name': 'Track 1'}, {'id': 't2', 'name': 'Track 2'}]
        mock_redis.get.return_value = json.dumps(tracks).encode('utf-8')

        cache = SpotifyCache(mock_redis)
        result = cache.get_playlist_tracks('pl1')

        assert result == tracks
        mock_redis.get.assert_called_once_with('shuffify:cache:tracks:pl1')

    def test_set_playlist_tracks_success(self):
        """Test setting playlist tracks cache."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        tracks = [{'id': 't1'}, {'id': 't2'}]
        result = cache.set_playlist_tracks('pl1', tracks)

        assert result is True


class TestSpotifyCacheAudioFeaturesOperations:
    """Test audio features cache operations."""

    def test_get_audio_features_all_cached(self):
        """Test getting all cached audio features."""
        mock_redis = Mock(spec=redis.Redis)
        features = [
            json.dumps({'id': 't1', 'tempo': 120}).encode('utf-8'),
            json.dumps({'id': 't2', 'tempo': 130}).encode('utf-8'),
        ]
        mock_redis.mget.return_value = features

        cache = SpotifyCache(mock_redis)
        result = cache.get_audio_features(['t1', 't2'])

        assert result == {
            't1': {'id': 't1', 'tempo': 120},
            't2': {'id': 't2', 'tempo': 130},
        }

    def test_get_audio_features_partial_cache(self):
        """Test getting partially cached audio features."""
        mock_redis = Mock(spec=redis.Redis)
        features = [
            json.dumps({'id': 't1', 'tempo': 120}).encode('utf-8'),
            None,  # t2 not cached
        ]
        mock_redis.mget.return_value = features

        cache = SpotifyCache(mock_redis)
        result = cache.get_audio_features(['t1', 't2'])

        assert result == {'t1': {'id': 't1', 'tempo': 120}}
        assert 't2' not in result

    def test_get_audio_features_empty_list(self):
        """Test getting audio features with empty list."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        result = cache.get_audio_features([])
        assert result == {}
        mock_redis.mget.assert_not_called()

    def test_set_audio_features_success(self):
        """Test setting audio features cache."""
        mock_redis = Mock(spec=redis.Redis)
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        cache = SpotifyCache(mock_redis)
        features = {
            't1': {'id': 't1', 'tempo': 120},
            't2': {'id': 't2', 'tempo': 130},
        }
        result = cache.set_audio_features(features)

        assert result is True
        assert mock_pipe.setex.call_count == 2
        mock_pipe.execute.assert_called_once()

    def test_set_audio_features_empty(self):
        """Test setting empty audio features."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        result = cache.set_audio_features({})
        assert result is True
        mock_redis.pipeline.assert_not_called()


class TestSpotifyCacheInvalidation:
    """Test cache invalidation operations."""

    def test_invalidate_playlist(self):
        """Test invalidating playlist cache."""
        mock_redis = Mock(spec=redis.Redis)
        cache = SpotifyCache(mock_redis)

        result = cache.invalidate_playlist('pl1')

        assert result is True
        mock_redis.delete.assert_called_once()
        call_args = mock_redis.delete.call_args[0]
        assert 'shuffify:cache:playlist:pl1' in call_args
        assert 'shuffify:cache:tracks:pl1' in call_args

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


class TestSpotifyCacheClearAll:
    """Test clear all cache operation."""

    def test_clear_all_success(self):
        """Test clearing all cache entries."""
        mock_redis = Mock(spec=redis.Redis)
        # Simulate scan returning keys then stopping
        mock_redis.scan.side_effect = [
            (123, [b'shuffify:cache:user:1', b'shuffify:cache:playlist:1']),
            (0, [b'shuffify:cache:tracks:1']),  # cursor=0 means done
        ]

        cache = SpotifyCache(mock_redis)
        result = cache.clear_all()

        assert result is True
        assert mock_redis.delete.call_count == 2

    def test_clear_all_empty(self):
        """Test clearing empty cache."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.scan.return_value = (0, [])

        cache = SpotifyCache(mock_redis)
        result = cache.clear_all()

        assert result is True
        mock_redis.delete.assert_not_called()

    def test_clear_all_redis_error(self):
        """Test handling Redis error on clear."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.scan.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        result = cache.clear_all()

        assert result is False


class TestSpotifyCacheConnection:
    """Test cache connection checks."""

    def test_is_connected_true(self):
        """Test is_connected returns True when connected."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.ping.return_value = True

        cache = SpotifyCache(mock_redis)
        assert cache.is_connected() is True

    def test_is_connected_false(self):
        """Test is_connected returns False when disconnected."""
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.ping.side_effect = redis.RedisError("Connection failed")

        cache = SpotifyCache(mock_redis)
        assert cache.is_connected() is False
