"""
Flask routes package for Shuffify.

This module handles HTTP requests and responses only.
All business logic is delegated to the services layer.

Feature modules register JSON endpoints on ``api_bp``, OAuth endpoints
on ``auth_bp``, and health on ``site_bp``.
"""

import functools
import logging

from flask import (
    Blueprint,
    jsonify,
    request,
    session,
)
from pydantic import ValidationError

from shuffify.error_handlers import (
    format_validation_error,
    json_error_response,
)
from shuffify.services import (
    AuthenticationError,
    AuthService,
    UserService,
)
from shuffify.services.live_operation_service import preflight_error_code
from shuffify.services.live_review_context import context_rejected

logger = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")
auth_bp = Blueprint("auth", __name__)
site_bp = Blueprint("site", __name__)


# =============================================================================
# Helper Functions (shared across all route modules)
# =============================================================================


def is_authenticated() -> bool:
    """Check if the user has a valid session token."""
    return AuthService.validate_session_token(
        session.get("spotify_token")
    )


def require_auth():
    """
    Get an authenticated Spotify API client, or None.

    Returns:
        SpotifyAPI if authenticated, None otherwise.
    """
    if not is_authenticated():
        return None
    try:
        return AuthService.get_authenticated_api(
            session["spotify_token"]
        )
    except AuthenticationError:
        return None


def json_error(message: str, status_code: int = 400) -> tuple:
    """Return a JSON error response.

    Delegates to `error_handlers.json_error_response` so the error envelope
    is constructed in exactly one place. Both names exist because they
    serve different layers -- routes default to 400, the global handlers
    always know their status -- but the payload shape is defined once.
    """
    return json_error_response(message, status_code)


def json_success(message: str, **extra) -> dict:
    """Return a JSON success response."""
    return jsonify({
        "success": True,
        "message": message,
        "category": "success",
        **extra,
    })


def validate_json(schema_class):
    """
    Parse and validate the JSON request body against a Pydantic schema.

    Returns:
        (parsed_model, None) on success.
        (None, error_response_tuple) on failure.

    Usage::

        parsed, err = validate_json(MySchema)
        if err:
            return err
        # use parsed.field ...
    """
    data = request.get_json(silent=True)
    if not data:
        return None, json_error(
            "Request body must be JSON.", 400
        )

    try:
        return schema_class(**data), None
    except ValidationError as e:
        return None, json_error(
            f"Validation error: {format_validation_error(e)}",
            400,
        )


def require_auth_and_db(f=None, *, on_preflight_error=None):
    """
    Decorator that enforces authentication and database availability.

    Checks performed in order:
    1. require_auth() -- returns 401 if not authenticated
    2. is_db_available() -- returns 503 if DB is down
    3. get_db_user() -- returns 401 if user not found in DB

    Injects ``api`` (SpotifyAPI) and ``user`` (User model)
    as keyword arguments to the wrapped function.

    ``on_preflight_error(message, status_code)`` optionally formats these
    gate failures. It never handles responses or exceptions from the route.

    Usage::

        @api_bp.route("/endpoint")
        @require_auth_and_db
        def my_route(api=None, user=None):
            # api and user are guaranteed non-None here
            ...
    """
    if f is None:
        return functools.partial(
            require_auth_and_db, on_preflight_error=on_preflight_error
        )
    preflight_error = on_preflight_error or json_error

    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        api = require_auth()
        if not api:
            return preflight_error("Please log in first.", 401)

        from shuffify import is_db_available
        if not is_db_available():
            return preflight_error(
                "Database is unavailable.", 503
            )

        user = get_db_user()
        if not user:
            return preflight_error("User not found.", 401)

        kwargs["api"] = api
        kwargs["user"] = user
        return f(*args, **kwargs)

    return decorated_function


def require_session_and_db(view=None, *, on_preflight_error=None):
    """Authorize queue requests from the session and database without Spotify I/O."""
    if view is None:
        return functools.partial(require_session_and_db, on_preflight_error=on_preflight_error)
    preflight_error = on_preflight_error or json_error

    @functools.wraps(view)
    def authorized(*args, **kwargs):
        if not is_authenticated():
            return preflight_error("Please log in first.", 401)
        from shuffify import is_db_available

        if not is_db_available():
            return preflight_error("Database is unavailable.", 503)
        user = get_db_user()
        if user is None:
            return preflight_error("User not found.", 401)
        return view(*args, user=user, **kwargs)

    return authorized


def json_reply(reply):
    """A service's ``(payload, status)`` or ``(payload, status, headers)`` reply as a JSON response."""
    payload, status_code, *headers = reply
    return (jsonify(payload), status_code, *headers)


def context_preflight(kind="live_context"):
    """``on_preflight_error`` for a read-only review: it changed nothing and may be loaded again."""

    def preflight(message, status_code):
        return json_reply(context_rejected(
            preflight_error_code(status_code), status_code, message,
            retryable=status_code in (401, 503), kind=kind,
        ))

    return preflight


def no_store(view):
    """Mark a view whose every response, errors included, must not be cached.

    The app's after_request hook sends ``Cache-Control: no-store`` for marked
    views, so CSRF, oversize, preflight and unexpected failures are covered too.
    Apply it between ``@api_bp.route`` and the view's other decorators.
    """
    view.no_store = True
    return view


def get_db_user():
    """
    Get the database User record for the current session user.

    Uses a cached database PK (``_db_user_id``) when available to
    avoid a ``spotify_id`` string lookup on every request.

    Returns:
        User model instance or None if not found.
    """
    from shuffify.models.db import User

    user_data = session.get("user_data")
    if not user_data or "id" not in user_data:
        return None

    # Fast path: use cached integer PK from session
    db_user_id = session.get("_db_user_id")
    if db_user_id:
        user = User.query.get(db_user_id)
        if user:
            return user
        # Cached ID stale — fall through to spotify_id lookup

    # Slow path: lookup by spotify_id string
    user = UserService.get_by_spotify_id(user_data["id"])
    if user:
        session["_db_user_id"] = user.id
        session.modified = True
    return user


def log_activity(
    user_id: int,
    activity_type,
    description: str,
    **kwargs,
) -> None:
    """
    Log a user activity. Never raises -- failures are logged as warnings.

    This is a convenience wrapper around ActivityLogService.log() that
    silences exceptions so activity logging never disrupts route handlers.

    Args:
        user_id: The internal database user ID.
        activity_type: An ActivityType enum value.
        description: Human-readable description of the action.
        **kwargs: Additional keyword args passed to ActivityLogService.log()
            (e.g., playlist_id, playlist_name, metadata).
    """
    try:
        from shuffify.services.activity_log_service import (
            ActivityLogService,
        )
        ActivityLogService.log(
            user_id=user_id,
            activity_type=activity_type,
            description=description,
            **kwargs,
        )
    except Exception as e:
        logger.warning("Activity logging failed: %s", e)


def load_schedule_context(db_user):
    """
    Load upstream sources and playlist pairs for schedule forms.

    Returns (upstream_sources_map, pairs_by_playlist) with graceful
    fallback to empty dicts if DB queries fail.
    """
    from shuffify.services import (
        PlaylistPairService,
        UpstreamSourceService,
    )

    upstream_sources_map = {}
    try:
        sources = (
            UpstreamSourceService
            .list_all_sources_for_user(
                db_user.spotify_id
            )
        )
        for src in sources:
            tid = src.target_playlist_id
            if tid not in upstream_sources_map:
                upstream_sources_map[tid] = []
            upstream_sources_map[tid].append(
                src.to_dict()
            )
    except Exception as e:
        logger.warning(
            "Could not load upstream sources: %s",
            e,
        )

    pairs_by_playlist = {}
    try:
        pairs = PlaylistPairService.get_pairs_for_user(
            db_user.id
        )
        pairs_by_playlist = {
            p.production_playlist_id: p.to_dict()
            for p in pairs
        }
    except Exception as e:
        logger.warning(
            "Could not load playlist pairs: %s",
            e,
        )

    return upstream_sources_map, pairs_by_playlist


# =============================================================================
# Import route modules to register their routes on the Blueprint.
# These must be at the bottom to avoid circular imports.
# =============================================================================

from shuffify.routes import (  # noqa: E402, F401
    contexts,
    core,
    operations,
    playlist_pairs,
    playlist_preferences,
    playlist_setup,
    playlists,
    raid_panel,
    routines,
    schedules,
    settings,
    shuffle,
    snapshots,
    track_locks,
    workshop,
)
