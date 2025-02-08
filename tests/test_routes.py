import pytest
from flask import url_for
from unittest.mock import patch, Mock

@pytest.fixture
def app():
    from app import create_app
    app = create_app('testing')
    return app

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def mock_spotify():
    with patch('app.spotify.client.SpotifyClient') as mock:
        mock.return_value.get_user_playlists.return_value = [
            {
                'name': 'Test Playlist',
                'id': 'test_id',
                'tracks': {'total': 10},
                'images': [{'url': 'https://example.com/image.jpg'}],
                'owner': {'id': 'test_user'},
            }
        ]
        mock.return_value.get_current_user.return_value = {
            'id': 'test_user',
            'display_name': 'Test User',
            'images': [{'url': 'https://example.com/profile.jpg'}]
        }
        yield mock

def test_index_page_no_auth(client):
    """Test index page without authentication."""
    response = client.get('/')
    assert response.status_code == 200
    assert b'Connect with Spotify' in response.data
    assert b'Welcome to Shuffify' in response.data

def test_index_page_with_auth(client, mock_spotify):
    """Test index page with authentication."""
    with client.session_transaction() as session:
        session['spotify_token'] = {'access_token': 'test_token'}
    
    response = client.get('/')
    assert response.status_code == 200
    assert b'Test Playlist' in response.data
    assert b'Test User' in response.data

def test_login_redirect(client):
    """Test login route redirects to Spotify."""
    with patch('app.spotify.client.SpotifyClient') as mock:
        mock.return_value.get_auth_url.return_value = 'https://spotify.com/auth'
        response = client.get('/login')
        assert response.status_code == 302
        assert 'spotify.com/auth' in response.location

def test_callback_success(client):
    """Test successful Spotify callback."""
    with patch('app.spotify.client.SpotifyClient') as mock:
        mock.return_value.get_token.return_value = {'access_token': 'test_token'}
        response = client.get('/callback?code=test_code')
        assert response.status_code == 302
        assert '/' == response.location

def test_shuffle_playlist(client, mock_spotify):
    """Test playlist shuffling."""
    with client.session_transaction() as session:
        session['spotify_token'] = {'access_token': 'test_token'}
    
    with patch('app.utils.shuffify.shuffle_playlist') as mock_shuffle:
        mock_shuffle.return_value = ['spotify:track:1', 'spotify:track:2']
        
        response = client.post('/shuffle/test_playlist_id', data={'keep_first': '2'})
        assert response.status_code == 302
        assert '/' == response.location
        
        # Verify shuffle was called with correct parameters
        mock_shuffle.assert_called_once()
        args = mock_shuffle.call_args[1]
        assert args['playlist_id'] == 'test_playlist_id'
        assert args['keep_first'] == 2

def test_undo_shuffle(client, mock_spotify):
    """Test undoing a shuffle."""
    with client.session_transaction() as session:
        session['spotify_token'] = {'access_token': 'test_token'}
        session['last_shuffle'] = {
            'playlist_id': 'test_playlist_id',
            'original_tracks': ['spotify:track:1', 'spotify:track:2']
        }
    
    response = client.post('/undo/test_playlist_id')
    assert response.status_code == 302
    assert '/' == response.location 