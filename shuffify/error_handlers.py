"""
Global Flask error handlers.

Provides consistent error responses across all endpoints by catching
service-layer exceptions and Pydantic validation errors.
"""

import logging
from flask import Blueprint, jsonify, render_template, request
from pydantic import ValidationError

from shuffify.services import (
    AuthenticationError,
    TokenValidationError,
    PlaylistError,
    PlaylistNotFoundError,
    PlaylistUpdateError,
    ShuffleError,
    InvalidAlgorithmError,
    ParameterValidationError,
    ShuffleExecutionError,
    StateError,
    NoHistoryError,
    AlreadyAtOriginalError,
    UserServiceError,
    UserNotFoundError,
    WorkshopSessionError,
    WorkshopSessionNotFoundError,
    WorkshopSessionLimitError,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
    ScheduleError,
    ScheduleNotFoundError,
    ScheduleLimitError,
    JobExecutionError,
)
from shuffify.spotify.exceptions import (
    SpotifyError,
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyTokenExpiredError,
    SpotifyRateLimitError,
    SpotifyNotFoundError,
)

logger = logging.getLogger(__name__)

# Blueprint for error handlers
errors = Blueprint("errors", __name__)


def json_error_response(message: str, status_code: int, category: str = "error"):
    """Create a standardized JSON error response."""
    return (
        jsonify({"success": False, "message": message, "category": category}),
        status_code,
    )


# =============================================================================
# Pydantic Validation Errors (400)
# =============================================================================


def handle_validation_error(error: ValidationError):
    """Handle Pydantic validation errors."""
    # Extract user-friendly error messages
    errors_list = []
    for err in error.errors():
        field = ".".join(str(loc) for loc in err["loc"])
        msg = err["msg"]
        errors_list.append(f"{field}: {msg}")

    message = "; ".join(errors_list) if errors_list else "Validation failed"
    logger.warning(f"Validation error: {message}")
    return json_error_response(message, 400)


# =============================================================================
# Authentication Errors (401)
# =============================================================================


def handle_authentication_error(error: AuthenticationError):
    """Handle authentication failures."""
    logger.warning(f"Authentication error: {error}")
    return json_error_response("Authentication failed. Please log in again.", 401)


def handle_token_validation_error(error: TokenValidationError):
    """Handle token validation failures."""
    logger.warning(f"Token validation error: {error}")
    return json_error_response("Session expired. Please log in again.", 401)


# =============================================================================
# Not Found Errors (404)
# =============================================================================


def handle_playlist_not_found(error: PlaylistNotFoundError):
    """Handle playlist not found errors."""
    logger.info(f"Playlist not found: {error}")
    return json_error_response("Playlist not found.", 404)


def handle_no_history_error(error: NoHistoryError):
    """Handle missing undo history."""
    logger.info(f"No history: {error}")
    return json_error_response("No history available for this playlist.", 404)


def handle_invalid_algorithm(error: InvalidAlgorithmError):
    """Handle invalid algorithm requests."""
    logger.warning(f"Invalid algorithm: {error}")
    return json_error_response(str(error), 400)


# =============================================================================
# Bad Request Errors (400)
# =============================================================================


def handle_parameter_validation_error(error: ParameterValidationError):
    """Handle parameter validation errors."""
    logger.warning(f"Parameter validation error: {error}")
    return json_error_response(str(error), 400)


def handle_already_at_original(error: AlreadyAtOriginalError):
    """Handle attempts to undo when already at original state."""
    logger.info(f"Already at original: {error}")
    return (
        jsonify(
            {
                "success": False,
                "message": "Already at original playlist state.",
                "category": "info",
            }
        ),
        400,
    )


def handle_playlist_error(error: PlaylistError):
    """Handle general playlist errors."""
    logger.error(f"Playlist error: {error}")
    return json_error_response(str(error), 400)


# =============================================================================
# Server Errors (500)
# =============================================================================


def handle_playlist_update_error(error: PlaylistUpdateError):
    """Handle playlist update failures."""
    logger.error(f"Playlist update error: {error}")
    return json_error_response("Failed to update playlist on Spotify.", 500)


def handle_shuffle_execution_error(error: ShuffleExecutionError):
    """Handle shuffle execution failures."""
    logger.error(f"Shuffle execution error: {error}")
    return json_error_response("Failed to execute shuffle.", 500)


def handle_shuffle_error(error: ShuffleError):
    """Handle general shuffle errors."""
    logger.error(f"Shuffle error: {error}")
    return json_error_response(str(error), 500)


def handle_state_error(error: StateError):
    """Handle state management errors."""
    logger.error(f"State error: {error}")
    return json_error_response("State management error.", 500)


# =============================================================================
# Database / Persistence Errors
# =============================================================================


def handle_user_service_error(error: UserServiceError):
    """Handle user service errors."""
    logger.error(f"User service error: {error}")
    return json_error_response(
        "User operation failed.", 500
    )


def handle_user_not_found(error: UserNotFoundError):
    """Handle user not found errors."""
    logger.info(f"User not found: {error}")
    return json_error_response("User not found.", 404)


def handle_workshop_session_not_found(
    error: WorkshopSessionNotFoundError,
):
    """Handle workshop session not found."""
    logger.info(f"Workshop session not found: {error}")
    return json_error_response(
        "Saved session not found.", 404
    )


def handle_workshop_session_limit(
    error: WorkshopSessionLimitError,
):
    """Handle workshop session limit exceeded."""
    logger.warning(f"Workshop session limit: {error}")
    return json_error_response(str(error), 400)


def handle_workshop_session_error(
    error: WorkshopSessionError,
):
    """Handle general workshop session errors."""
    logger.error(f"Workshop session error: {error}")
    return json_error_response(
        "Workshop session operation failed.", 500
    )


def handle_upstream_source_not_found(
    error: UpstreamSourceNotFoundError,
):
    """Handle upstream source not found."""
    logger.info(f"Upstream source not found: {error}")
    return json_error_response("Source not found.", 404)


def handle_upstream_source_error(
    error: UpstreamSourceError,
):
    """Handle general upstream source errors."""
    logger.error(f"Upstream source error: {error}")
    return json_error_response(
        "Source operation failed.", 500
    )


# =============================================================================
# Schedule Errors
# =============================================================================


def handle_schedule_not_found(
    error: ScheduleNotFoundError,
):
    """Handle schedule not found errors."""
    logger.info(f"Schedule not found: {error}")
    return json_error_response(
        "Schedule not found.", 404
    )


def handle_schedule_limit(
    error: ScheduleLimitError,
):
    """Handle schedule limit exceeded errors."""
    logger.warning(
        f"Schedule limit exceeded: {error}"
    )
    return json_error_response(str(error), 400)


def handle_schedule_error(error: ScheduleError):
    """Handle general schedule errors."""
    logger.error(f"Schedule error: {error}")
    return json_error_response(str(error), 500)


def handle_job_execution_error(
    error: JobExecutionError,
):
    """Handle job execution failures."""
    logger.error(f"Job execution error: {error}")
    return json_error_response(
        f"Job execution failed: {error}", 500
    )


# =============================================================================
# Spotify API Errors (catch-all for exceptions escaping service layer)
# =============================================================================


def handle_spotify_token_expired(
    error: SpotifyTokenExpiredError,
):
    """Handle expired Spotify tokens."""
    logger.warning(f"Spotify token expired: {error}")
    return json_error_response(
        "Session expired. Please log in again.", 401
    )


def handle_spotify_rate_limit(
    error: SpotifyRateLimitError,
):
    """Handle Spotify API rate limiting."""
    logger.warning(f"Spotify rate limit: {error}")
    response, status = json_error_response(
        "Spotify is rate limiting requests. "
        "Please wait a moment and try again.",
        429,
    )
    if hasattr(error, "retry_after") and error.retry_after:
        response.headers["Retry-After"] = str(
            error.retry_after
        )
    return response, status


def handle_spotify_not_found(
    error: SpotifyNotFoundError,
):
    """Handle Spotify resource not found."""
    logger.info(f"Spotify resource not found: {error}")
    return json_error_response(
        "Spotify resource not found.", 404
    )


def handle_spotify_auth_error(
    error: SpotifyAuthError,
):
    """Handle Spotify authentication errors."""
    logger.warning(f"Spotify auth error: {error}")
    return json_error_response(
        "Spotify authentication failed. "
        "Please log in again.",
        401,
    )


def handle_spotify_api_error(
    error: SpotifyAPIError,
):
    """Handle general Spotify API errors."""
    logger.error(f"Spotify API error: {error}")
    return json_error_response(
        "Spotify API error. Please try again.", 500
    )


def handle_spotify_error(error: SpotifyError):
    """Handle any other Spotify errors."""
    logger.error(f"Spotify error: {error}")
    return json_error_response(
        "A Spotify error occurred. Please try again.",
        500,
    )


# =============================================================================
# HTTP Error Codes
# =============================================================================


def handle_bad_request(error):
    """Handle 400 Bad Request."""
    return json_error_response("Bad request.", 400)


def handle_unauthorized(error):
    """Handle 401 Unauthorized."""
    return json_error_response("Please log in first.", 401)


def handle_not_found(error):
    """Handle 404 Not Found."""
    # Only return JSON for API routes
    if request.path.startswith("/api/") or request.is_json:
        return json_error_response("Resource not found.", 404)
    # Let Flask handle HTML 404 pages
    return error


def handle_internal_error(error):
    """Handle 500 Internal Server Error."""
    logger.error(
        "Internal server error on %s %s: %s [type=%s]",
        request.method,
        request.path,
        error,
        type(error).__name__,
        exc_info=True,
    )
    # Return JSON for API routes and AJAX requests
    if (
        request.path.startswith("/api/")
        or request.is_json
        or request.headers.get("X-Requested-With")
        == "XMLHttpRequest"
    ):
        return json_error_response(
            "An unexpected error occurred.", 500
        )
    # Render HTML error page for browser navigation
    return render_template("errors/500.html"), 500


# =============================================================================
# Rate Limiting (429)
# =============================================================================


def handle_rate_limit_exceeded(error):
    """Handle 429 Too Many Requests from Flask-Limiter."""
    logger.warning(
        "Rate limit exceeded: %s on %s",
        request.remote_addr,
        request.path,
    )
    response, status = json_error_response(
        "Too many requests. Please wait a moment and try again.",
        429,
    )
    retry_after = error.description if isinstance(
        error.description, str
    ) and error.description.isdigit() else None
    if retry_after:
        response.headers["Retry-After"] = retry_after
    elif hasattr(error, "retry_after"):
        response.headers["Retry-After"] = str(error.retry_after)
    return response, status


# =============================================================================
# Registration
# =============================================================================


def register_error_handlers(app):
    """Register global error handlers with the Flask app."""
    handlers = [
        (ValidationError, handle_validation_error),
        (AuthenticationError, handle_authentication_error),
        (TokenValidationError, handle_token_validation_error),
        (PlaylistNotFoundError, handle_playlist_not_found),
        (NoHistoryError, handle_no_history_error),
        (InvalidAlgorithmError, handle_invalid_algorithm),
        (ParameterValidationError, handle_parameter_validation_error),
        (AlreadyAtOriginalError, handle_already_at_original),
        (PlaylistError, handle_playlist_error),
        (PlaylistUpdateError, handle_playlist_update_error),
        (ShuffleExecutionError, handle_shuffle_execution_error),
        (ShuffleError, handle_shuffle_error),
        (StateError, handle_state_error),
        (UserServiceError, handle_user_service_error),
        (UserNotFoundError, handle_user_not_found),
        (WorkshopSessionNotFoundError, handle_workshop_session_not_found),
        (WorkshopSessionLimitError, handle_workshop_session_limit),
        (WorkshopSessionError, handle_workshop_session_error),
        (UpstreamSourceNotFoundError, handle_upstream_source_not_found),
        (UpstreamSourceError, handle_upstream_source_error),
        (ScheduleNotFoundError, handle_schedule_not_found),
        (ScheduleLimitError, handle_schedule_limit),
        (ScheduleError, handle_schedule_error),
        (JobExecutionError, handle_job_execution_error),
        (SpotifyTokenExpiredError, handle_spotify_token_expired),
        (SpotifyRateLimitError, handle_spotify_rate_limit),
        (SpotifyNotFoundError, handle_spotify_not_found),
        (SpotifyAuthError, handle_spotify_auth_error),
        (SpotifyAPIError, handle_spotify_api_error),
        (SpotifyError, handle_spotify_error),
        (400, handle_bad_request),
        (401, handle_unauthorized),
        (404, handle_not_found),
        (500, handle_internal_error),
        (429, handle_rate_limit_exceeded),
    ]

    for exc_or_code, handler in handlers:
        app.errorhandler(exc_or_code)(handler)

    logger.info("Global error handlers registered")
