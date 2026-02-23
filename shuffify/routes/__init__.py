"""
Flask routes package for Shuffify.

This module handles HTTP requests and responses only.
All business logic is delegated to the services layer.

The single `main` Blueprint is split across feature modules for
navigability. All modules import `main` from this package and
register routes on it.
"""

from flask import (
    Blueprint,
    render_template,
    request,
    session,
    jsonify,
    flash,
)
import functools
import logging
from datetime import datetime, timezone

from pydantic import ValidationError

from shuffify.services import (
    AuthService,
    UserService,
    AuthenticationError,
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
# Helper Functions (shared across all route modules)
# =============================================================================


def is_authenticated() -> bool:
    """Check if the user has a valid session token."""
    return AuthService.validate_session_token(
        session.get("spotify_token")
    )


def require_auth():
    """
    Get authenticated client or None.

    Returns:
        SpotifyClient if authenticated, None otherwise.
    """
    if not is_authenticated():
        return None
    try:
        return AuthService.get_authenticated_client(
            session["spotify_token"]
        )
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
        jsonify({
            "success": False,
            "message": message,
            "category": "error",
        }),
        status_code,
    )


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
        first_error = e.errors()[0] if e.errors() else {}
        msg = first_error.get("msg", "Invalid input")
        return None, json_error(
            f"Validation error: {msg}", 400
        )


def require_auth_and_db(f):
    """
    Decorator that enforces authentication and database availability.

    Checks performed in order:
    1. require_auth() -- returns 401 if not authenticated
    2. is_db_available() -- returns 503 if DB is down
    3. get_db_user() -- returns 401 if user not found in DB

    Injects ``client`` (SpotifyClient) and ``user`` (User model)
    as keyword arguments to the wrapped function.

    Usage::

        @main.route("/endpoint")
        @require_auth_and_db
        def my_route(client=None, user=None):
            # client and user are guaranteed non-None here
            ...
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        client = require_auth()
        if not client:
            return json_error("Please log in first.", 401)

        from shuffify import is_db_available
        if not is_db_available():
            return json_error(
                "Database is unavailable.", 503
            )

        user = get_db_user()
        if not user:
            return json_error("User not found.", 401)

        kwargs["client"] = client
        kwargs["user"] = user
        return f(*args, **kwargs)

    return decorated_function


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


# =============================================================================
# Import route modules to register their routes on the Blueprint.
# These must be at the bottom to avoid circular imports.
# =============================================================================

from shuffify.routes import (  # noqa: E402, F401
    core,
    playlists,
    shuffle,
    workshop,
    upstream_sources,
    schedules,
    settings,
    snapshots,
    playlist_pairs,
    raid_panel,
)
