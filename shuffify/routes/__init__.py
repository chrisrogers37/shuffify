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
    session,
    jsonify,
    flash,
)
import logging
from datetime import datetime, timezone

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
)
