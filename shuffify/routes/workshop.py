"""
Workshop routes: playlist workshop page, preview shuffle, commit,
search, external playlist loading, and session persistence.
"""

import logging

from flask import session, redirect, url_for, request, jsonify
from pydantic import ValidationError

from shuffify.routes import (
    main,
    is_authenticated,
    require_auth,
    clear_session_and_show_login,
    json_error,
    json_success,
    get_db_user,
    log_activity,
)
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    StateService,
    WorkshopSessionService,
    WorkshopSessionError,
    WorkshopSessionNotFoundError,
    WorkshopSessionLimitError,
    AuthenticationError,
    PlaylistError,
    PlaylistSnapshotService,
    UserService,
)
from shuffify.enums import SnapshotType, ActivityType
from shuffify import is_db_available
from shuffify.schemas import (
    parse_shuffle_request,
    WorkshopCommitRequest,
    WorkshopSearchRequest,
    ExternalPlaylistRequest,
)
from shuffify.spotify.url_parser import (
    parse_spotify_playlist_url,
)
from flask import render_template

logger = logging.getLogger(__name__)


@main.route("/workshop/<playlist_id>")
def workshop(playlist_id):
    """Render the Playlist Workshop page."""
    if not is_authenticated():
        return redirect(url_for("main.index"))

    try:
        client = AuthService.get_authenticated_client(
            session["spotify_token"]
        )
        user = AuthService.get_user_data(client)

        playlist_service = PlaylistService(client)
        playlist = playlist_service.get_playlist(
            playlist_id, include_features=False
        )

        algorithms = ShuffleService.list_algorithms()

        logger.info(
            f"User {user.get('display_name', 'Unknown')} "
            f"opened workshop for playlist "
            f"'{playlist.name}' ({len(playlist)} tracks)"
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


@main.route(
    "/workshop/<playlist_id>/preview-shuffle",
    methods=["POST"],
)
def workshop_preview_shuffle(playlist_id):
    """
    Run a shuffle algorithm on client-provided tracks and
    return the new order WITHOUT saving to Spotify.
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    shuffle_request = parse_shuffle_request(data)
    algorithm = ShuffleService.get_algorithm(
        shuffle_request.algorithm
    )
    params = shuffle_request.get_algorithm_params()

    tracks = data.get("tracks")
    if not tracks or not isinstance(tracks, list):
        return json_error(
            "Request must include 'tracks' array.", 400
        )

    shuffled_uris = ShuffleService.execute(
        shuffle_request.algorithm, tracks, params
    )

    logger.info(
        f"Preview shuffle for playlist {playlist_id} "
        f"with {shuffle_request.algorithm}"
    )

    return jsonify({
        "success": True,
        "shuffled_uris": shuffled_uris,
        "algorithm_name": algorithm.name,
    })


@main.route(
    "/workshop/<playlist_id>/commit", methods=["POST"]
)
def workshop_commit(playlist_id):
    """Save the workshop's staged track order to Spotify."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    try:
        commit_request = WorkshopCommitRequest(**data)
    except ValidationError as e:
        return json_error(
            f"Invalid request: {e.error_count()} "
            f"validation error(s).",
            400,
        )

    playlist_service = PlaylistService(client)
    playlist = playlist_service.get_playlist(
        playlist_id, include_features=False
    )
    current_uris = [
        track["uri"] for track in playlist.tracks
    ]

    # --- Auto-snapshot before commit ---
    if is_db_available():
        db_user = get_db_user()
        if (
            db_user
            and PlaylistSnapshotService
            .is_auto_snapshot_enabled(db_user.id)
        ):
            try:
                PlaylistSnapshotService.create_snapshot(
                    user_id=db_user.id,
                    playlist_id=playlist_id,
                    playlist_name=playlist.name,
                    track_uris=current_uris,
                    snapshot_type=(
                        SnapshotType.AUTO_PRE_COMMIT
                    ),
                    trigger_description=(
                        "Before workshop commit"
                    ),
                )
            except Exception as e:
                logger.warning(
                    "Auto-snapshot before commit "
                    f"failed: {e}"
                )
    # --- End auto-snapshot ---

    StateService.ensure_playlist_initialized(
        session, playlist_id, current_uris
    )

    if not ShuffleService.shuffle_changed_order(
        current_uris, commit_request.track_uris
    ):
        return json_success(
            "No changes to save — track order is unchanged."
        )

    playlist_service.update_playlist_tracks(
        playlist_id, commit_request.track_uris
    )

    updated_state = StateService.record_new_state(
        session, playlist_id, commit_request.track_uris
    )

    logger.info(
        f"Workshop commit for playlist {playlist_id}: "
        f"{len(commit_request.track_uris)} tracks saved"
    )

    # Log activity (non-blocking)
    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if spotify_id:
        db_user = UserService.get_by_spotify_id(
            spotify_id
        )
        if db_user:
            log_activity(
                user_id=db_user.id,
                activity_type=(
                    ActivityType.WORKSHOP_COMMIT
                ),
                description=(
                    f"Committed workshop changes "
                    f"to '{playlist.name}'"
                ),
                playlist_id=playlist_id,
                playlist_name=playlist.name,
                metadata={
                    "track_count": len(
                        commit_request.track_uris
                    ),
                },
            )

    return json_success(
        "Playlist saved to Spotify!",
        playlist_state=updated_state.to_dict(),
    )


@main.route("/workshop/search", methods=["POST"])
def workshop_search():
    """Search Spotify's catalog for tracks."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    try:
        search_request = WorkshopSearchRequest(**data)
    except ValidationError as e:
        return json_error(
            f"Invalid request: {e.error_count()} "
            f"validation error(s).",
            400,
        )

    raw_tracks = client.search_tracks(
        query=search_request.query,
        limit=search_request.limit,
        offset=search_request.offset,
    )

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
            "album_name": track.get("album", {}).get(
                "name", ""
            ),
            "album_image_url": (
                track.get("album", {})
                .get("images", [{}])[0]
                .get("url", "")
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
# External Playlist Routes
# =============================================================================


@main.route(
    "/workshop/search-playlists", methods=["POST"]
)
def workshop_search_playlists():
    """Search for public playlists by name."""
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
            "Search query too long (max 200 characters).",
            400,
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
        logger.error(
            f"Playlist search failed: {e}", exc_info=True
        )
        return json_error(
            "Search failed. Please try again.", 500
        )


@main.route(
    "/workshop/load-external-playlist", methods=["POST"]
)
def workshop_load_external_playlist():
    """Load tracks from an external playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    try:
        ext_request = ExternalPlaylistRequest(**data)
    except Exception as e:
        return json_error(str(e), 400)

    # --- URL mode: load a specific playlist ---
    if ext_request.url:
        playlist_id = parse_spotify_playlist_url(
            ext_request.url
        )
        if not playlist_id:
            return json_error(
                "Could not parse a playlist ID from the "
                "provided URL. Please use a Spotify "
                "playlist URL, URI, or ID.",
                400,
            )

        try:
            playlist_service = PlaylistService(client)
            playlist = playlist_service.get_playlist(
                playlist_id, include_features=False
            )

            if "external_playlist_history" not in session:
                session["external_playlist_history"] = []

            history = session["external_playlist_history"]
            entry = {
                "id": playlist.id,
                "name": playlist.name,
                "owner_id": playlist.owner_id,
                "track_count": len(playlist),
            }
            history = [
                h for h in history
                if h["id"] != playlist.id
            ]
            history.insert(0, entry)
            session["external_playlist_history"] = (
                history[:10]
            )
            session.modified = True

            logger.info(
                f"Loaded external playlist "
                f"'{playlist.name}' "
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
            logger.error(
                f"Failed to load external playlist: {e}"
            )
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
                f"Playlist search failed: {e}",
                exc_info=True,
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


@main.route(
    "/workshop/<playlist_id>/sessions", methods=["GET"]
)
def list_workshop_sessions(playlist_id):
    """List all saved workshop sessions for a playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. "
            "Cannot load saved sessions.",
            503,
        )

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error(
            "User data not found in session.", 401
        )

    sessions = WorkshopSessionService.list_sessions(
        spotify_id, playlist_id
    )
    return jsonify({
        "success": True,
        "sessions": [ws.to_dict() for ws in sessions],
    })


@main.route(
    "/workshop/<playlist_id>/sessions", methods=["POST"]
)
def save_workshop_session(playlist_id):
    """Save the current workshop state as a named session."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. "
            "Cannot save session.",
            503,
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
        return json_error(
            "User data not found in session.", 401
        )

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

        # Log activity (non-blocking)
        db_user = UserService.get_by_spotify_id(
            spotify_id
        )
        if db_user:
            log_activity(
                user_id=db_user.id,
                activity_type=(
                    ActivityType
                    .WORKSHOP_SESSION_SAVE
                ),
                description=(
                    f"Saved workshop session "
                    f"'{session_name}'"
                ),
                playlist_id=playlist_id,
                metadata={
                    "session_name": session_name,
                    "track_count": len(track_uris),
                },
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
            "Database is unavailable. "
            "Cannot load session.",
            503,
        )

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error(
            "User data not found in session.", 401
        )

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
    "/workshop/sessions/<int:session_id>",
    methods=["PUT"],
)
def update_workshop_session(session_id):
    """Update an existing saved workshop session."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. "
            "Cannot update session.",
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
        return json_error(
            "track_uris must be a list.", 400
        )

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error(
            "User data not found in session.", 401
        )

    try:
        ws = WorkshopSessionService.update_session(
            session_id=session_id,
            spotify_id=spotify_id,
            track_uris=(
                track_uris
                if track_uris is not None
                else []
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
    "/workshop/sessions/<int:session_id>",
    methods=["DELETE"],
)
def delete_workshop_session(session_id):
    """Delete a saved workshop session."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. "
            "Cannot delete session.",
            503,
        )

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error(
            "User data not found in session.", 401
        )

    try:
        WorkshopSessionService.delete_session(
            session_id, spotify_id
        )

        # Log activity (non-blocking)
        db_user = UserService.get_by_spotify_id(
            spotify_id
        )
        if db_user:
            log_activity(
                user_id=db_user.id,
                activity_type=(
                    ActivityType
                    .WORKSHOP_SESSION_DELETE
                ),
                description=(
                    f"Deleted workshop session "
                    f"{session_id}"
                ),
            )

        return json_success("Session deleted.")
    except WorkshopSessionNotFoundError:
        return json_error("Saved session not found.", 404)
    except WorkshopSessionError as e:
        return json_error(str(e), 500)
