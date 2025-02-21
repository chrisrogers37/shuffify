from flask import Blueprint, render_template, session, redirect, url_for, request, flash, jsonify
from app.spotify.client import SpotifyClient
from app.utils.shuffify import shuffle_playlist
import logging

logger = logging.getLogger(__name__)
main = Blueprint('main', __name__)

@main.route('/')
def index():
    """Home page route."""
    if 'spotify_token' not in session:
        return render_template('index.html')
    
    try:
        spotify = SpotifyClient(session['spotify_token'])
        playlists = spotify.get_user_playlists()
        user = spotify.get_current_user()
        return render_template('dashboard.html', playlists=playlists, user=user)
    except Exception as e:
        logger.error(f"Error loading dashboard: {str(e)}")
        session.pop('spotify_token', None)
        flash('Your session has expired. Please log in again.', 'error')
        return redirect(url_for('main.index'))

@main.route('/login')
def login():
    """Handle login with Spotify OAuth."""
    try:
        spotify = SpotifyClient()
        auth_url = spotify.get_auth_url()
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Error during login: {str(e)}")
        flash("An error occurred during login. Please try again.", "error")
        return redirect(url_for('main.index'))

@main.route('/callback')
def callback():
    """Handle Spotify OAuth callback."""
    error = request.args.get('error')
    code = request.args.get('code')
    
    if error:
        logger.error(f"Error during Spotify callback: {error}")
        flash("Error during Spotify authentication. Please try again.", "error")
        return redirect(url_for('main.index'))
        
    if not code:
        logger.error("No code received from Spotify")
        flash("No authorization code received. Please try again.", "error")
        return redirect(url_for('main.index'))
    
    try:
        spotify = SpotifyClient()
        token = spotify.get_token(code)
        session['spotify_token'] = token
        return redirect(url_for('main.index'))
    except Exception as e:
        logger.error(f"Error during callback: {str(e)}")
        flash("An error occurred during authentication. Please try again.", "error")
        return redirect(url_for('main.index'))

@main.route('/logout')
def logout():
    """Clear session and log out."""
    session.clear()
    return redirect(url_for('main.index'))

@main.route('/shuffle/<playlist_id>', methods=['POST'])
def shuffle(playlist_id):
    """Shuffle a playlist."""
    if 'spotify_token' not in session:
        return redirect(url_for('main.index'))
    
    try:
        keep_first = int(request.form.get('keep_first', 0))
        spotify = SpotifyClient(session['spotify_token'])
        
        # Store original state for undo
        original_tracks = spotify.get_playlist_tracks(playlist_id)
        session['last_shuffle'] = {
            'playlist_id': playlist_id,
            'original_tracks': original_tracks
        }
        
        # Perform shuffle
        shuffled_uris = shuffle_playlist(spotify.sp, playlist_id, keep_first)
        if shuffled_uris:
            success = spotify.update_playlist_tracks(playlist_id, shuffled_uris)
            if success:
                flash('Playlist successfully shuffled!', 'success')
            else:
                flash('Failed to update playlist.', 'error')
        else:
            flash('Failed to shuffle playlist.', 'error')
            
    except Exception as e:
        logger.error(f"Error shuffling playlist: {str(e)}")
        flash('An error occurred while shuffling the playlist.', 'error')
    
    return redirect(url_for('main.index'))

@main.route('/undo/<playlist_id>', methods=['POST'])
def undo(playlist_id):
    """Restore playlist to its original state."""
    if 'spotify_token' not in session:
        flash('Please log in first.', 'error')
        return redirect(url_for('main.index'))
        
    if 'last_shuffle' not in session:
        flash('No recent shuffle to undo.', 'error')
        return redirect(url_for('main.index'))
    
    try:
        last_shuffle = session['last_shuffle']
        if last_shuffle['playlist_id'] != playlist_id:
            flash('Cannot undo: playlist mismatch.', 'error')
            return redirect(url_for('main.index'))
        
        spotify = SpotifyClient(session['spotify_token'])
        success = spotify.update_playlist_tracks(playlist_id, last_shuffle['original_tracks'])
        
        if success:
            # Clear the last_shuffle from session after successful undo
            session.pop('last_shuffle', None)
            flash('Successfully restored original playlist order!', 'success')
        else:
            flash('Failed to restore playlist.', 'error')
            
    except Exception as e:
        logger.error(f"Error undoing shuffle: {str(e)}")
        flash('An error occurred while restoring the playlist.', 'error')
    
    return redirect(url_for('main.index'))

@main.route('/health')
def health_check():
    """Health check endpoint for Digital Ocean."""
    return jsonify({"status": "healthy"}), 200 