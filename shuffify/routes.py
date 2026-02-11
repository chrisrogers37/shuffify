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
    UserService,
    WorkshopSessionService,
    WorkshopSessionError,
    WorkshopSessionNotFoundError,
    WorkshopSessionLimitError,
    UpstreamSourceService,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
    AuthenticationError,
    PlaylistError,
    PlaylistUpdateError,
)
from shuffify import is_db_available
from pydantic import ValidationError
from shuffify.schemas import (
    parse_shuffle_request,
    PlaylistQueryParams,
    WorkshopCommitRequest,
    WorkshopSearchRequest,
    ExternalPlaylistRequest,
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
)
from shuffify.services.scheduler_service import (
    SchedulerService,
)
from shuffify.services.job_executor_service import (
    JobExecutorService,
)
from shuffify.spotify.url_parser import parse_spotify_playlist_url

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


def get_db_user():
    """
    Get the database User record for the current session user.

    Returns:
        User model instance or None if not found.
    """
    user_data = session.get("user_data")
    if not user_data or "id" not in user_data:
        return None

    return UserService.get_by_spotify_id(user_data["id"])


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

        # Upsert user record in database (non-blocking)
        try:
            UserService.upsert_from_spotify(user_data)
        except Exception as e:
            # Database failure should NOT block login
            logger.warning(
                f"Failed to upsert user to database: {e}. "
                f"Login continues without persistence."
            )

        # Store encrypted refresh token for scheduled operations
        if token_data.get("refresh_token"):
            try:
                from shuffify.services.token_service import (
                    TokenService,
                )
                from shuffify.models.db import db as _db

                db_user = UserService.get_by_spotify_id(
                    user_data["id"]
                )
                if db_user and TokenService.is_initialized():
                    db_user.encrypted_refresh_token = (
                        TokenService.encrypt_token(
                            token_data["refresh_token"]
                        )
                    )
                    _db.session.commit()
                    logger.debug(
                        f"Stored encrypted refresh token "
                        f"for user {user_data['id']}"
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to store refresh token: {e}. "
                    f"Scheduled operations may not work."
                )

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


@main.route("/api/user-playlists")
def api_user_playlists():
    """Return the user's editable playlists as JSON for AJAX consumers."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    try:
        playlist_service = PlaylistService(client)
        playlists = playlist_service.get_user_playlists()

        # Return a lightweight list: id, name, track count, image
        result = []
        for p in playlists:
            result.append({
                "id": p["id"],
                "name": p["name"],
                "track_count": p.get("tracks", {}).get("total", 0),
                "image_url": (
                    p["images"][0]["url"] if p.get("images") else None
                ),
            })

        logger.debug(f"API returned {len(result)} playlists")
        return jsonify({"success": True, "playlists": result})
    except PlaylistError as e:
        logger.error(f"Failed to fetch playlists for API: {e}")
        return json_error("Failed to fetch playlists.", 500)


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


# =============================================================================
# Workshop: External Playlist Routes
# =============================================================================


@main.route("/workshop/search-playlists", methods=["POST"])
def workshop_search_playlists():
    """
    Search for public playlists by name.

    Expects JSON body: { "query": "jazz vibes" }
    Returns JSON: { "success": true, "playlists": [...] }
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    query = data.get("query", "").strip()
    if not query:
        return json_error("Search query is required.", 400)

    if len(query) > 200:
        return json_error(
            "Search query too long (max 200 characters).", 400
        )

    try:
        results = client.search_playlists(query, limit=10)

        logger.info(
            f"Playlist search for '{query}' returned "
            f"{len(results)} results"
        )

        return jsonify({
            "success": True,
            "playlists": results,
        })

    except Exception as e:
        logger.error(f"Playlist search failed: {e}", exc_info=True)
        return json_error("Search failed. Please try again.", 500)


@main.route("/workshop/load-external-playlist", methods=["POST"])
def workshop_load_external_playlist():
    """
    Load tracks from an external playlist by URL/URI/ID or search query.

    Expects JSON body:
        { "url": "https://open.spotify.com/playlist/..." }
        or
        { "query": "jazz vibes" }

    Returns JSON:
        For URL: { "success": true, "mode": "tracks", ... }
        For query: { "success": true, "mode": "search", ... }
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Validate with Pydantic
    try:
        ext_request = ExternalPlaylistRequest(**data)
    except Exception as e:
        return json_error(str(e), 400)

    # --- URL mode: load a specific playlist ---
    if ext_request.url:
        playlist_id = parse_spotify_playlist_url(ext_request.url)
        if not playlist_id:
            return json_error(
                "Could not parse a playlist ID from the provided URL. "
                "Please use a Spotify playlist URL, URI, or ID.",
                400,
            )

        try:
            playlist_service = PlaylistService(client)
            playlist = playlist_service.get_playlist(
                playlist_id, include_features=False
            )

            # Store in session history for "recently loaded" feature
            if "external_playlist_history" not in session:
                session["external_playlist_history"] = []

            history = session["external_playlist_history"]
            # Add to front, remove duplicates, keep max 10
            entry = {
                "id": playlist.id,
                "name": playlist.name,
                "owner_id": playlist.owner_id,
                "track_count": len(playlist),
            }
            history = [h for h in history if h["id"] != playlist.id]
            history.insert(0, entry)
            session["external_playlist_history"] = history[:10]
            session.modified = True

            logger.info(
                f"Loaded external playlist '{playlist.name}' "
                f"({len(playlist)} tracks)"
            )

            return jsonify({
                "success": True,
                "mode": "tracks",
                "playlist": {
                    "id": playlist.id,
                    "name": playlist.name,
                    "owner_id": playlist.owner_id,
                    "description": playlist.description,
                    "track_count": len(playlist),
                },
                "tracks": playlist.tracks,
            })

        except PlaylistError as e:
            logger.error(f"Failed to load external playlist: {e}")
            return json_error(
                "Could not load playlist. "
                "It may be private or deleted.",
                404,
            )

    # --- Query mode: search for playlists ---
    if ext_request.query:
        try:
            results = client.search_playlists(
                ext_request.query, limit=10
            )

            logger.info(
                f"External playlist search for "
                f"'{ext_request.query}' "
                f"returned {len(results)} results"
            )

            return jsonify({
                "success": True,
                "mode": "search",
                "playlists": results,
            })

        except Exception as e:
            logger.error(
                f"Playlist search failed: {e}", exc_info=True
            )
            return json_error(
                "Search failed. Please try again.", 500
            )

    return json_error(
        "Either 'url' or 'query' must be provided.", 400
    )


# =============================================================================
# Workshop Session Persistence Routes
# =============================================================================


@main.route("/workshop/<playlist_id>/sessions", methods=["GET"])
def list_workshop_sessions(playlist_id):
    """List all saved workshop sessions for a playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. Cannot load saved sessions.",
            503,
        )

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    sessions = WorkshopSessionService.list_sessions(
        spotify_id, playlist_id
    )
    return jsonify({
        "success": True,
        "sessions": [ws.to_dict() for ws in sessions],
    })


@main.route("/workshop/<playlist_id>/sessions", methods=["POST"])
def save_workshop_session(playlist_id):
    """Save the current workshop state as a named session."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. Cannot save session.", 503
        )

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    session_name = data.get("session_name", "").strip()
    track_uris = data.get("track_uris", [])

    if not session_name:
        return json_error("Session name is required.", 400)

    if not isinstance(track_uris, list):
        return json_error("track_uris must be a list.", 400)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        ws = WorkshopSessionService.save_session(
            spotify_id=spotify_id,
            playlist_id=playlist_id,
            session_name=session_name,
            track_uris=track_uris,
        )
        logger.info(
            f"User {spotify_id} saved workshop session "
            f"'{session_name}' for playlist {playlist_id}"
        )
        return json_success(
            f"Session '{session_name}' saved.",
            session=ws.to_dict(),
        )
    except WorkshopSessionLimitError as e:
        return json_error(str(e), 400)
    except WorkshopSessionError as e:
        return json_error(str(e), 500)


@main.route(
    "/workshop/sessions/<int:session_id>", methods=["GET"]
)
def load_workshop_session(session_id):
    """Load a saved workshop session by ID."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. Cannot load session.", 503
        )

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        ws = WorkshopSessionService.get_session(
            session_id, spotify_id
        )
        return jsonify({
            "success": True,
            "session": ws.to_dict(),
        })
    except WorkshopSessionNotFoundError:
        return json_error("Saved session not found.", 404)


@main.route(
    "/workshop/sessions/<int:session_id>", methods=["PUT"]
)
def update_workshop_session(session_id):
    """Update an existing saved workshop session."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. Cannot update session.",
            503,
        )

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    track_uris = data.get("track_uris")
    session_name = data.get("session_name")

    if track_uris is not None and not isinstance(
        track_uris, list
    ):
        return json_error("track_uris must be a list.", 400)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        ws = WorkshopSessionService.update_session(
            session_id=session_id,
            spotify_id=spotify_id,
            track_uris=(
                track_uris if track_uris is not None else []
            ),
            session_name=session_name,
        )
        return json_success(
            f"Session '{ws.session_name}' updated.",
            session=ws.to_dict(),
        )
    except WorkshopSessionNotFoundError:
        return json_error("Saved session not found.", 404)
    except WorkshopSessionError as e:
        return json_error(str(e), 500)


@main.route(
    "/workshop/sessions/<int:session_id>", methods=["DELETE"]
)
def delete_workshop_session(session_id):
    """Delete a saved workshop session."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. Cannot delete session.",
            503,
        )

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        WorkshopSessionService.delete_session(
            session_id, spotify_id
        )
        return json_success("Session deleted.")
    except WorkshopSessionNotFoundError:
        return json_error("Saved session not found.", 404)
    except WorkshopSessionError as e:
        return json_error(str(e), 500)


# =============================================================================
# Upstream Source Routes
# =============================================================================


@main.route(
    "/playlist/<playlist_id>/upstream-sources", methods=["GET"]
)
def list_upstream_sources(playlist_id):
    """List all upstream sources for a target playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable.", 503)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    sources = UpstreamSourceService.list_sources(
        spotify_id, playlist_id
    )
    return jsonify({
        "success": True,
        "sources": [s.to_dict() for s in sources],
    })


@main.route(
    "/playlist/<playlist_id>/upstream-sources", methods=["POST"]
)
def add_upstream_source(playlist_id):
    """Add an upstream source to a target playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable.", 503)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    source_playlist_id = data.get("source_playlist_id")
    if not source_playlist_id:
        return json_error(
            "source_playlist_id is required.", 400
        )

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        source = UpstreamSourceService.add_source(
            spotify_id=spotify_id,
            target_playlist_id=playlist_id,
            source_playlist_id=source_playlist_id,
            source_type=data.get("source_type", "external"),
            source_url=data.get("source_url"),
            source_name=data.get("source_name"),
        )
        return json_success(
            "Source added.",
            source=source.to_dict(),
        )
    except UpstreamSourceError as e:
        return json_error(str(e), 400)


@main.route(
    "/upstream-sources/<int:source_id>", methods=["DELETE"]
)
def delete_upstream_source(source_id):
    """Delete an upstream source configuration."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable.", 503)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        UpstreamSourceService.delete_source(
            source_id, spotify_id
        )
        return json_success("Source removed.")
    except UpstreamSourceNotFoundError:
        return json_error("Source not found.", 404)
    except UpstreamSourceError as e:
        return json_error(str(e), 500)


# =============================================================================
# Schedule Routes
# =============================================================================


@main.route("/schedules")
def schedules():
    """Render the Schedules management page."""
    if not is_authenticated():
        return redirect(url_for("main.index"))

    try:
        client = AuthService.get_authenticated_client(
            session["spotify_token"]
        )
        user = AuthService.get_user_data(client)

        db_user = get_db_user()
        if not db_user:
            flash(
                "Please log in again to access schedules.",
                "error",
            )
            return redirect(url_for("main.index"))

        user_schedules = SchedulerService.get_user_schedules(
            db_user.id
        )

        playlist_service = PlaylistService(client)
        playlists = playlist_service.get_user_playlists()

        algorithms = ShuffleService.list_algorithms()

        return render_template(
            "schedules.html",
            user=user,
            schedules=[s.to_dict() for s in user_schedules],
            playlists=playlists,
            algorithms=algorithms,
            max_schedules=(
                SchedulerService.MAX_SCHEDULES_PER_USER
            ),
        )

    except (AuthenticationError, PlaylistError) as e:
        logger.error(f"Error loading schedules page: {e}")
        return clear_session_and_show_login(
            "Your session has expired. Please log in again."
        )


@main.route("/schedules/create", methods=["POST"])
def create_schedule():
    """Create a new scheduled operation."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    if not db_user.encrypted_refresh_token:
        return json_error(
            "Your account needs a fresh login to enable "
            "scheduled operations. Please log out and "
            "log back in.",
            400,
        )

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Validate with Pydantic
    create_request = ScheduleCreateRequest(**data)

    schedule = SchedulerService.create_schedule(
        user_id=db_user.id,
        job_type=create_request.job_type,
        target_playlist_id=(
            create_request.target_playlist_id
        ),
        target_playlist_name=(
            create_request.target_playlist_name
        ),
        schedule_type=create_request.schedule_type,
        schedule_value=create_request.schedule_value,
        source_playlist_ids=(
            create_request.source_playlist_ids
        ),
        algorithm_name=create_request.algorithm_name,
        algorithm_params=create_request.algorithm_params,
    )

    # Register with APScheduler
    try:
        from flask import current_app
        from shuffify.scheduler import add_job_for_schedule

        add_job_for_schedule(
            schedule,
            current_app._get_current_object(),
        )
    except RuntimeError as e:
        logger.warning(
            f"Could not register schedule with "
            f"APScheduler: {e}"
        )

    logger.info(
        f"User {db_user.spotify_id} created schedule "
        f"{schedule.id}: {schedule.job_type} on "
        f"{schedule.target_playlist_name}"
    )

    return json_success(
        "Schedule created successfully.",
        schedule=schedule.to_dict(),
    )


@main.route(
    "/schedules/<int:schedule_id>", methods=["PUT"]
)
def update_schedule(schedule_id):
    """Update an existing schedule."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    update_request = ScheduleUpdateRequest(**data)
    update_fields = {
        k: v
        for k, v in update_request.model_dump().items()
        if v is not None
    }

    schedule = SchedulerService.update_schedule(
        schedule_id=schedule_id,
        user_id=db_user.id,
        **update_fields,
    )

    # Update APScheduler job
    try:
        from flask import current_app
        from shuffify.scheduler import (
            add_job_for_schedule,
            remove_job_for_schedule,
        )

        if schedule.is_enabled:
            add_job_for_schedule(
                schedule,
                current_app._get_current_object(),
            )
        else:
            remove_job_for_schedule(schedule_id)
    except RuntimeError as e:
        logger.warning(
            f"Could not update APScheduler job: {e}"
        )

    logger.info(f"Updated schedule {schedule_id}")

    return json_success(
        "Schedule updated successfully.",
        schedule=schedule.to_dict(),
    )


@main.route(
    "/schedules/<int:schedule_id>", methods=["DELETE"]
)
def delete_schedule(schedule_id):
    """Delete a schedule."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    from shuffify.scheduler import remove_job_for_schedule

    remove_job_for_schedule(schedule_id)

    SchedulerService.delete_schedule(
        schedule_id, db_user.id
    )

    logger.info(f"Deleted schedule {schedule_id}")

    return json_success("Schedule deleted successfully.")


@main.route(
    "/schedules/<int:schedule_id>/toggle", methods=["POST"]
)
def toggle_schedule(schedule_id):
    """Toggle a schedule's enabled/disabled state."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    schedule = SchedulerService.toggle_schedule(
        schedule_id, db_user.id
    )

    try:
        from flask import current_app
        from shuffify.scheduler import (
            add_job_for_schedule,
            remove_job_for_schedule,
        )

        if schedule.is_enabled:
            add_job_for_schedule(
                schedule,
                current_app._get_current_object(),
            )
        else:
            remove_job_for_schedule(schedule_id)
    except RuntimeError as e:
        logger.warning(
            f"Could not update APScheduler job: {e}"
        )

    status_text = (
        "enabled" if schedule.is_enabled else "disabled"
    )
    return json_success(
        f"Schedule {status_text}.",
        schedule=schedule.to_dict(),
    )


@main.route(
    "/schedules/<int:schedule_id>/run", methods=["POST"]
)
def run_schedule_now(schedule_id):
    """Manually trigger a schedule execution."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    result = JobExecutorService.execute_now(
        schedule_id, db_user.id
    )

    return json_success(
        "Schedule executed successfully.",
        result=result,
    )


@main.route("/schedules/<int:schedule_id>/history")
def schedule_history(schedule_id):
    """Get execution history for a schedule."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    history = SchedulerService.get_execution_history(
        schedule_id, db_user.id, limit=10
    )

    return jsonify({"success": True, "history": history})
