from flask import Blueprint, render_template, session, redirect, url_for, request, flash, jsonify, send_from_directory
from shuffify.spotify.client import SpotifyClient
from shuffify.models.playlist import Playlist
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
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
        logger.debug("Index route accessed - Session keys: %s", list(session.keys()) if session else 'No session')
        
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
            logger.debug("User %s successfully loaded dashboard", user.get('display_name', 'Unknown'))
            return render_template('dashboard.html', playlists=playlists, user=user, algorithms=algorithms)
        except Exception as e:
            logger.error("Error with Spotify client: %s", str(e), exc_info=True)
            # Clear session and flash messages
            session.clear()
            flash('Your session has expired. Please log in again.', 'error')
            return render_template('index.html')
            
    except Exception as e:
        logger.error("Error in index route: %s", str(e), exc_info=True)
        session.clear()
        return render_template('index.html')

@main.route('/terms')
def terms():
    """Terms of Service page."""
    return send_from_directory('static/public', 'terms.html')

@main.route('/privacy')
def privacy():
    """Privacy Policy page."""
    return send_from_directory('static/public', 'privacy.html')

@main.route('/health')
def health():
    """Health check endpoint for Docker and monitoring."""
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()}), 200

@main.route('/login')
def login():
    """Handle login with Spotify OAuth."""
    try:
        # Check for legal consent
        legal_consent = request.args.get('legal_consent')
        if not legal_consent:
            flash('You must agree to the Terms of Service and Privacy Policy to use Shuffify.', 'error')
            return redirect(url_for('main.index'))
        
        # Clear any existing session data to start fresh
        session.pop('spotify_token', None)
        session.pop('user_data', None)
        session.modified = True
        
        spotify = SpotifyClient()
        auth_url = spotify.get_auth_url()
        logger.debug("Generated auth URL: %s", auth_url)
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Error during login: {str(e)}", exc_info=True)
        flash("An error occurred during login. Please try again.", "error")
        return redirect(url_for('main.index'))



@main.route('/callback')
def callback():
    """Handle the OAuth callback from Spotify"""
    logger.debug("Callback request - Args: %s", request.args)
    logger.debug("Callback request - Headers: %s", request.headers)
    
    # Check for OAuth errors first (common with Facebook login)
    error = request.args.get('error')
    if error:
        logger.error("OAuth error received: %s", error)
        error_description = request.args.get('error_description', 'Unknown error')
        flash(f'OAuth Error: {error_description}', 'error')
        return redirect(url_for('main.index'))
    
    # Get the authorization code from the callback
    code = request.args.get('code')
    if not code:
        logger.error("No code received in callback")
        flash('No authorization code received from Spotify. Please try again.', 'error')
        return redirect(url_for('main.index'))
    
    logger.debug("Received auth code from Spotify, attempting to get token")
    
    # Initialize client without token to get auth URL
    client = SpotifyClient()
    
    try:
        # Exchange the code for an access token
        token_data = client.get_token(code)
        logger.debug("Successfully got token, storing in session")
        
        # Validate token data structure
        if not isinstance(token_data, dict) or 'access_token' not in token_data:
            logger.error("Invalid token data received: %s", type(token_data))
            flash('Invalid token data received from Spotify. Please try again.', 'error')
            return redirect(url_for('main.index'))
        
        # Store the full token data in the session
        session['spotify_token'] = token_data
        
        # Initialize client with full token data
        client = SpotifyClient(token=token_data)
        
        # Test the token by getting user data
        try:
            user_data = client.get_current_user()
            logger.debug("Successfully retrieved user data: %s", user_data.get('display_name', 'Unknown'))
        except Exception as user_error:
            logger.error("Failed to get user data with token: %s", str(user_error))
            # Clear the invalid token from session
            session.pop('spotify_token', None)
            flash('Failed to authenticate with Spotify. Please try again.', 'error')
            return redirect(url_for('main.index'))
        
        # Get playlists
        try:
            playlists = client.get_user_playlists()
            logger.debug("Successfully retrieved %d playlists", len(playlists))
        except Exception as playlist_error:
            logger.error("Failed to get playlists: %s", str(playlist_error))
            # Don't fail the login if we can't get playlists, just log it
            playlists = []
        
        # Store user data in session
        session['user_data'] = user_data
        
        # Ensure session is saved
        session.modified = True
        
        logger.info("User %s successfully authenticated", user_data.get('display_name', 'Unknown'))
        
        # Redirect to dashboard with playlists
        return redirect(url_for('main.index'))
        
    except Exception as e:
        logger.error("Error with Spotify client: %s", str(e), exc_info=True)
        # Clear any partial session data
        session.pop('spotify_token', None)
        session.pop('user_data', None)
        flash('Error connecting to Spotify. Please try again.', 'error')
        return redirect(url_for('main.index'))

@main.route('/logout')
def logout():
    """Clear session and log out."""
    session.clear()
    return redirect(url_for('main.index'))

@main.route('/playlist/<playlist_id>')
def get_playlist(playlist_id):
    """Get playlist data with optional features."""
    try:
        if 'spotify_token' not in session:
            return jsonify({'error': 'Please log in first.'}), 401
            
        spotify = SpotifyClient(session['spotify_token'])
        include_features = request.args.get('features', 'false').lower() == 'true'
        playlist = Playlist.from_spotify(spotify, playlist_id, include_features)
        return jsonify(playlist.to_dict())
    except Exception as e:
        logger.error(f"Error getting playlist: {str(e)}")
        return jsonify({'error': str(e)}), 500

@main.route('/playlist/<playlist_id>/stats')
def get_playlist_stats(playlist_id):
    """Get playlist feature statistics."""
    try:
        if 'spotify_token' not in session:
            return jsonify({'error': 'Please log in first.'}), 401
            
        spotify = SpotifyClient(session['spotify_token'])
        playlist = Playlist.from_spotify(spotify, playlist_id, include_features=True)
        return jsonify(playlist.get_feature_stats())
    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}")
        return jsonify({'error': str(e)}), 500

@main.route('/shuffle/<playlist_id>', methods=['POST'])
def shuffle(playlist_id):
    """Shuffle a playlist using the selected algorithm."""
    try:
        if 'spotify_token' not in session:
            logger.warning("No spotify_token in session")
            return jsonify({'success': False, 'message': 'Please log in first.', 'category': 'error'}), 401
        
        # Get algorithm and parameters from form
        algorithm_name = request.form.get('algorithm', 'BasicShuffle')
        try:
            algorithm_class = ShuffleRegistry.get_algorithm(algorithm_name)
            algorithm = algorithm_class()
        except ValueError as e:
            logger.error(f"Invalid algorithm requested: {algorithm_name}")
            return jsonify({'success': False, 'message': 'Invalid shuffle algorithm.', 'category': 'error'}), 400
        
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
        
        # Get playlist data
        playlist = Playlist.from_spotify(spotify, playlist_id, include_features=False)
        tracks = playlist.tracks

        if not tracks:
            logger.error("No tracks found in playlist")
            return jsonify({'success': False, 'message': 'No tracks found in playlist.', 'category': 'error'}), 404
        
        # Initialize the session state for playlist histories if it doesn't exist.
        if 'playlist_states' not in session:
            session['playlist_states'] = {}
        
        current_uris = [track['uri'] for track in tracks]
        
        # If this is the first interaction with this playlist, store its original state.
        if playlist_id not in session['playlist_states']:
            logger.info(f"First interaction with playlist {playlist_id}. Storing original state.")
            session['playlist_states'][playlist_id] = {
                'states': [current_uris],  # The very first state is the original order.
                'current_index': 0
            }
        
        # The state we are shuffling is the one at the current index.
        playlist_state = session['playlist_states'][playlist_id]
        uris_to_shuffle = playlist_state['states'][playlist_state['current_index']]
        
        # The tracks object needs to be in the same order as the URIs we're shuffling.
        uri_to_track_map = {t['uri']: t for t in tracks}
        tracks_to_shuffle = [uri_to_track_map[uri] for uri in uris_to_shuffle if uri in uri_to_track_map]

        shuffled_uris = algorithm.shuffle(tracks_to_shuffle, sp=spotify, **params)
        
        if not shuffled_uris or shuffled_uris == uris_to_shuffle:
            logger.warning("Shuffle did not change playlist order.")
            return jsonify({'success': False, 'message': 'Shuffle did not change the playlist order.', 'category': 'info'})
        
        # Update the playlist on Spotify with the new order.
        success = spotify.update_playlist_tracks(playlist_id, shuffled_uris)
        
        if success:
            # If the user had undone steps, truncate the future states before adding a new one.
            playlist_state['states'] = playlist_state['states'][:playlist_state['current_index'] + 1]
            
            # Add the new state to the history and advance the pointer.
            playlist_state['states'].append(shuffled_uris)
            playlist_state['current_index'] += 1
            
            # Explicitly re-assign the state back to the session to ensure it's saved.
            session['playlist_states'][playlist_id] = playlist_state
            session.modified = True
            
            logger.info(f"New state for playlist {playlist_id} saved. Index at {playlist_state['current_index']}.")
            
            updated_playlist = Playlist.from_spotify(spotify, playlist_id, include_features=False)
            
            return jsonify({
                'success': True,
                'message': f'Playlist shuffled with {algorithm.name}.',
                'category': 'success',
                'playlist': updated_playlist.to_dict(),
                'playlist_state': playlist_state
            })
        else:
            logger.error("Failed to update Spotify playlist.")
            return jsonify({'success': False, 'message': 'Failed to update playlist on Spotify.', 'category': 'error'}), 500

    except Exception as e:
        logger.error(f"Error in shuffle route: {e}", exc_info=True)
        return jsonify({'success': False, 'message': 'An unexpected error occurred.', 'category': 'error'}), 500

@main.route('/undo/<playlist_id>', methods=['POST'])
def undo(playlist_id):
    """Undo the last shuffle for a playlist by stepping back in the state history."""
    try:
        if 'playlist_states' not in session or playlist_id not in session['playlist_states']:
            logger.warning(f"No state history found for playlist {playlist_id}")
            return jsonify({'success': False, 'message': 'No history to restore.', 'category': 'error'}), 404
        
        playlist_state = session['playlist_states'][playlist_id]
        
        # Check if we can step back further.
        if playlist_state['current_index'] <= 0:
            logger.info(f"Already at the original state for playlist {playlist_id}.")
            return jsonify({'success': False, 'message': 'Already at original playlist state.', 'category': 'info'}), 400

        # Move the pointer to the previous state.
        playlist_state['current_index'] -= 1
        restore_uris = playlist_state['states'][playlist_state['current_index']]
        
        logger.info(f"Restoring playlist {playlist_id} to state index {playlist_state['current_index']} with {len(restore_uris)} tracks.")
        
        # Explicitly re-assign the state to the session to ensure the index change is saved.
        session['playlist_states'][playlist_id] = playlist_state
        session.modified = True
        
        spotify = SpotifyClient(session['spotify_token'])
        success = spotify.update_playlist_tracks(playlist_id, restore_uris)
        
        if success:
            restored_playlist = Playlist.from_spotify(spotify, playlist_id, include_features=False)
            logger.info(f"Successfully restored playlist {playlist_id} to state index {playlist_state['current_index']}.")
            return jsonify({
                'success': True,
                'message': 'Playlist restored successfully.',
                'category': 'success',
                'playlist': restored_playlist.to_dict(),
                'playlist_state': playlist_state
            })
        else:
            logger.error(f"Failed to restore playlist {playlist_id} on Spotify.")
            # If the restore fails, revert the index change and re-save the session state.
            playlist_state['current_index'] += 1
            session['playlist_states'][playlist_id] = playlist_state
            session.modified = True
            return jsonify({'success': False, 'message': 'Failed to restore playlist.', 'category': 'error'}), 500

    except Exception as e:
        logger.error(f"Error in undo route: {e}", exc_info=True)
        return jsonify({'success': False, 'message': 'An unexpected error occurred.', 'category': 'error'}), 500 