import pytest
from unittest.mock import Mock, patch
from app.spotify.client import SpotifyClient

def test_spotify_client_initialization():
    with patch('app.spotify.client.SpotifyOAuth') as mock_oauth:
        mock_oauth.return_value = Mock()
        client = SpotifyClient()
        assert client.scope is not None
        assert "playlist-read-private" in client.scope
        assert "playlist-modify-public" in client.scope

def test_get_user_playlists():
    with patch('app.spotify.client.SpotifyOAuth') as mock_oauth:
        mock_oauth.return_value = Mock()
        client = SpotifyClient()
        client.sp = Mock()
        
        # Mock the current user
        client.sp.current_user.return_value = {'id': 'test_user'}
        
        # Mock playlist response
        mock_playlists = {
            'items': [
                {'owner': {'id': 'test_user'}, 'name': 'Playlist 1'},
                {'owner': {'id': 'other_user'}, 'collaborative': True, 'name': 'Playlist 2'},
                {'owner': {'id': 'other_user'}, 'collaborative': False, 'name': 'Playlist 3'}
            ],
            'next': None
        }
        client.sp.current_user_playlists.return_value = mock_playlists
        
        playlists = client.get_user_playlists()
        assert len(playlists) == 2  # Should only include owned and collaborative playlists
        assert playlists[0]['name'] == 'Playlist 1'
        assert playlists[1]['name'] == 'Playlist 2'

def test_get_playlist_tracks():
    with patch('app.spotify.client.SpotifyOAuth') as mock_oauth:
        mock_oauth.return_value = Mock()
        client = SpotifyClient()
        client.sp = Mock()
        
        # Mock playlist items response
        mock_tracks = {
            'items': [
                {'track': {'uri': 'spotify:track:1'}},
                {'track': {'uri': 'spotify:track:2'}},
            ],
            'next': None
        }
        client.sp.playlist_items.return_value = mock_tracks
        
        tracks = client.get_playlist_tracks('test_playlist_id')
        assert len(tracks) == 2
        assert tracks[0] == 'spotify:track:1'
        assert tracks[1] == 'spotify:track:2'

def test_update_playlist_tracks():
    with patch('app.spotify.client.SpotifyOAuth') as mock_oauth:
        mock_oauth.return_value = Mock()
        client = SpotifyClient()
        client.sp = Mock()
        
        track_uris = [f'spotify:track:{i}' for i in range(150)]  # Test batch processing
        result = client.update_playlist_tracks('test_playlist_id', track_uris)
        
        assert result is True
        client.sp.playlist_replace_items.assert_called_once_with('test_playlist_id', [])
        assert client.sp.playlist_add_items.call_count == 2  # Should be called twice for 150 tracks