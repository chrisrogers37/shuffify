"""
Pytest configuration and shared fixtures for Shuffify tests.

This module provides common fixtures used across all test modules,
including mock Spotify clients, sample data, and Flask app contexts.
"""

import pytest
from unittest.mock import Mock, MagicMock
import time


# =============================================================================
# Sample Data Fixtures
# =============================================================================

@pytest.fixture
def sample_token():
    """A valid Spotify OAuth token."""
    return {
        'access_token': 'test_access_token_12345',
        'token_type': 'Bearer',
        'expires_in': 3600,
        'expires_at': time.time() + 3600,
        'refresh_token': 'test_refresh_token_67890',
        'scope': 'playlist-read-private playlist-modify-public'
    }


@pytest.fixture
def expired_token():
    """An expired Spotify OAuth token."""
    return {
        'access_token': 'expired_access_token',
        'token_type': 'Bearer',
        'expires_in': 3600,
        'expires_at': time.time() - 100,  # Expired
        'refresh_token': 'test_refresh_token',
    }


@pytest.fixture
def invalid_token():
    """A token missing required fields."""
    return {
        'token_type': 'Bearer',
        # Missing 'access_token'
    }


@pytest.fixture
def sample_user():
    """Sample Spotify user data."""
    return {
        'id': 'user123',
        'display_name': 'Test User',
        'email': 'test@example.com',
        'images': [{'url': 'https://example.com/avatar.jpg'}],
        'country': 'US',
        'product': 'premium',
        'uri': 'spotify:user:user123',
    }


@pytest.fixture
def sample_playlist_data():
    """Sample Spotify playlist data."""
    return {
        'id': 'playlist123',
        'name': 'My Test Playlist',
        'description': 'A playlist for testing',
        'owner': {'id': 'user123'},
        'tracks': {'total': 10},
        'images': [{'url': 'https://example.com/cover.jpg'}]
    }


@pytest.fixture
def sample_playlists():
    """List of sample playlists."""
    return [
        {
            'id': 'playlist1',
            'name': 'Playlist One',
            'owner': {'id': 'user123'},
            'tracks': {'total': 25},
            'collaborative': False
        },
        {
            'id': 'playlist2',
            'name': 'Playlist Two',
            'owner': {'id': 'user123'},
            'tracks': {'total': 50},
            'collaborative': False
        },
        {
            'id': 'playlist3',
            'name': 'Collab Playlist',
            'owner': {'id': 'other_user'},
            'tracks': {'total': 15},
            'collaborative': True
        }
    ]


@pytest.fixture
def sample_tracks():
    """Sample track data for a playlist."""
    return [
        {
            'id': f'track{i}',
            'name': f'Track {i}',
            'uri': f'spotify:track:track{i}',
            'duration_ms': 180000 + (i * 1000),
            'is_local': False,
            'artists': [{'name': f'Artist {i}', 'external_urls': {'spotify': f'https://open.spotify.com/artist/artist{i}'}}],
            'album': {
                'name': f'Album {i}',
                'images': [{'url': f'https://example.com/album{i}.jpg'}]
            },
            'external_urls': {'spotify': f'https://open.spotify.com/track/track{i}'}
        }
        for i in range(1, 11)
    ]


@pytest.fixture
def sample_track_uris(sample_tracks):
    """List of track URIs from sample tracks."""
    return [track['uri'] for track in sample_tracks]


@pytest.fixture
def sample_audio_features():
    """Sample audio features for tracks."""
    return {
        f'track{i}': {
            'tempo': 120.0 + i,
            'energy': 0.5 + (i * 0.05),
            'valence': 0.6 + (i * 0.03),
            'danceability': 0.7 + (i * 0.02),
            'acousticness': 0.3,
            'instrumentalness': 0.1
        }
        for i in range(1, 11)
    }


# =============================================================================
# Mock Fixtures
# =============================================================================

@pytest.fixture
def mock_spotify_client(sample_user, sample_playlists, sample_playlist_data, sample_tracks, sample_audio_features):
    """A mock SpotifyClient with pre-configured responses."""
    mock = Mock()

    # Configure method responses
    mock.get_current_user.return_value = sample_user
    mock.get_user_playlists.return_value = sample_playlists
    mock.get_playlist.return_value = sample_playlist_data
    mock.get_playlist_tracks.return_value = sample_tracks
    mock.get_track_audio_features.return_value = sample_audio_features
    mock.update_playlist_tracks.return_value = True
    mock.get_auth_url.return_value = 'https://accounts.spotify.com/authorize?...'
    mock.get_token.return_value = {
        'access_token': 'new_token',
        'token_type': 'Bearer',
        'expires_at': time.time() + 3600,
        'refresh_token': 'new_refresh'
    }

    return mock


@pytest.fixture
def mock_session():
    """A mock Flask session (dict-like with modified flag)."""
    class MockSession(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.modified = False

    return MockSession()


@pytest.fixture
def session_with_token(mock_session, sample_token):
    """A mock session pre-populated with a valid token."""
    mock_session['spotify_token'] = sample_token
    return mock_session


@pytest.fixture
def session_with_state(mock_session, sample_token, sample_track_uris):
    """A mock session with token and playlist state history."""
    mock_session['spotify_token'] = sample_token
    mock_session['playlist_states'] = {
        'playlist123': {
            'states': [
                sample_track_uris,  # Original state
                sample_track_uris[::-1],  # Reversed (after shuffle)
            ],
            'current_index': 1
        }
    }
    return mock_session


# =============================================================================
# Flask App Fixtures
# =============================================================================

@pytest.fixture
def app():
    """Create a Flask application for testing."""
    import os
    os.environ['SPOTIFY_CLIENT_ID'] = 'test_client_id'
    os.environ['SPOTIFY_CLIENT_SECRET'] = 'test_client_secret'
    os.environ['SPOTIFY_REDIRECT_URI'] = 'http://localhost:5000/callback'
    os.environ['SECRET_KEY'] = 'test-secret-key-for-testing'

    from shuffify import create_app
    app = create_app('development')
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SCHEDULER_ENABLED'] = False

    with app.app_context():
        from shuffify.models.db import db
        db.create_all()

    return app


@pytest.fixture
def app_context(app):
    """Provide Flask application context."""
    with app.app_context():
        yield app


@pytest.fixture
def client(app):
    """Provide Flask test client."""
    return app.test_client()


@pytest.fixture
def authenticated_client(app):
    """Flask test client with a valid session token pre-set."""
    with app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_access_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "test_refresh_token",
            }
        yield test_client


# =============================================================================
# Algorithm Fixtures
# =============================================================================

@pytest.fixture
def basic_algorithm():
    """An instance of BasicShuffle algorithm."""
    from shuffify.shuffle_algorithms.basic import BasicShuffle
    return BasicShuffle()


@pytest.fixture
def all_algorithms():
    """Dictionary of all available algorithms."""
    from shuffify.shuffle_algorithms.registry import ShuffleRegistry
    return ShuffleRegistry.get_available_algorithms()
