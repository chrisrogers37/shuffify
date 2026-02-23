"""
Spotify API error handling and retry logic.

Contains retry constants, backoff calculation, error classification,
and the api_error_handler decorator used by SpotifyAPI methods.
"""

import logging
import time
from functools import wraps
from typing import Callable

import spotipy

from requests.exceptions import RequestException, ConnectionError, Timeout

from .exceptions import (
    SpotifyAPIError,
    SpotifyRateLimitError,
    SpotifyNotFoundError,
    SpotifyTokenExpiredError,
)

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 4
BASE_DELAY = 2  # seconds
MAX_DELAY = 16  # seconds


def _calculate_backoff_delay(attempt: int, base_delay: float = BASE_DELAY) -> float:
    """
    Calculate exponential backoff delay with jitter.

    Args:
        attempt: The current retry attempt (0-indexed).
        base_delay: Base delay in seconds.

    Returns:
        Delay in seconds, capped at MAX_DELAY.
    """
    delay = min(base_delay * (2**attempt), MAX_DELAY)
    return delay


def _classify_error(exception: Exception) -> str:
    """
    Classify an exception into an error category.

    Returns one of: 'not_found', 'token_expired',
    'rate_limited', 'server_error', 'network_error',
    'client_error', 'unexpected'.
    """
    if isinstance(exception, spotipy.SpotifyException):
        if exception.http_status == 404:
            return "not_found"
        elif exception.http_status == 401:
            return "token_expired"
        elif exception.http_status == 429:
            return "rate_limited"
        elif exception.http_status in (500, 502, 503, 504):
            return "server_error"
        else:
            return "client_error"
    elif isinstance(
        exception,
        (ConnectionError, Timeout, RequestException),
    ):
        return "network_error"
    else:
        return "unexpected"


def _should_retry(error_category: str) -> bool:
    """Determine if an error category is retryable."""
    return error_category in (
        "rate_limited", "server_error", "network_error",
    )


def _get_retry_delay(
    exception: Exception,
    error_category: str,
    attempt: int,
) -> float:
    """
    Calculate retry delay based on error type and attempt.

    For rate limits, respects the Retry-After header.
    For other retryable errors, uses exponential backoff.
    """
    if (
        error_category == "rate_limited"
        and isinstance(exception, spotipy.SpotifyException)
    ):
        retry_after = (
            exception.headers.get("Retry-After", 60)
            if exception.headers
            else 60
        )
        return max(
            int(retry_after),
            _calculate_backoff_delay(attempt),
        )
    return _calculate_backoff_delay(attempt)


def _raise_final_error(
    exception: Exception,
    error_category: str,
    func_name: str,
) -> None:
    """
    Raise the appropriate custom exception after retries
    are exhausted or for non-retryable errors.
    """
    if error_category == "not_found":
        msg = getattr(exception, "msg", str(exception))
        raise SpotifyNotFoundError(
            f"Resource not found: {msg}"
        )
    elif error_category == "token_expired":
        msg = getattr(exception, "msg", str(exception))
        raise SpotifyTokenExpiredError(
            f"Token expired or invalid: {msg}"
        )
    elif error_category == "rate_limited":
        retry_after = 60
        if (
            isinstance(exception, spotipy.SpotifyException)
            and exception.headers
        ):
            retry_after = int(
                exception.headers.get("Retry-After", 60)
            )
        msg = getattr(exception, "msg", str(exception))
        raise SpotifyRateLimitError(
            f"Rate limited after {MAX_RETRIES + 1} "
            f"attempts: {msg}",
            retry_after=retry_after,
        )
    elif error_category == "server_error":
        msg = getattr(exception, "msg", str(exception))
        logger.error(
            "Spotify API error in %s after %d attempts: %s",
            func_name, MAX_RETRIES + 1, exception,
        )
        raise SpotifyAPIError(
            f"API error after retries: {msg}"
        )
    elif error_category == "network_error":
        logger.error(
            "Network error in %s after %d attempts: %s",
            func_name, MAX_RETRIES + 1, exception,
            exc_info=True,
        )
        raise SpotifyAPIError(
            f"Network error after retries: {exception}"
        )
    elif error_category == "client_error":
        msg = getattr(exception, "msg", str(exception))
        logger.error(
            "Spotify API error in %s: %s",
            func_name, exception,
        )
        raise SpotifyAPIError(f"API error: {msg}")
    else:
        logger.error(
            "Unexpected error in %s: %s",
            func_name, exception, exc_info=True,
        )
        raise SpotifyAPIError(
            f"Unexpected error: {exception}"
        )


def api_error_handler(func: Callable) -> Callable:
    """
    Decorator for handling Spotify API errors with automatic retry.

    Catches spotipy exceptions and converts them to our exception types.
    Implements exponential backoff for rate limits and transient errors.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exception = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                category = _classify_error(e)

                if (
                    not _should_retry(category)
                    or attempt >= MAX_RETRIES
                ):
                    _raise_final_error(
                        e, category, func.__name__
                    )

                delay = _get_retry_delay(
                    e, category, attempt
                )
                logger.warning(
                    "%s in %s, attempt %d/%d. "
                    "Retrying in %ss",
                    category, func.__name__,
                    attempt + 1, MAX_RETRIES + 1,
                    delay,
                )
                time.sleep(delay)

        # Should not reach here, but handle it just in case
        if last_exception:
            raise SpotifyAPIError(
                f"Failed after {MAX_RETRIES + 1} "
                f"attempts: {last_exception}"
            )

    return wrapper
