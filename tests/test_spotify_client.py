import pytest
from unittest.mock import Mock, patch
from src.api.spotify_client import SpotifyClient

@pytest.fixture
def mock_spotify_client():
    with patch('src.api.spotify_client.SpotifyAuthenticator') as mock_auth:
        mock_auth.return_value.get_spotify_client.return_value = Mock()
        client = SpotifyClient()
        yield client

def test_get_user_playlists(mock_spotify_client):
    mock_response = {
        'items': [{'name': 'Test Playlist', 'id': '1', 'tracks': {'total': 10}}],
        'next': None
    }
    mock_spotify_client.client.current_user_playlists.return_value = mock_response
    
    playlists = mock_spotify_client.get_user_playlists()
    assert len(playlists) == 1
    assert playlists[0]['name'] == 'Test Playlist' 