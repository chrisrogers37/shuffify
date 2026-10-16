"""Response payloads shared by request handlers and application services."""


ERROR_CODES = {
    400: "invalid_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "request_too_large",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def error_payload(message, status_code, *, error_code=None, category="error", **extra):
    """Build a failed response without discarding operation-specific fields."""
    return {
        "success": False,
        "error_code": error_code or ERROR_CODES.get(status_code, "request_failed"),
        "message": message,
        "category": category,
        **extra,
    }


def busy_error(message="A routine is using this playlist. Try again.", **extra):
    """Report a playlist lock conflict with a consistent retryable envelope."""
    return error_payload(message, 409, error_code="playlist_busy", retryable=True, **extra), 409
