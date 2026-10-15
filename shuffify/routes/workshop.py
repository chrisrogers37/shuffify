"""
Workshop routes: playlist workshop page, preview shuffle, commit,
search, external playlist loading, and session persistence.
"""

import logging

from flask import flash, jsonify, redirect, render_template, request, session, url_for
from pydantic import ValidationError

from shuffify.enums import ActivityType
from shuffify.error_handlers import format_validation_error
from shuffify.routes import (
    api_bp,
    clear_session_and_show_login,
    json_error,
    json_success,
    load_schedule_context,
    log_activity,
    require_auth_and_db,
    require_auth_page,
    require_session_and_db,
    site_bp,
    validate_json,
)
from shuffify.routes.queue import capture_direct_history, enqueue_command
from shuffify.schemas import (
    ExternalPlaylistRequest,
    WorkshopCommitRequest,
    WorkshopSearchRequest,
    parse_shuffle_request,
)
from shuffify.services import (
    AuthenticationError,
    PlaylistAccessError,
    PlaylistError,
    PlaylistNotFoundError,
    PlaylistService,
    ShuffleService,
    WorkshopSessionError,
    WorkshopSessionLimitError,
    WorkshopSessionNotFoundError,
    WorkshopSessionService,
)
from shuffify.services.operations.commands.schemas import WorkshopCommitCommand
from shuffify.services.playlist_preference_service import (
    PlaylistPreferenceService,
)
from shuffify.services.workshop_commit_service import (
    WorkshopCommitService,
    WorkshopTargetResult,
)
from shuffify.services.workshop_finalize_service import WorkshopFinalizeService
from shuffify.spotify.exceptions import SpotifyAuthError
from shuffify.spotify.url_parser import (
    parse_spotify_playlist_url,
)

logger = logging.getLogger(__name__)


@site_bp.route("/workshop")
@require_auth_page
def workshop_hub(api=None, user=None, spotify_profile=None):
    """Render the Workshop hub with no playlist selected."""
    algorithms = ShuffleService.list_algorithms()
    return render_template(
        "workshop.html",
        playlist=None,
        algorithms=algorithms,
        prev_playlist_id=None,
        next_playlist_id=None,
        prev_playlist_name=None,
        next_playlist_name=None,
        upstream_sources_json={},
    )


@site_bp.route("/workshop/<playlist_id>")
@require_auth_page
def workshop(playlist_id, api=None, user=None, spotify_profile=None):
    """Render the Playlist Workshop page."""
    try:
        playlist_service = PlaylistService(api)
        playlist = playlist_service.get_playlist(
            playlist_id, include_features=False
        )

        algorithms = ShuffleService.list_algorithms()

        upstream_sources_json, _ = (
            load_schedule_context(user)
        )

        prev_playlist_id = None
        next_playlist_id = None
        # The neighbours' names label the previous/next links (presentation only).
        playlist_names = {}
        try:
            all_playlists = (
                playlist_service.get_user_playlists()
            )
            ordered_ids = [
                p["id"] for p in all_playlists
            ]
            playlist_names = {
                p["id"]: p.get("name") for p in all_playlists
            }
            try:
                preferences = (
                    PlaylistPreferenceService
                    .get_user_preferences(user.id)
                )
                if preferences:
                    favs, visible, _hidden = (
                        PlaylistPreferenceService
                        .apply_preferences(
                            all_playlists, preferences
                        )
                    )
                    ordered_ids = [
                        p["id"]
                        for p in favs + visible
                    ]
            except Exception as e:
                logger.debug(
                    "Could not apply playlist "
                    "preferences for navigation: %s",
                    e,
                )

            if playlist_id in ordered_ids:
                idx = ordered_ids.index(playlist_id)
                if idx > 0:
                    prev_playlist_id = ordered_ids[
                        idx - 1
                    ]
                if idx < len(ordered_ids) - 1:
                    next_playlist_id = ordered_ids[
                        idx + 1
                    ]
        except Exception as e:
            logger.debug(
                "Could not build playlist "
                "navigation: %s",
                e,
            )

        logger.info(
            f"User {(spotify_profile or {}).get('display_name', 'Unknown')} "
            f"opened workshop for playlist "
            f"'{playlist.name}' ({len(playlist)} tracks)"
        )

        return render_template(
            "workshop.html",
            playlist=playlist.to_dict(),
            user=spotify_profile,
            algorithms=algorithms,
            upstream_sources_json=upstream_sources_json,
            prev_playlist_id=prev_playlist_id,
            next_playlist_id=next_playlist_id,
            prev_playlist_name=playlist_names.get(prev_playlist_id),
            next_playlist_name=playlist_names.get(next_playlist_id),
        )

    except (AuthenticationError, SpotifyAuthError) as e:
        logger.error(f"Error loading workshop: {e}")
        return clear_session_and_show_login(
            "Your session has expired. Please log in again."
        )
    except PlaylistError as e:
        # A missing playlist, someone else's, or Spotify failing: the
        # session is fine, so the user stays logged in.
        logger.warning(f"Could not open playlist {playlist_id} in the workshop: {e}")
        flash(_unloadable_playlist_message(e), "error")
        return redirect(url_for("site.workshop_hub"))


def _unloadable_playlist_message(error: PlaylistError) -> str:
    """What to tell the user when the Workshop cannot load a playlist."""
    if isinstance(error, PlaylistNotFoundError):
        return "That playlist doesn't exist or was deleted."
    if isinstance(error, PlaylistAccessError):
        return "You can only open your own or collaborative playlists in the Workshop."
    return "Couldn't load that playlist from Spotify. Please try again."


@api_bp.route(
    "/workshop/<playlist_id>/preview-shuffle",
    methods=["POST"],
)
@require_auth_and_db
def workshop_preview_shuffle(
    playlist_id, api=None, user=None
):
    """
    Run a shuffle algorithm on client-provided tracks and
    return the new order WITHOUT saving to Spotify.
    """
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
        shuffle_request.algorithm, tracks, params,
        locked_positions=shuffle_request.locked_positions,
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


def _commit_preflight_error(message, status_code):
    """Report a rejected request before the commit service can write anything."""
    result = WorkshopTargetResult(
        "not_written",
        message,
        status_code,
        "preflight_failed",
        requires_refresh=False,
        retryable=True,
    )
    return jsonify(result.to_dict()), result.http_status


@api_bp.route("/workshop/<playlist_id>/commit", methods=["POST"])
@require_session_and_db(on_preflight_error=_commit_preflight_error)
def workshop_commit(playlist_id, user=None):
    """Validate and serialize a target-only Workshop commit."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _commit_preflight_error("Request body must be a JSON object.", 400)
    if "baseline_track_uris" not in data:
        result = WorkshopTargetResult(
            "not_written",
            "Load the current playlist before saving this draft.",
            400,
            "baseline_required",
            True,
        )
        return jsonify(result.to_dict()), result.http_status
    try:
        commit_request = WorkshopCommitRequest(**data)
    except ValidationError as error:
        return _commit_preflight_error(
            f"Validation error: {format_validation_error(error)}", 400
        )
    command = WorkshopCommitCommand(playlist_id=playlist_id,
                                     baseline_track_uris=commit_request.baseline_track_uris,
                                     track_uris=commit_request.track_uris)

    def build(storage):
        binding, _mapping = capture_direct_history(user, playlist_id, "workshop_commit", storage=storage)
        return command, binding

    return enqueue_command(user, "workshop_commit", command.model_dump(mode="json"), build)


@api_bp.route("/workshop/<playlist_id>/current-state")
@require_auth_and_db
def workshop_current_state(playlist_id, api=None, user=None):
    """Read a complete current order without altering the draft or its history."""
    result = WorkshopCommitService(api).read_current(playlist_id, user)
    return jsonify(result.to_dict()), result.http_status


@api_bp.route("/workshop/<playlist_id>/source-context")
@require_auth_and_db
def workshop_source_context(playlist_id, api=None, user=None):
    """Read named source scope before review without changing Spotify or the DB."""
    explicit = "raid" in request.args or "archive" in request.args
    body, status = WorkshopFinalizeService(api).source_context(
        playlist_id, user,
        raid=request.args.get("raid") == "1" if explicit else True,
        archive=request.args.get("archive") == "1" if explicit else True,
    )
    return jsonify(body), status


@api_bp.route("/workshop/search", methods=["POST"])
@require_auth_and_db
def workshop_search(api=None, user=None):
    """Search Spotify's catalog for tracks."""
    search_request, err = validate_json(
        WorkshopSearchRequest
    )
    if err:
        return err

    raw_tracks = api.search_tracks(
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


@api_bp.route(
    "/workshop/search-playlists", methods=["POST"]
)
@require_auth_and_db
def workshop_search_playlists(
    api=None, user=None
):
    """Search for public playlists by name."""
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
        results = api.search_playlists(query, limit=10)

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


def _load_playlist_by_url(api, ext_request, current_user_id=None):
    """Load tracks from a specific playlist by URL/URI/ID."""
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
        playlist_service = PlaylistService(api)
        try:
            playlist = playlist_service.get_playlist(
                playlist_id, include_features=False
            )
            # Non-owned playlists that report tracks but return none.
            is_restricted = (
                len(playlist.tracks) == 0
                and (playlist.total_tracks or 0) > 0
                and playlist.owner_id != current_user_id
            )
        except PlaylistAccessError:
            # Another user's playlist: in Development Mode, Spotify gives
            # the metadata but refuses the tracks, and their count.
            playlist = playlist_service.get_playlist_summary(playlist_id)
            is_restricted = True

        if "external_playlist_history" not in session:
            session["external_playlist_history"] = []

        history = session["external_playlist_history"]
        track_count = (
            playlist.total_tracks
            if is_restricted
            else len(playlist)
        )
        entry = {
            "id": playlist.id,
            "name": playlist.name,
            "owner_id": playlist.owner_id,
            "track_count": track_count,
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

        if is_restricted:
            logger.info(
                f"External playlist "
                f"'{playlist.name}' is restricted "
                f"(Spotify returned none of its tracks)"
            )
            return jsonify({
                "success": True,
                "mode": "restricted",
                "playlist": {
                    "id": playlist.id,
                    "name": playlist.name,
                    "owner_id": playlist.owner_id,
                    "description": playlist.description,
                    "track_count": playlist.total_tracks,
                },
                "tracks": [],
                "message": (
                    "Track listing unavailable for "
                    "this playlist. You can still "
                    "search for individual tracks "
                    "to add."
                ),
                "suggested_search": playlist.name,
            })

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


def _search_playlists_by_query(api, ext_request):
    """Search for playlists by query string."""
    try:
        results = api.search_playlists(
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


@api_bp.route(
    "/workshop/load-external-playlist", methods=["POST"]
)
@require_auth_and_db
def workshop_load_external_playlist(
    api=None, user=None
):
    """Load tracks from an external playlist."""
    ext_request, err = validate_json(
        ExternalPlaylistRequest
    )
    if err:
        return err

    if ext_request.url:
        return _load_playlist_by_url(
            api, ext_request, current_user_id=user.spotify_id
        )

    if ext_request.query:
        return _search_playlists_by_query(api, ext_request)

    return json_error(
        "Either 'url' or 'query' must be provided.", 400
    )


# =============================================================================
# Workshop Session Persistence Routes
# =============================================================================


@api_bp.route(
    "/workshop/<playlist_id>/sessions", methods=["GET"]
)
@require_auth_and_db
def list_workshop_sessions(
    playlist_id, api=None, user=None
):
    """List all saved workshop sessions for a playlist."""
    sessions = WorkshopSessionService.list_sessions(
        user.spotify_id, playlist_id
    )
    return jsonify({
        "success": True,
        "sessions": [ws.to_dict() for ws in sessions],
    })


@api_bp.route(
    "/workshop/<playlist_id>/sessions", methods=["POST"]
)
@require_auth_and_db
def save_workshop_session(
    playlist_id, api=None, user=None
):
    """Save the current workshop state as a named session."""
    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    session_name = data.get("session_name", "").strip()
    track_uris = data.get("track_uris", [])

    if not session_name:
        return json_error("Session name is required.", 400)

    if not isinstance(track_uris, list):
        return json_error("track_uris must be a list.", 400)

    try:
        ws = WorkshopSessionService.save_session(
            spotify_id=user.spotify_id,
            playlist_id=playlist_id,
            session_name=session_name,
            track_uris=track_uris,
        )
        logger.info(
            f"User {user.spotify_id} saved workshop "
            f"session '{session_name}' for playlist "
            f"{playlist_id}"
        )

        # Log activity (non-blocking)
        log_activity(
            user_id=user.id,
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


@api_bp.route(
    "/workshop/sessions/<int:session_id>", methods=["GET"]
)
@require_auth_and_db
def load_workshop_session(
    session_id, api=None, user=None
):
    """Load a saved workshop session by ID."""
    try:
        ws = WorkshopSessionService.get_session(
            session_id, user.spotify_id
        )
        return jsonify({
            "success": True,
            "session": ws.to_dict(),
        })
    except WorkshopSessionNotFoundError:
        return json_error("Saved session not found.", 404)


@api_bp.route(
    "/workshop/sessions/<int:session_id>",
    methods=["PUT"],
)
@require_auth_and_db
def update_workshop_session(
    session_id, api=None, user=None
):
    """Update an existing saved workshop session."""
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

    try:
        ws = WorkshopSessionService.update_session(
            session_id=session_id,
            spotify_id=user.spotify_id,
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


@api_bp.route(
    "/workshop/sessions/<int:session_id>",
    methods=["DELETE"],
)
@require_auth_and_db
def delete_workshop_session(
    session_id, api=None, user=None
):
    """Delete a saved workshop session."""
    try:
        WorkshopSessionService.delete_session(
            session_id, user.spotify_id
        )

        # Log activity (non-blocking)
        log_activity(
            user_id=user.id,
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
