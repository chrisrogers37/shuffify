from flask import Blueprint, render_template, session, redirect, url_for, request, flash, jsonify
from app.spotify.client import SpotifyClient
from app.utils.shuffle_algorithms.registry import ShuffleRegistry
from app.services.track_cache import TrackCache
import logging
import traceback
from datetime import datetime

logger = logging.getLogger(__name__)
main = Blueprint('main', __name__)

@main.context_processor
def inject_current_year():
    """Make current year available to all templates."""
    return {'current_year': datetime.utcnow().year}

@main.route('/')
def index():
    """Home page route."""
    try:
        logger.debug("Session state: %s", dict(session))
        
        if 'spotify_token' not in session:
            logger.debug("No spotify_token in session, showing login page")
            # Clear any existing flash messages when showing login page
            session.pop('_flashes', None)
            return render_template('index.html')
        
        try:
            spotify = SpotifyClient(session['spotify_token'])
            playlists = spotify.get_user_playlists()
            user = spotify.get_current_user()
            algorithms = ShuffleRegistry.list_algorithms()
            return render_template('dashboard.html', playlists=playlists, user=user, algorithms=algorithms)
        except Exception as e:
            logger.error("Error with Spotify client: %s\nTraceback: %s", str(e), traceback.format_exc())
            # Clear session and flash messages
            session.clear()
            flash('Your session has expired. Please log in again.', 'error')
            return render_template('index.html')
            
    except Exception as e:
        logger.error("Error in index route: %s\nTraceback: %s", str(e), traceback.format_exc())
        session.clear()
        return render_template('index.html')

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
    try:
        logger.debug("Callback request - Args: %s", dict(request.args))
        logger.debug("Callback request - Headers: %s", dict(request.headers))
        
        error = request.args.get('error')
        code = request.args.get('code')
        
        if error:
            logger.error("Error during Spotify callback: %s", error)
            flash("Error during Spotify authentication. Please try again.", "error")
            return redirect(url_for('main.index'))
            
        if not code:
            logger.error("No code received from Spotify")
            flash("No authorization code received. Please try again.", "error")
            return redirect(url_for('main.index'))
        
        logger.debug("Received auth code from Spotify, attempting to get token")
        spotify = SpotifyClient()
        token = spotify.get_token(code)
        
        if not token:
            logger.error("Failed to get token from Spotify")
            flash("Failed to complete authentication. Please try again.", "error")
            return redirect(url_for('main.index'))
            
        logger.debug("Successfully got token, storing in session")
        session['spotify_token'] = token
        return redirect(url_for('main.index'))
        
    except Exception as e:
        logger.error("Error during callback: %s\nTraceback: %s", str(e), traceback.format_exc())
        flash("An error occurred during authentication. Please try again.", "error")
        return redirect(url_for('main.index'))

@main.route('/logout')
def logout():
    """Clear session and log out."""
    session.clear()
    return redirect(url_for('main.index'))

@main.route('/shuffle/<playlist_id>', methods=['POST'])
def shuffle(playlist_id):
    """Shuffle a playlist using the selected algorithm."""
    try:
        logger.debug("Shuffle request - Headers: %s", dict(request.headers))
        logger.debug("Session state: %s", dict(session))
        
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        
        if 'spotify_token' not in session:
            logger.warning("No spotify_token in session")
            if is_ajax:
                return jsonify({'success': False, 'message': 'Please log in first.', 'category': 'error'})
            return redirect(url_for('main.index'))
        
        # Get algorithm and parameters from form
        algorithm_name = request.form.get('algorithm', 'BasicShuffle')
        try:
            algorithm = ShuffleRegistry.get_algorithm(algorithm_name)
        except ValueError as e:
            logger.error(f"Invalid algorithm requested: {algorithm_name}")
            if is_ajax:
                return jsonify({'success': False, 'message': 'Invalid shuffle algorithm.', 'category': 'error'})
            flash('Invalid shuffle algorithm.', 'error')
            return redirect(url_for('main.index'))
        
        # Parse algorithm parameters from form
        params = {}
        for param_name, param_info in algorithm.parameters.items():
            if param_name in request.form:
                value = request.form[param_name]
                # Convert value to appropriate type
                if param_info['type'] == 'integer':
                    params[param_name] = int(value)
                elif param_info['type'] == 'float':
                    params[param_name] = float(value)
                elif param_info['type'] == 'boolean':
                    params[param_name] = value.lower() in ('true', 't', 'yes', 'y', '1')
                else:
                    params[param_name] = value
        
        spotify = SpotifyClient(session['spotify_token'])
        
        # Initialize playlist states if not exists
        if 'playlist_states' not in session:
            session['playlist_states'] = {}
            
        # Get current state before shuffling
        current_tracks = spotify.get_playlist_tracks(playlist_id)
        if not current_tracks:
            logger.error("Failed to get current tracks from playlist")
            if is_ajax:
                return jsonify({'success': False, 'message': 'Failed to get playlist tracks.', 'category': 'error'})
            flash('Failed to get playlist tracks.', 'error')
            return redirect(url_for('main.index'))
            
        logger.info(f"Current track order for playlist {playlist_id}. Track count: {len(current_tracks)}")
        
        # Initialize or reset state stack for this playlist
        if playlist_id not in session['playlist_states'] or not session['playlist_states'][playlist_id].get('states'):
            session['playlist_states'][playlist_id] = {
                'states': [current_tracks],  # First state is the original order
                'current_index': 0
            }
        
        # Perform shuffle using the selected algorithm
        try:
            shuffled_uris = algorithm.shuffle(current_tracks, sp=spotify.sp, **params)
        except Exception as e:
            logger.error(f"Error during shuffle operation: {str(e)}")
            if is_ajax:
                return jsonify({'success': False, 'message': str(e), 'category': 'error'})
            flash(str(e), 'error')
            return redirect(url_for('main.index'))
            
        if shuffled_uris:
            success = spotify.update_playlist_tracks(playlist_id, shuffled_uris)
            if success:
                # Add new state to the stack
                playlist_states = session['playlist_states'][playlist_id]
                # Remove any states after current index (in case of previous undos)
                playlist_states['states'] = playlist_states['states'][:playlist_states['current_index'] + 1]
                playlist_states['states'].append(shuffled_uris)
                playlist_states['current_index'] += 1
                session.modified = True
                logger.debug("Updated playlist states: %s", session['playlist_states'][playlist_id])
                
                message = 'Playlist successfully shuffled!'
                category = 'success'
            else:
                message = 'Failed to update playlist.'
                category = 'error'
                success = False
        else:
            message = 'Failed to shuffle playlist.'
            category = 'error'
            success = False
            
    except Exception as e:
        logger.error("Error in shuffle route: %s\nTraceback: %s", str(e), traceback.format_exc())
        message = 'An error occurred while shuffling the playlist.'
        category = 'error'
        success = False
    
    try:
        if is_ajax:
            return jsonify({
                'success': success,
                'message': message,
                'category': category
            })
        else:
            flash(message, category)
            return redirect(url_for('main.index'))
    except Exception as e:
        logger.error("Error returning response: %s\nTraceback: %s", str(e), traceback.format_exc())
        return jsonify({
            'success': False,
            'message': 'An unexpected error occurred.',
            'category': 'error'
        })

@main.route('/undo/<playlist_id>', methods=['POST'])
def undo(playlist_id):
    """Restore playlist to its previous state."""
    try:
        logger.debug("Undo request - Headers: %s", dict(request.headers))
        logger.debug("Session state before undo: %s", dict(session))
        
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        
        if 'spotify_token' not in session:
            logger.warning("No spotify_token in session")
            if is_ajax:
                return jsonify({'success': False, 'message': 'Please log in first.', 'category': 'error'})
            flash('Please log in first.', 'error')
            return redirect(url_for('main.index'))
            
        if 'playlist_states' not in session or playlist_id not in session['playlist_states']:
            logger.warning("No shuffle history for this playlist")
            if is_ajax:
                return jsonify({'success': False, 'message': 'No shuffle history for this playlist.', 'category': 'error'})
            flash('No shuffle history for this playlist.', 'error')
            return redirect(url_for('main.index'))
        
        playlist_states = session['playlist_states'][playlist_id]
        current_index = playlist_states['current_index']
        
        # Check if we can undo
        if current_index <= 0:
            logger.warning("Already at original playlist state")
            if is_ajax:
                return jsonify({'success': False, 'message': 'Already at original playlist state.', 'category': 'error'})
            flash('Already at original playlist state.', 'error')
            return redirect(url_for('main.index'))
        
        # Get previous state
        previous_state = playlist_states['states'][current_index - 1]
        
        spotify = SpotifyClient(session['spotify_token'])
        logger.info(f"Restoring playlist {playlist_id} to previous state (index {current_index - 1})")
        success = spotify.update_playlist_tracks(playlist_id, previous_state)
        
        if success:
            # Update current index
            playlist_states['current_index'] -= 1
            session.modified = True
            
            message = 'Successfully restored previous playlist order!'
            category = 'success'
            logger.info("Successfully restored playlist order")
        else:
            message = 'Failed to restore playlist.'
            category = 'error'
            logger.error("Failed to restore playlist order")
            
    except Exception as e:
        logger.error("Error undoing shuffle: %s\nTraceback: %s", str(e), traceback.format_exc())
        message = 'An error occurred while restoring the playlist.'
        category = 'error'
        success = False
    
    if is_ajax:
        response = {
            'success': success,
            'message': message,
            'category': category,
            'can_undo_more': current_index > 1 if success else False  # We can undo more if we're not at index 1 or 0
        }
        logger.debug("Sending AJAX response: %s", response)
        return jsonify(response)
    else:
        flash(message, category)
        return redirect(url_for('main.index'))

@main.route('/health')
def health_check():
    """Health check endpoint for Digital Ocean."""
    return jsonify({"status": "healthy"}), 200 