"""
Shared fixtures for Shuffify test suite.

Provides sample tracks, playlists, and audio features for testing
shuffle algorithms and playlist operations.
"""

import pytest
from typing import List, Dict, Any
from unittest.mock import Mock, MagicMock


@pytest.fixture
def sample_tracks() -> List[Dict[str, Any]]:
    """Generate a list of 20 sample tracks for testing."""
    return [
        {
            'id': f'track_{i}',
            'name': f'Track {i}',
            'uri': f'spotify:track:track_{i}',
            'duration_ms': 180000 + (i * 1000),
            'is_local': False,
            'artists': [{'name': f'Artist {i % 5}', 'external_urls': {'spotify': f'https://open.spotify.com/artist/artist_{i % 5}'}}],
            'album': {
                'name': f'Album {i % 3}',
                'images': [{'url': f'https://example.com/album_{i % 3}.jpg'}]
            },
            'external_urls': {'spotify': f'https://open.spotify.com/track/track_{i}'}
        }
        for i in range(20)
    ]


@pytest.fixture
def small_tracks() -> List[Dict[str, Any]]:
    """Generate a small list of 5 tracks for edge case testing."""
    return [
        {
            'id': f'small_track_{i}',
            'name': f'Small Track {i}',
            'uri': f'spotify:track:small_{i}',
            'duration_ms': 200000,
            'is_local': False,
            'artists': [{'name': 'Test Artist'}],
            'album': {'name': 'Test Album', 'images': [{'url': 'https://example.com/album.jpg'}]},
            'external_urls': {'spotify': f'https://open.spotify.com/track/small_{i}'}
        }
        for i in range(5)
    ]


@pytest.fixture
def single_track() -> List[Dict[str, Any]]:
    """A single track for edge case testing."""
    return [
        {
            'id': 'single_track',
            'name': 'Single Track',
            'uri': 'spotify:track:single',
            'duration_ms': 180000,
            'is_local': False,
            'artists': [{'name': 'Solo Artist'}],
            'album': {'name': 'Solo Album', 'images': [{'url': 'https://example.com/solo.jpg'}]},
            'external_urls': {'spotify': 'https://open.spotify.com/track/single'}
        }
    ]


@pytest.fixture
def empty_tracks() -> List[Dict[str, Any]]:
    """Empty track list for edge case testing."""
    return []


@pytest.fixture
def tracks_with_missing_uri() -> List[Dict[str, Any]]:
    """Tracks where some are missing URIs (should be filtered out)."""
    return [
        {'id': 'track_1', 'name': 'Valid Track 1', 'uri': 'spotify:track:valid_1'},
        {'id': 'track_2', 'name': 'Missing URI Track'},  # No URI
        {'id': 'track_3', 'name': 'Valid Track 2', 'uri': 'spotify:track:valid_2'},
        {'id': 'track_4', 'name': 'Empty URI Track', 'uri': ''},  # Empty URI
        {'id': 'track_5', 'name': 'Valid Track 3', 'uri': 'spotify:track:valid_3'},
    ]


@pytest.fixture
def sample_audio_features() -> Dict[str, Dict[str, Any]]:
    """Sample audio features keyed by track ID."""
    features = {}
    for i in range(20):
        features[f'track_{i}'] = {
            'tempo': 100 + (i * 5),
            'energy': 0.3 + (i * 0.03),
            'valence': 0.2 + (i * 0.04),
            'danceability': 0.4 + (i * 0.025),
            'acousticness': 0.1 + (i * 0.02),
            'instrumentalness': 0.0 + (i * 0.01),
            'liveness': 0.1 + (i * 0.01),
            'speechiness': 0.05 + (i * 0.005)
        }
    return features


@pytest.fixture
def sample_playlist_data() -> Dict[str, Any]:
    """Sample Spotify playlist API response data."""
    return {
        'id': 'playlist_123',
        'name': 'Test Playlist',
        'owner': {'id': 'user_456'},
        'description': 'A test playlist for unit testing',
        'tracks': {'total': 20}
    }


@pytest.fixture
def mock_spotify_client(sample_playlist_data, sample_tracks, sample_audio_features):
    """Create a mock SpotifyClient for testing Playlist.from_spotify()."""
    mock_client = Mock()
    mock_client.get_playlist.return_value = sample_playlist_data
    mock_client.get_playlist_tracks.return_value = sample_tracks
    mock_client.get_track_audio_features.return_value = sample_audio_features
    return mock_client


def get_uris_from_tracks(tracks: List[Dict[str, Any]]) -> List[str]:
    """Helper to extract URIs from track list."""
    return [t['uri'] for t in tracks if t.get('uri')]


@pytest.fixture
def track_uris(sample_tracks) -> List[str]:
    """URIs extracted from sample_tracks fixture."""
    return get_uris_from_tracks(sample_tracks)
