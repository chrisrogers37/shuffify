"""
Global Flask error handlers.

Provides consistent error responses across all endpoints by catching
service-layer exceptions and Pydantic validation errors.
"""

import logging
from flask import Blueprint, jsonify, request
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


def register_error_handlers(app):
    """
    Register global error handlers with the Flask app.

    Args:
        app: The Flask application instance.
    """

    # =========================================================================
    # Pydantic Validation Errors (400)
    # =========================================================================

    @app.errorhandler(ValidationError)
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

    # =========================================================================
    # Authentication Errors (401)
    # =========================================================================

    @app.errorhandler(AuthenticationError)
    def handle_authentication_error(error: AuthenticationError):
        """Handle authentication failures."""
        logger.warning(f"Authentication error: {error}")
        return json_error_response("Authentication failed. Please log in again.", 401)

    @app.errorhandler(TokenValidationError)
    def handle_token_validation_error(error: TokenValidationError):
        """Handle token validation failures."""
        logger.warning(f"Token validation error: {error}")
        return json_error_response("Session expired. Please log in again.", 401)

    # =========================================================================
    # Not Found Errors (404)
    # =========================================================================

    @app.errorhandler(PlaylistNotFoundError)
    def handle_playlist_not_found(error: PlaylistNotFoundError):
        """Handle playlist not found errors."""
        logger.info(f"Playlist not found: {error}")
        return json_error_response("Playlist not found.", 404)

    @app.errorhandler(NoHistoryError)
    def handle_no_history_error(error: NoHistoryError):
        """Handle missing undo history."""
        logger.info(f"No history: {error}")
        return json_error_response("No history available for this playlist.", 404)

    @app.errorhandler(InvalidAlgorithmError)
    def handle_invalid_algorithm(error: InvalidAlgorithmError):
        """Handle invalid algorithm requests."""
        logger.warning(f"Invalid algorithm: {error}")
        return json_error_response(str(error), 400)

    # =========================================================================
    # Bad Request Errors (400)
    # =========================================================================

    @app.errorhandler(ParameterValidationError)
    def handle_parameter_validation_error(error: ParameterValidationError):
        """Handle parameter validation errors."""
        logger.warning(f"Parameter validation error: {error}")
        return json_error_response(str(error), 400)

    @app.errorhandler(AlreadyAtOriginalError)
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

    @app.errorhandler(PlaylistError)
    def handle_playlist_error(error: PlaylistError):
        """Handle general playlist errors."""
        logger.error(f"Playlist error: {error}")
        return json_error_response(str(error), 400)

    # =========================================================================
    # Server Errors (500)
    # =========================================================================

    @app.errorhandler(PlaylistUpdateError)
    def handle_playlist_update_error(error: PlaylistUpdateError):
        """Handle playlist update failures."""
        logger.error(f"Playlist update error: {error}")
        return json_error_response("Failed to update playlist on Spotify.", 500)

    @app.errorhandler(ShuffleExecutionError)
    def handle_shuffle_execution_error(error: ShuffleExecutionError):
        """Handle shuffle execution failures."""
        logger.error(f"Shuffle execution error: {error}")
        return json_error_response("Failed to execute shuffle.", 500)

    @app.errorhandler(ShuffleError)
    def handle_shuffle_error(error: ShuffleError):
        """Handle general shuffle errors."""
        logger.error(f"Shuffle error: {error}")
        return json_error_response(str(error), 500)

    @app.errorhandler(StateError)
    def handle_state_error(error: StateError):
        """Handle state management errors."""
        logger.error(f"State error: {error}")
        return json_error_response("State management error.", 500)

    # =========================================================================
    # Database / Persistence Errors
    # =========================================================================

    @app.errorhandler(UserServiceError)
    def handle_user_service_error(error: UserServiceError):
        """Handle user service errors."""
        logger.error(f"User service error: {error}")
        return json_error_response(
            "User operation failed.", 500
        )

    @app.errorhandler(UserNotFoundError)
    def handle_user_not_found(error: UserNotFoundError):
        """Handle user not found errors."""
        logger.info(f"User not found: {error}")
        return json_error_response("User not found.", 404)

    @app.errorhandler(WorkshopSessionNotFoundError)
    def handle_workshop_session_not_found(
        error: WorkshopSessionNotFoundError,
    ):
        """Handle workshop session not found."""
        logger.info(f"Workshop session not found: {error}")
        return json_error_response(
            "Saved session not found.", 404
        )

    @app.errorhandler(WorkshopSessionLimitError)
    def handle_workshop_session_limit(
        error: WorkshopSessionLimitError,
    ):
        """Handle workshop session limit exceeded."""
        logger.warning(f"Workshop session limit: {error}")
        return json_error_response(str(error), 400)

    @app.errorhandler(WorkshopSessionError)
    def handle_workshop_session_error(
        error: WorkshopSessionError,
    ):
        """Handle general workshop session errors."""
        logger.error(f"Workshop session error: {error}")
        return json_error_response(
            "Workshop session operation failed.", 500
        )

    @app.errorhandler(UpstreamSourceNotFoundError)
    def handle_upstream_source_not_found(
        error: UpstreamSourceNotFoundError,
    ):
        """Handle upstream source not found."""
        logger.info(f"Upstream source not found: {error}")
        return json_error_response("Source not found.", 404)

    @app.errorhandler(UpstreamSourceError)
    def handle_upstream_source_error(
        error: UpstreamSourceError,
    ):
        """Handle general upstream source errors."""
        logger.error(f"Upstream source error: {error}")
        return json_error_response(
            "Source operation failed.", 500
        )

    # =========================================================================
    # HTTP Error Codes
    # =========================================================================

    @app.errorhandler(400)
    def handle_bad_request(error):
        """Handle 400 Bad Request."""
        return json_error_response("Bad request.", 400)

    @app.errorhandler(401)
    def handle_unauthorized(error):
        """Handle 401 Unauthorized."""
        return json_error_response("Please log in first.", 401)

    @app.errorhandler(404)
    def handle_not_found(error):
        """Handle 404 Not Found."""
        # Only return JSON for API routes
        if request.path.startswith("/api/") or request.is_json:
            return json_error_response("Resource not found.", 404)
        # Let Flask handle HTML 404 pages
        return error

    @app.errorhandler(500)
    def handle_internal_error(error):
        """Handle 500 Internal Server Error."""
        logger.error(f"Internal server error: {error}", exc_info=True)
        return json_error_response("An unexpected error occurred.", 500)

    logger.info("Global error handlers registered")
