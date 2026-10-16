"""
Global Flask error handlers.

Provides consistent error responses across all endpoints by catching
service-layer exceptions and Pydantic validation errors.
"""

import logging

from flask import current_app, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from shuffify.services import (
    AlreadyAtOriginalError,
    AuthenticationError,
    InvalidAlgorithmError,
    JobExecutionError,
    NoHistoryError,
    ParameterValidationError,
    PlaylistAccessError,
    PlaylistError,
    PlaylistNotFoundError,
    PlaylistUpdateError,
    ScheduleError,
    ScheduleNotFoundError,
    ShuffleError,
    ShuffleExecutionError,
    StateError,
    TokenValidationError,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
    UserNotFoundError,
    UserServiceError,
    WorkshopSessionError,
    WorkshopSessionLimitError,
    WorkshopSessionNotFoundError,
)
from shuffify.services.api_response import ERROR_CODES, error_payload
from shuffify.services.live_action_context_service import inspection_rejected
from shuffify.services.live_operation_service import (
    DIRECT_ENDPOINT_KINDS,
    LIVE_ENDPOINT_KINDS,
    direct_rejected_result,
    rejected_result,
)
from shuffify.services.live_review_context import context_rejected
from shuffify.services.workshop_finalize_service import WorkshopFinalizeResult
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyError,
    SpotifyNotFoundError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyTokenExpiredError,
)

logger = logging.getLogger(__name__)


def json_error_response(message: str, status_code: int, category: str = "error", **extra):
    """Create a standardized JSON error response."""
    response = jsonify(error_payload(message, status_code, category=category, **extra))
    response.headers["Cache-Control"] = "no-store"
    return response, status_code


def is_api_request():
    """Include unresolved API URLs, whose request has no Blueprint or endpoint."""
    return request.path == "/api" or request.path.startswith("/api/")


def normalize_api_response(response):
    """Apply the API envelope and cache policy to normal and early responses."""
    if not is_api_request():
        return response
    payload = response.get_json(silent=True)
    if isinstance(payload, list):
        payload = {"data": payload}
    elif not isinstance(payload, dict):
        if request.method == "OPTIONS" and response.status_code < 400:
            payload = {}
        else:
            if response.status_code < 400:
                logger.error("API endpoint returned a non-JSON body: %s", request.endpoint)
                response.status_code = 500
            payload = error_payload("The request could not be completed.", response.status_code)
    payload.setdefault("success", response.status_code < 400)
    if not payload["success"]:
        payload.setdefault("error_code", ERROR_CODES.get(response.status_code, "request_failed"))
        payload.setdefault("message", "The request could not be completed.")
        payload.setdefault("category", "error")
    response.set_data(current_app.json.dumps(payload))
    response.mimetype = "application/json"
    response.headers["Cache-Control"] = "no-store"
    return response


# =============================================================================
# Pydantic Validation Errors (400)
# =============================================================================


def format_validation_error(error: ValidationError) -> str:
    """Render every field error in a ValidationError as one message.

    All of them, not just the first: a multi-field form that reports one
    problem per submit makes the user discover the rest one round trip at a
    time. This is the single formatter for Pydantic failures wherever they
    are caught -- the global handler below and the per-route `validate_json`
    in `shuffify.routes` both come through here, so the two paths cannot
    drift into describing the same error differently.
    """
    errors_list = []
    for err in error.errors():
        field = ".".join(str(loc) for loc in err["loc"])
        msg = err["msg"]
        errors_list.append(f"{field}: {msg}")

    return "; ".join(errors_list) if errors_list else "Validation failed"


def handle_validation_error(error: ValidationError):
    """Handle Pydantic validation errors."""
    message = format_validation_error(error)
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


def handle_playlist_access_error(error: PlaylistAccessError):
    """Handle a playlist the user cannot edit or read."""
    logger.warning(f"Playlist access denied: {error}")
    return json_error_response(str(error), 403)


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
    return json_error_response("User operation failed.", 500)


def handle_user_not_found(error: UserNotFoundError):
    """Handle user not found errors."""
    logger.info(f"User not found: {error}")
    return json_error_response("User not found.", 404)


def handle_workshop_session_not_found(
    error: WorkshopSessionNotFoundError,
):
    """Handle workshop session not found."""
    logger.info(f"Workshop session not found: {error}")
    return json_error_response("Saved session not found.", 404)


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
    return json_error_response("Workshop session operation failed.", 500)


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
    return json_error_response("Source operation failed.", 500)


# =============================================================================
# Schedule Errors
# =============================================================================


def handle_schedule_not_found(
    error: ScheduleNotFoundError,
):
    """Handle schedule not found errors."""
    logger.info(f"Schedule not found: {error}")
    return json_error_response("Schedule not found.", 404)


def handle_schedule_error(error: ScheduleError):
    """Handle general schedule errors."""
    logger.error(f"Schedule error: {error}")
    return json_error_response(str(error), 500)


def handle_job_execution_error(
    error: JobExecutionError,
):
    """Handle job execution failures."""
    logger.error(f"Job execution error: {error}")
    return json_error_response(f"Job execution failed: {error}", 500)


# =============================================================================
# Spotify API Errors (catch-all for exceptions escaping service layer)
# =============================================================================


def handle_spotify_token_expired(
    error: SpotifyTokenExpiredError,
):
    """Handle expired Spotify tokens."""
    logger.warning(f"Spotify token expired: {error}")
    return json_error_response("Session expired. Please log in again.", 401)


def handle_spotify_rate_limit(
    error: SpotifyRateLimitError,
):
    """Handle Spotify API rate limiting."""
    logger.warning(f"Spotify rate limit: {error}")
    response, status = json_error_response(
        "Spotify is rate limiting requests. Please wait a moment and try again.",
        429,
    )
    if hasattr(error, "retry_after") and error.retry_after:
        response.headers["Retry-After"] = str(error.retry_after)
    return response, status


def handle_spotify_quota_exceeded(
    error: SpotifyQuotaExceededError,
):
    """Handle Spotify's quota 429, which no short wait will clear."""
    logger.warning(f"Spotify quota exceeded: {error}")
    response, status = json_error_response(
        "Shuffify has used up its Spotify request quota for now. Please try again later.",
        429,
    )
    if error.retry_after:
        response.headers["Retry-After"] = str(error.retry_after)
    return response, status


def handle_spotify_not_found(
    error: SpotifyNotFoundError,
):
    """Handle Spotify resource not found."""
    logger.info(f"Spotify resource not found: {error}")
    return json_error_response("Spotify resource not found.", 404)


def handle_spotify_auth_error(
    error: SpotifyAuthError,
):
    """Handle Spotify authentication errors."""
    logger.warning(f"Spotify auth error: {error}")
    return json_error_response(
        "Spotify authentication failed. Please log in again.",
        401,
    )


def handle_spotify_api_error(
    error: SpotifyAPIError,
):
    """Handle general Spotify API errors."""
    logger.error(f"Spotify API error: {error}")
    return json_error_response("Spotify API error. Please try again.", 500)


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


# Review endpoints and the kind each reports before its body can be read.
_REVIEW_ENDPOINT_KINDS = {
    "api.live_context": "live_context",
    "api.run_schedule_context": "schedule",
    "api.snapshot_restore_context": "snapshot_restore",
}


def _review_rejection(code, status_code, review_message, check_message, *, read_body):
    """The reply for a review or check refused before its route ran.

    A review that never loaded changed nothing. A check that never ran
    observed nothing, so earlier results stay unverified, never no-effect.
    """
    if request.endpoint in _REVIEW_ENDPOINT_KINDS:
        payload, status_code = context_rejected(
            code, status_code, review_message, kind=_REVIEW_ENDPOINT_KINDS[request.endpoint]
        )
    elif request.endpoint == "api.inspect_live":
        body = request.get_json(silent=True) if read_body else None
        payload, status_code = inspection_rejected(body, code, status_code, check_message)
    else:
        return None
    return jsonify(payload), status_code


def handle_csrf_error(error):
    """Handle CSRF validation failures."""
    logger.warning(
        "CSRF validation failed on %s %s: %s",
        request.method,
        request.path,
        error,
    )
    # Flask-WTF rejects these requests before their route-level preflight
    # callback runs. Preserve the source contract without dispatching a read
    # or write; both raid URLs resolve to the same registered endpoint.
    count_key = {
        "api.pending_raids_finalize": "promoted_count",
        "api.archive_tracks": "archived_count",
        "api.finalize_restore": "restored_count",
    }.get(request.endpoint)
    if count_key:
        body = request.get_json(silent=True)
        inspect_only = isinstance(body, dict) and body.get("inspect_only") is True
        message = (
            "Session verification failed. This check did not run, so earlier "
            "source changes remain unverified. "
            if inspect_only else
            "Session verification failed. This attempt applied no source changes. "
        ) + (
            "Reloading loses the recovery details kept on this page; "
            "earlier Spotify changes remain."
        )
        result = WorkshopFinalizeResult.request_rejected(
            body, message, 400, count_key,
            error_code="session_verification_expired",
        )
        return jsonify(result.to_dict()), result.http_status
    review = _review_rejection(
        "session_verification_expired", 400,
        "Session verification failed. This review did not load and nothing was changed. "
        "Reloading loses the recovery details kept on this page; earlier Spotify changes remain.",
        "Session verification failed. This check did not run, so earlier results remain "
        "unverified. Reloading loses the recovery details kept on this page; earlier "
        "Spotify changes remain.",
        read_body=True,
    )
    if review:
        return review
    # CSRF runs before authorization, so no resource identity or schedule
    # kind has been established. Direct actions have no execution receipt.
    direct_kind = DIRECT_ENDPOINT_KINDS.get(request.endpoint)
    live_kind = direct_kind or LIVE_ENDPOINT_KINDS.get(request.endpoint)
    if live_kind:
        reject = direct_rejected_result if direct_kind else rejected_result
        result = reject(
            live_kind,
            error_code="session_verification_expired",
            message=(
                "Session verification failed. This attempt did not run. "
                "Reloading loses the recovery details kept on this page; "
                "earlier Spotify changes remain."
            ),
        )
        status_code = result.pop("http_status")
        return jsonify(success=False, category="error", **result), status_code
    message = (
        "Your request could not be verified. "
        "Please refresh the page and try again."
    )
    return json_error_response(message, 400)


def handle_bad_request(error):
    """Handle 400 Bad Request."""
    return json_error_response("Bad request.", 400)


def handle_request_too_large(error):
    """Handle 413 Content Too Large: a body over the request limits."""
    # INFO, not WARNING: Sentry records WARNING and up, and anyone can send one.
    logger.info(
        "Request too large: %s %s (Content-Length %s)",
        request.method,
        request.path,
        request.content_length,
    )
    # The body was refused unread, so the inspection kind cannot be known.
    review = _review_rejection(
        "request_too_large", 413,
        "That review request is too large. Nothing was changed.",
        "That check is too large to send. Earlier results remain unverified.",
        read_body=False,
    )
    if review:
        return review
    return json_error_response("That request is too large.", 413)


def handle_unauthorized(error):
    """Handle 401 Unauthorized."""
    return json_error_response("Please log in first.", 401)


def handle_not_found(error):
    """Handle 404 Not Found without a server-rendered page."""
    return json_error_response("Resource not found.", 404)


def handle_http_error(error):
    """Preserve HTTP status and protocol headers while formatting API failures."""
    response, status = json_error_response(error.description, error.code)
    for key, value in error.get_headers():
        if key.lower() not in ("content-type", "content-length"):
            response.headers.add(key, value)
    return response, status


def handle_unexpected_error(error):
    """Keep unexpected backend failures in the authored JSON envelope."""
    return handle_internal_error(error)


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
    return json_error_response("An unexpected error occurred.", 500)


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
    retry_after = (
        error.description
        if isinstance(error.description, str) and error.description.isdigit()
        else None
    )
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
    from flask_wtf.csrf import CSRFError

    handlers = [
        (CSRFError, handle_csrf_error),
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
        (PlaylistAccessError, handle_playlist_access_error),
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
        (ScheduleError, handle_schedule_error),
        (JobExecutionError, handle_job_execution_error),
        (SpotifyTokenExpiredError, handle_spotify_token_expired),
        (SpotifyQuotaExceededError, handle_spotify_quota_exceeded),
        (SpotifyRateLimitError, handle_spotify_rate_limit),
        (SpotifyNotFoundError, handle_spotify_not_found),
        (SpotifyAuthError, handle_spotify_auth_error),
        (SpotifyAPIError, handle_spotify_api_error),
        (SpotifyError, handle_spotify_error),
        (400, handle_bad_request),
        (401, handle_unauthorized),
        (404, handle_not_found),
        (413, handle_request_too_large),
        (500, handle_internal_error),
        (HTTPException, handle_http_error),
        (Exception, handle_unexpected_error),
        (429, handle_rate_limit_exceeded),
    ]

    for exc_or_code, handler in handlers:
        app.errorhandler(exc_or_code)(handler)

    app.after_request(normalize_api_response)

    logger.info("Global error handlers registered")
