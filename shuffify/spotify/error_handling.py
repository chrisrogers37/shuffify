"""
Spotify API error handling.

Provides the api_error_handler decorator used by SpotifyAPI methods
to catch and convert unexpected exceptions.

Note: HTTP-level error handling (retries, rate limits, token refresh)
is handled by SpotifyHTTPClient. This module provides a thin wrapper
for unexpected exceptions only.
"""

import logging
from functools import wraps
from typing import Callable

from .exceptions import (
    SpotifyAPIError,
    SpotifyRateLimitError,
    SpotifyNotFoundError,
    SpotifyTokenExpiredError,
)

logger = logging.getLogger(__name__)


def api_error_handler(func: Callable) -> Callable:
    """
    Decorator for handling unexpected errors in SpotifyAPI methods.

    HTTP-level errors (401, 404, 429, 5xx) and retries are handled by
    SpotifyHTTPClient. This decorator catches any remaining unexpected
    exceptions and converts them to SpotifyAPIError.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (
            SpotifyAPIError,
            SpotifyNotFoundError,
            SpotifyRateLimitError,
            SpotifyTokenExpiredError,
        ):
            raise
        except Exception as e:
            logger.error(
                "Unexpected error in %s: %s",
                func.__name__, e, exc_info=True,
            )
            raise SpotifyAPIError(
                f"Unexpected error: {e}"
            )

    return wrapper
