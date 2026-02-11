"""
Flask routes for Shuffify.

This module handles HTTP requests and responses only.
All business logic is delegated to the services layer.
"""

from flask import (
    Blueprint,
    render_template,
    session,
    redirect,
    url_for,
    request,
    flash,
    jsonify,
    send_from_directory,
)
import logging
from datetime import datetime, timezone

from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    StateService,
    AuthenticationError,
    PlaylistError,
    PlaylistUpdateError,
)
from pydantic import ValidationError
from shuffify.schemas import (
    parse_shuffle_request,
    PlaylistQueryParams,
    WorkshopCommitRequest,
    WorkshopSearchRequest,
)

logger = logging.getLogger(__name__)
main = Blueprint("main", __name__)


# =============================================================================
# Template Context Processors
# =============================================================================


@main.context_processor
def inject_current_year():
    """Make current year available to all templates."""
    return {"current_year": datetime.now(timezone.utc).year}


# =============================================================================
# Helper Functions
# =============================================================================


def is_authenticated() -> bool:
    """Check if the user has a valid session token."""
    return AuthService.validate_session_token(session.get("spotify_token"))


def require_auth():
    """
    Get authenticated client or None.

    Returns:
        SpotifyClient if authenticated, None otherwise.
    """
    if not is_authenticated():
        return None
    try:
        return AuthService.get_authenticated_client(session["spotify_token"])
    except AuthenticationError:
        return None


def clear_session_and_show_login(message: str = None):
    """Clear session and return to login page with optional message."""
    session.clear()
    if message:
        flash(message, "error")
    return render_template("index.html")


def json_error(message: str, status_code: int = 400) -> tuple:
    """Return a JSON error response."""
    return (
        jsonify({"success": False, "message": message, "category": "error"}),
        status_code,
    )


def json_success(message: str, **extra) -> dict:
    """Return a JSON success response."""
    return jsonify(
        {"success": True, "message": message, "category": "success", **extra}
    )


# =============================================================================
# Public Routes
# =============================================================================


@main.route("/")
def index():
    """Home page - shows login or dashboard based on auth state."""
    try:
        logger.debug("Index route accessed")

        if not is_authenticated():
            logger.debug("No valid token, showing login page")
            session.pop("_flashes", None)
            return render_template("index.html")

        try:
            client = AuthService.get_authenticated_client(session["spotify_token"])
            user = AuthService.get_user_data(client)

            playlist_service = PlaylistService(client)
            playlists = playlist_service.get_user_playlists()

            algorithms = ShuffleService.list_algorithms()

            logger.debug(f"User {user.get('display_name', 'Unknown')} loaded dashboard")
            return render_template(
                "dashboard.html", playlists=playlists, user=user, algorithms=algorithms
            )

        except (AuthenticationError, PlaylistError) as e:
            logger.error(f"Error loading dashboard: {e}")
            return clear_session_and_show_login(
                "Your session has expired. Please log in again."
            )

    except Exception as e:
        logger.error(f"Unexpected error in index route: {e}", exc_info=True)
        return clear_session_and_show_login()


@main.route("/terms")
def terms():
    """Terms of Service page."""
    return send_from_directory("static/public", "terms.html")


@main.route("/privacy")
def privacy():
    """Privacy Policy page."""
    return send_from_directory("static/public", "privacy.html")


@main.route("/health")
def health():
    """Health check endpoint for Docker and monitoring."""
    return (
        jsonify(
            {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}
        ),
        200,
    )


# =============================================================================
# Authentication Routes
# =============================================================================


@main.route("/login")
def login():
    """Initiate Spotify OAuth flow."""
    try:
        # Check for legal consent
        if not request.args.get("legal_consent"):
            flash(
                "You must agree to the Terms of Service and Privacy Policy to use Shuffify.",
                "error",
            )
            return redirect(url_for("main.index"))

        # Clear any existing session data
        session.pop("spotify_token", None)
        session.pop("user_data", None)
        session.modified = True

        auth_url = AuthService.get_auth_url()
        logger.debug(f"Redirecting to Spotify auth: {auth_url}")
        return redirect(auth_url)

    except AuthenticationError as e:
        logger.error(f"Login error: {e}")
        flash("An error occurred during login. Please try again.", "error")
        return redirect(url_for("main.index"))


@main.route("/callback")
def callback():
    """Handle OAuth callback from Spotify."""
    logger.debug(f"Callback received with args: {request.args}")

    # Check for OAuth errors
    error = request.args.get("error")
    if error:
        logger.error(f"OAuth error: {error}")
        flash(
            f'OAuth Error: {request.args.get("error_description", "Unknown error")}',
            "error",
        )
        return redirect(url_for("main.index"))

    # Get authorization code
    code = request.args.get("code")
    if not code:
        logger.error("No authorization code in callback")
        flash("No authorization code received from Spotify. Please try again.", "error")
        return redirect(url_for("main.index"))

    try:
        # Exchange code for token
        token_data = AuthService.exchange_code_for_token(code)
        session["spotify_token"] = token_data

        # Validate by fetching user data
        client, user_data = AuthService.authenticate_and_get_user(token_data)
        session["user_data"] = user_data
        session.modified = True

        logger.info(
            f"User {user_data.get('display_name', 'Unknown')} authenticated successfully"
        )
        return redirect(url_for("main.index"))

    except AuthenticationError as e:
        logger.error(f"Authentication failed: {e}")
        session.pop("spotify_token", None)
        session.pop("user_data", None)
        flash("Error connecting to Spotify. Please try again.", "error")
        return redirect(url_for("main.index"))


@main.route("/logout")
def logout():
    """Clear session and log out."""
    session.clear()
    return redirect(url_for("main.index"))


# =============================================================================
# Playlist API Routes
# =============================================================================


@main.route("/refresh-playlists", methods=["POST"])
def refresh_playlists():
    """Refresh playlists from Spotify without losing undo state."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    try:
        playlist_service = PlaylistService(client)
        playlists = playlist_service.get_user_playlists(skip_cache=True)

        logger.info(f"Refreshed {len(playlists)} playlists from Spotify")
        return json_success(
            "Playlists refreshed successfully.",
            playlists=playlists,
        )
    except PlaylistError as e:
        logger.error(f"Failed to refresh playlists: {e}")
        return json_error("Failed to refresh playlists. Please try again.", 500)


@main.route("/playlist/<playlist_id>")
def get_playlist(playlist_id):
    """Get playlist data with optional audio features."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    # Validate query parameters
    query_params = PlaylistQueryParams(features=request.args.get("features", "false"))

    playlist_service = PlaylistService(client)
    playlist = playlist_service.get_playlist(playlist_id, query_params.features)
    return jsonify(playlist.to_dict())


@main.route("/playlist/<playlist_id>/stats")
def get_playlist_stats(playlist_id):
    """Get playlist audio feature statistics."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    playlist_service = PlaylistService(client)
    stats = playlist_service.get_playlist_stats(playlist_id)
    return jsonify(stats)


# =============================================================================
# Shuffle Routes
# =============================================================================


@main.route("/shuffle/<playlist_id>", methods=["POST"])
def shuffle(playlist_id):
    """Shuffle a playlist using the selected algorithm."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    # Validate request using Pydantic schema (raises ValidationError on failure)
    shuffle_request = parse_shuffle_request(request.form.to_dict())

    # Get algorithm instance
    algorithm = ShuffleService.get_algorithm(shuffle_request.algorithm)

    # Get validated parameters for this algorithm
    params = shuffle_request.get_algorithm_params()

    # Get playlist data
    playlist_service = PlaylistService(client)
    playlist = playlist_service.get_playlist(playlist_id, include_features=False)
    playlist_service.validate_playlist_has_tracks(playlist)

    # Get current track URIs
    current_uris = [track["uri"] for track in playlist.tracks]

    # Initialize or get state for this playlist
    StateService.ensure_playlist_initialized(session, playlist_id, current_uris)

    # Get URIs from current state (may differ from Spotify if manually reordered)
    uris_to_shuffle = (
        StateService.get_current_uris(session, playlist_id) or current_uris
    )

    # Prepare tracks in the correct order for shuffling
    tracks_to_shuffle = ShuffleService.prepare_tracks_for_shuffle(
        playlist.tracks, uris_to_shuffle
    )

    # Execute shuffle
    shuffled_uris = ShuffleService.execute(
        shuffle_request.algorithm, tracks_to_shuffle, params, spotify_client=client
    )

    # Check if order actually changed
    if not ShuffleService.shuffle_changed_order(uris_to_shuffle, shuffled_uris):
        return jsonify(
            {
                "success": False,
                "message": "Shuffle did not change the playlist order.",
                "category": "info",
            }
        )

    # Update Spotify
    playlist_service.update_playlist_tracks(playlist_id, shuffled_uris)

    # Record new state
    updated_state = StateService.record_new_state(session, playlist_id, shuffled_uris)

    # Fetch updated playlist for response
    updated_playlist = playlist_service.get_playlist(
        playlist_id, include_features=False
    )

    logger.info(f"Shuffled playlist {playlist_id} with {shuffle_request.algorithm}")

    return json_success(
        f"Playlist shuffled with {algorithm.name}.",
        playlist=updated_playlist.to_dict(),
        playlist_state=updated_state.to_dict(),
    )


@main.route("/undo/<playlist_id>", methods=["POST"])
def undo(playlist_id):
    """Undo the last shuffle for a playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    # Get previous state URIs (raises NoHistoryError or AlreadyAtOriginalError)
    restore_uris = StateService.undo(session, playlist_id)

    logger.info(f"Restoring playlist {playlist_id} with {len(restore_uris)} tracks")

    # Update Spotify
    playlist_service = PlaylistService(client)
    try:
        playlist_service.update_playlist_tracks(playlist_id, restore_uris)
    except PlaylistUpdateError:
        # Revert the undo if Spotify update failed
        StateService.revert_undo(session, playlist_id)
        raise  # Re-raise for global handler

    # Fetch restored playlist for response
    restored_playlist = playlist_service.get_playlist(
        playlist_id, include_features=False
    )
    state_info = StateService.get_state_info(session, playlist_id)

    logger.info(f"Successfully restored playlist {playlist_id}")

    return json_success(
        "Playlist restored successfully.",
        playlist=restored_playlist.to_dict(),
        playlist_state=state_info,
    )


# =============================================================================
# Workshop Routes
# =============================================================================


@main.route("/workshop/<playlist_id>")
def workshop(playlist_id):
    """Render the Playlist Workshop page."""
    if not is_authenticated():
        return redirect(url_for("main.index"))

    try:
        client = AuthService.get_authenticated_client(session["spotify_token"])
        user = AuthService.get_user_data(client)

        playlist_service = PlaylistService(client)
        playlist = playlist_service.get_playlist(
            playlist_id, include_features=False
        )

        algorithms = ShuffleService.list_algorithms()

        logger.info(
            f"User {user.get('display_name', 'Unknown')} opened workshop for "
            f"playlist '{playlist.name}' ({len(playlist)} tracks)"
        )

        return render_template(
            "workshop.html",
            playlist=playlist.to_dict(),
            user=user,
            algorithms=algorithms,
        )

    except (AuthenticationError, PlaylistError) as e:
        logger.error(f"Error loading workshop: {e}")
        return clear_session_and_show_login(
            "Your session has expired. Please log in again."
        )


@main.route("/workshop/<playlist_id>/preview-shuffle", methods=["POST"])
def workshop_preview_shuffle(playlist_id):
    """
    Run a shuffle algorithm on client-provided tracks and return the new
    order WITHOUT saving to Spotify or fetching from the API.

    Expects JSON body:
        { "algorithm": "BasicShuffle", "tracks": [...], ... params }
    Returns JSON:
        { "success": true, "shuffled_uris": [...] }
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Parse and validate via existing Pydantic schema
    shuffle_request = parse_shuffle_request(data)

    # Get algorithm instance
    algorithm = ShuffleService.get_algorithm(shuffle_request.algorithm)

    # Get validated parameters for this algorithm
    params = shuffle_request.get_algorithm_params()

    # Use tracks from client — no Spotify API call needed
    tracks = data.get("tracks")
    if not tracks or not isinstance(tracks, list):
        return json_error("Request must include 'tracks' array.", 400)

    # Execute shuffle (does NOT update Spotify)
    shuffled_uris = ShuffleService.execute(
        shuffle_request.algorithm, tracks, params
    )

    logger.info(
        f"Preview shuffle for playlist {playlist_id} "
        f"with {shuffle_request.algorithm}"
    )

    return jsonify(
        {
            "success": True,
            "shuffled_uris": shuffled_uris,
            "algorithm_name": algorithm.name,
        }
    )


@main.route("/workshop/<playlist_id>/commit", methods=["POST"])
def workshop_commit(playlist_id):
    """
    Save the workshop's staged track order to Spotify.

    Expects JSON body: { "track_uris": ["spotify:track:...", ...] }
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Validate with Pydantic
    try:
        commit_request = WorkshopCommitRequest(**data)
    except ValidationError as e:
        return json_error(
            f"Invalid request: {e.error_count()} validation error(s).", 400
        )

    # Get current track URIs from Spotify for state tracking
    playlist_service = PlaylistService(client)
    playlist = playlist_service.get_playlist(
        playlist_id, include_features=False
    )
    current_uris = [track["uri"] for track in playlist.tracks]

    # Initialize state if needed
    StateService.ensure_playlist_initialized(
        session, playlist_id, current_uris
    )

    # Check if order actually changed
    if not ShuffleService.shuffle_changed_order(
        current_uris, commit_request.track_uris
    ):
        return json_success(
            "No changes to save — track order is unchanged."
        )

    # Update Spotify
    playlist_service.update_playlist_tracks(
        playlist_id, commit_request.track_uris
    )

    # Record new state for undo
    updated_state = StateService.record_new_state(
        session, playlist_id, commit_request.track_uris
    )

    logger.info(
        f"Workshop commit for playlist {playlist_id}: "
        f"{len(commit_request.track_uris)} tracks saved"
    )

    return json_success(
        "Playlist saved to Spotify!",
        playlist_state=updated_state.to_dict(),
    )


@main.route("/workshop/search", methods=["POST"])
def workshop_search():
    """
    Search Spotify's catalog for tracks.

    Expects JSON body: { "query": "...", "limit": 20, "offset": 0 }
    Returns JSON: { "success": true, "tracks": [...] }
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Validate with Pydantic
    try:
        search_request = WorkshopSearchRequest(**data)
    except ValidationError as e:
        return json_error(
            f"Invalid request: {e.error_count()} validation error(s).", 400
        )

    # Execute search via SpotifyClient facade
    raw_tracks = client.search_tracks(
        query=search_request.query,
        limit=search_request.limit,
        offset=search_request.offset,
    )

    # Transform raw Spotify track objects to simplified format
    # matching the structure used by workshopState/trackDataByUri
    tracks = []
    for track in raw_tracks:
        if not track.get("id") or not track.get("uri"):
            continue
        tracks.append({
            "id": track["id"],
            "name": track["name"],
            "uri": track["uri"],
            "duration_ms": track.get("duration_ms", 0),
            "artists": [
                artist.get("name", "Unknown")
                for artist in track.get("artists", [])
            ],
            "album_name": track.get("album", {}).get("name", ""),
            "album_image_url": (
                track.get("album", {}).get("images", [{}])[0].get("url", "")
            ),
        })

    logger.info(
        f"Workshop search for '{search_request.query}' "
        f"returned {len(tracks)} tracks"
    )

    return jsonify({
        "success": True,
        "tracks": tracks,
        "query": search_request.query,
        "offset": search_request.offset,
        "limit": search_request.limit,
    })
