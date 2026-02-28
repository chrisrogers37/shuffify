"""
Lightweight HTTP client for the Spotify Web API.

Wraps requests.Session with automatic token management, retry logic,
rate limit handling, and pagination support.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

import requests
from requests.exceptions import ConnectionError, RequestException, Timeout

from .exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
    SpotifyRateLimitError,
    SpotifyTokenExpiredError,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.spotify.com/v1"
MAX_RETRIES = 4
BASE_DELAY = 2  # seconds
MAX_DELAY = 16  # seconds


def _calculate_backoff_delay(attempt: int) -> float:
    """Calculate exponential backoff delay, capped at MAX_DELAY."""
    return min(BASE_DELAY * (2 ** attempt), MAX_DELAY)


class SpotifyHTTPClient:
    """
    HTTP client for Spotify Web API requests.

    Handles authorization headers, token refresh on 401, rate limit
    backoff on 429, and retries on transient errors (5xx, network).
    """

    def __init__(
        self,
        access_token: str,
        on_token_refresh: Optional[Callable[[], str]] = None,
    ):
        """
        Initialize the HTTP client.

        Args:
            access_token: Bearer token for API requests.
            on_token_refresh: Optional callback that returns a fresh
                access token. Called on 401 responses.
        """
        self._access_token = access_token
        self._on_token_refresh = on_token_refresh
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        })

    def update_token(self, access_token: str) -> None:
        """Update the bearer token for future requests."""
        self._access_token = access_token
        self._session.headers["Authorization"] = f"Bearer {access_token}"

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    # -----------------------------------------------------------------
    # Public HTTP methods
    # -----------------------------------------------------------------

    def get(self, path: str, params: Optional[Dict] = None) -> Any:
        """Send a GET request."""
        return self._request("GET", path, params=params)

    def post(self, path: str, json: Any = None) -> Any:
        """Send a POST request."""
        return self._request("POST", path, json=json)

    def put(self, path: str, json: Any = None) -> Any:
        """Send a PUT request."""
        return self._request("PUT", path, json=json)

    def delete(self, path: str, json: Any = None) -> Any:
        """Send a DELETE request."""
        return self._request("DELETE", path, json=json)

    def get_all_pages(
        self,
        path: str,
        params: Optional[Dict] = None,
        items_key: str = "items",
    ) -> List[Dict]:
        """
        Fetch all pages of a paginated endpoint.

        Follows the ``next`` URL in each response until exhausted.

        Args:
            path: Initial API path (e.g. ``/me/playlists``).
            params: Optional query parameters for the first request.
            items_key: Key containing the list items (default ``items``).

        Returns:
            Concatenated list of all items across pages.
        """
        all_items: List[Dict] = []
        url: Optional[str] = f"{BASE_URL}{path}"

        # First request uses params; subsequent requests use the full
        # ``next`` URL returned by Spotify (which embeds its own params).
        is_first = True

        while url:
            if is_first:
                data = self._request("GET", path, params=params)
                is_first = False
            else:
                data = self._request_url("GET", url)

            if data and items_key in data:
                all_items.extend(data[items_key])

            url = data.get("next") if data else None

        return all_items

    # -----------------------------------------------------------------
    # Internal request handling
    # -----------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json: Any = None,
    ) -> Any:
        """Make a request to a relative API path."""
        url = f"{BASE_URL}{path}"
        return self._request_url(method, url, params=params, json=json)

    def _request_url(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        json: Any = None,
    ) -> Any:
        """
        Execute an HTTP request with retry and error handling.

        Retries on 429 (rate limit), 5xx, and network errors.
        On 401, attempts a single token refresh before failing.
        """
        token_refreshed = False

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._session.request(
                    method, url, params=params, json=json, timeout=30,
                )

                # --- Success ---
                if response.status_code == 204:
                    return None
                if response.ok:
                    return response.json()

                # --- 401 Unauthorized: try token refresh once ---
                if response.status_code == 401:
                    if not token_refreshed and self._on_token_refresh:
                        logger.info("401 received, attempting token refresh")
                        try:
                            new_token = self._on_token_refresh()
                            self.update_token(new_token)
                            token_refreshed = True
                            continue
                        except Exception as e:
                            logger.error("Token refresh failed: %s", e)
                    raise SpotifyTokenExpiredError(
                        "Token expired or invalid"
                    )

                # --- 404 Not Found ---
                if response.status_code == 404:
                    raise SpotifyNotFoundError(
                        f"Resource not found: {url}"
                    )

                # --- 429 Rate Limited ---
                if response.status_code == 429:
                    if attempt >= MAX_RETRIES:
                        retry_after = int(
                            response.headers.get("Retry-After", 60)
                        )
                        raise SpotifyRateLimitError(
                            f"Rate limited after {MAX_RETRIES + 1} attempts",
                            retry_after=retry_after,
                        )
                    retry_after = int(
                        response.headers.get("Retry-After", 1)
                    )
                    delay = max(
                        retry_after,
                        _calculate_backoff_delay(attempt),
                    )
                    logger.warning(
                        "Rate limited (429), retry %d/%d in %ss",
                        attempt + 1, MAX_RETRIES + 1, delay,
                    )
                    time.sleep(delay)
                    continue

                # --- 5xx Server Error ---
                if response.status_code >= 500:
                    if attempt >= MAX_RETRIES:
                        raise SpotifyAPIError(
                            f"Server error {response.status_code} "
                            f"after {MAX_RETRIES + 1} attempts"
                        )
                    delay = _calculate_backoff_delay(attempt)
                    logger.warning(
                        "Server error %d, retry %d/%d in %ss",
                        response.status_code,
                        attempt + 1, MAX_RETRIES + 1, delay,
                    )
                    time.sleep(delay)
                    continue

                # --- Other client errors (400, 403, etc.) ---
                try:
                    body = response.json()
                    msg = body.get("error", {}).get(
                        "message", response.text
                    )
                except Exception:
                    msg = response.text
                raise SpotifyAPIError(
                    f"API error {response.status_code}: {msg}"
                )

            except (ConnectionError, Timeout, RequestException) as e:
                if attempt >= MAX_RETRIES:
                    raise SpotifyAPIError(
                        f"Network error after {MAX_RETRIES + 1} "
                        f"attempts: {e}"
                    )
                delay = _calculate_backoff_delay(attempt)
                logger.warning(
                    "Network error, retry %d/%d in %ss: %s",
                    attempt + 1, MAX_RETRIES + 1, delay, e,
                )
                time.sleep(delay)

            except (
                SpotifyNotFoundError,
                SpotifyTokenExpiredError,
                SpotifyRateLimitError,
                SpotifyAPIError,
            ):
                raise

        # Should not reach here
        raise SpotifyAPIError(
            f"Request failed after {MAX_RETRIES + 1} attempts"
        )
