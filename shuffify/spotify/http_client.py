"""
Lightweight HTTP client for the Spotify Web API.

Wraps requests.Session with automatic token management, retry logic,
rate limit handling, and pagination support.
"""

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, RequestException, Timeout

from .deadline import SpotifyDeadlineError, bounded_sleep, check_deadline, remaining_timeout
from .endpoints import API_BASE_URL
from .exceptions import (
    SpotifyAPIError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyReadTransientError,
    SpotifyTokenExpiredError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)
from .observation import current_observer
from .transport import DeadlineHTTPAdapter

logger = logging.getLogger(__name__)

BASE_URL = API_BASE_URL
MAX_RETRIES = 4
BASE_DELAY = 2  # seconds
MAX_DELAY = 16  # seconds
REQUEST_TIMEOUT = 30  # seconds, for each attempt
# Spotify counts calls in a rolling 30-second window
# (https://developer.spotify.com/documentation/web-api/concepts/rate-limits).
# A 429 asking for a longer wait is answered at once rather than slept out:
# no request can wait that long, and retrying sooner is still refused.
MAX_RETRY_AFTER = 30  # seconds

# Connection-pool sizing for the process-wide adapter. One pool is enough --
# every request targets the single Spotify API host -- but it has to hold
# enough sockets for all concurrent workers in the process.
POOL_CONNECTIONS = 10
POOL_MAXSIZE = 20

_adapter_lock = threading.Lock()
_shared_adapter: Optional[HTTPAdapter] = None
_shared_adapter_pid: Optional[int] = None


def _calculate_backoff_delay(attempt: int) -> float:
    """Calculate exponential backoff delay, capped at MAX_DELAY."""
    return min(BASE_DELAY * (2**attempt), MAX_DELAY)


def _retry_after(response) -> Optional[int]:
    """A 429's Retry-After in seconds, which is how Spotify documents it;
    None when it is missing or anything else."""
    try:
        return max(0, int(response.headers.get("Retry-After")))
    except (TypeError, ValueError):
        return None


def _quota_exceeded(response) -> bool:
    """Whether a 429 is Spotify's quota one:
    ``{"error": {"status": 429, "message": ..., "reason": "QUOTA_EXCEEDED"}}``."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return False
    return isinstance(error, dict) and error.get("reason") == "QUOTA_EXCEEDED"


def get_shared_adapter() -> HTTPAdapter:
    """Return this process's shared connection-pool adapter.

    The bearer token lives on the ``Session``, so sessions cannot be shared
    between users without serving one user's token on another's request. The
    connection pool underneath is user-agnostic, so that is what gets shared:
    every client keeps its own ``Session`` (and therefore its own auth), and
    they all draw sockets from one pool.

    The pool is rebuilt whenever the owning PID changes. Sockets do not
    survive ``fork()`` safely -- two processes drawing from one inherited pool
    can interleave writes on the same connection -- so a pool inherited from a
    parent process, as under a preloading Gunicorn, is precisely what a worker
    must not keep.
    """
    global _shared_adapter, _shared_adapter_pid

    pid = os.getpid()
    with _adapter_lock:
        if _shared_adapter is None or _shared_adapter_pid != pid:
            _shared_adapter = DeadlineHTTPAdapter(
                pool_connections=POOL_CONNECTIONS,
                pool_maxsize=POOL_MAXSIZE,
            )
            _shared_adapter_pid = pid
        return _shared_adapter


class SpotifyHTTPClient:
    """
    HTTP client for Spotify Web API requests.

    Handles authorization headers, token refresh on 401, rate limit
    backoff on 429, and read retries on transient errors (5xx, network).
    Writes with uncertain outcomes are never automatically replayed.
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
        self._session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
        )
        adapter = get_shared_adapter()
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def update_token(self, access_token: str) -> None:
        """Update the bearer token for future requests."""
        self._access_token = access_token
        self._session.headers["Authorization"] = f"Bearer {access_token}"

    def close(self) -> None:
        """Release this client's session, leaving the shared pool intact.

        ``Session.close()`` closes every adapter mounted on it, so the shared
        adapter is detached first -- otherwise one client going away would
        drop the connection pool out from under every other client in the
        process.
        """
        self._session.adapters.clear()
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

    def _perform_request(self, method, url, **kwargs):
        """Fence and observe each actual mutation attempt without observing reads."""
        observer = current_observer() if method != "GET" else None
        if observer is None:
            return self._session.request(method, url, **kwargs)
        path = urlsplit(url).path.removeprefix(urlsplit(BASE_URL).path.rstrip("/"))
        ticket = observer.before_http(method, path, kwargs.get("json"))
        try:
            response = self._session.request(method, url, **kwargs)
        except Exception as error:
            observer.http_failed(ticket, error)
            raise
        observer.after_http(ticket, response)
        return response

    def _request_url(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        json: Any = None,
    ) -> Any:
        """
        Execute an HTTP request with retry and error handling.

        Retries on 429 (rate limit), and on 5xx/network errors for reads.
        On 401, attempts a single token refresh before failing.
        Uncertain write outcomes require the caller to reconcile state.
        """
        token_refreshed = False
        attempt = 0

        while attempt <= MAX_RETRIES:
            timeout = remaining_timeout(REQUEST_TIMEOUT)
            try:
                response = self._perform_request(
                    method,
                    url,
                    params=params,
                    json=json,
                    timeout=timeout,
                )

                status = response.status_code
                if method == "GET" and status != 429:
                    check_deadline()

                # --- Success ---
                if 200 <= status < 300:
                    # Nothing to parse: a 204, or a write Spotify documents
                    # without a body (Change Playlist Details answers 200).
                    if status == 204 or (method != "GET" and not response.content):
                        return None
                    try:
                        data = response.json()
                        if method == "GET":
                            check_deadline()
                        return data
                    except ValueError:
                        # Empty or not JSON where data was due. A write is
                        # not retried: Spotify may already have applied it.
                        # A read is, like a 5xx -- never returned as None,
                        # which would end paging early and hand a shuffle a
                        # truncated playlist to write back.
                        if method != "GET":
                            raise SpotifyWriteUnverifiedError(
                                f"Spotify answered {status} with a body that is not JSON"
                            )
                        if attempt >= MAX_RETRIES:
                            raise SpotifyReadTransientError(
                                f"Spotify answered {status} with an unreadable body "
                                f"after {MAX_RETRIES + 1} attempts"
                            )
                        delay = _calculate_backoff_delay(attempt)
                        logger.warning(
                            "Unreadable %d body, retry %d/%d in %ss",
                            status,
                            attempt + 1,
                            MAX_RETRIES + 1,
                            delay,
                        )
                        bounded_sleep(delay, sleep=time.sleep)
                        attempt += 1
                        continue

                # --- 401 Unauthorized: try token refresh once ---
                if response.status_code == 401:
                    if not token_refreshed and self._on_token_refresh:
                        logger.info("401 received, attempting token refresh")
                        try:
                            new_token = self._on_token_refresh()
                            self.update_token(new_token)
                            token_refreshed = True
                            attempt = 0  # refresh restarts attempts, never the shared deadline
                            continue
                        except (SpotifyDeadlineError, SpotifyRateLimitError, SpotifyReadTransientError):
                            raise
                        except Exception as e:
                            check_deadline()
                            logger.error("Token refresh failed: %s", e)
                    raise SpotifyTokenExpiredError("Token expired or invalid")

                # --- 404 Not Found ---
                if response.status_code == 404:
                    raise SpotifyNotFoundError(f"Resource not found: {url}")

                # --- 429 Rate Limited ---
                if response.status_code == 429:
                    retry_after = _retry_after(response)
                    if _quota_exceeded(response):
                        raise SpotifyQuotaExceededError(
                            "Spotify quota exceeded", retry_after=retry_after
                        )
                    if retry_after is not None and retry_after > MAX_RETRY_AFTER:
                        raise SpotifyRateLimitError(
                            f"Rate limited for {retry_after}s",
                            retry_after=retry_after,
                        )
                    if attempt >= MAX_RETRIES:
                        raise SpotifyRateLimitError(
                            f"Rate limited after {MAX_RETRIES + 1} attempts",
                            retry_after=60 if retry_after is None else retry_after,
                        )
                    delay = max(
                        retry_after or 0,
                        _calculate_backoff_delay(attempt),
                    )
                    logger.warning(
                        "Rate limited (429), retry %d/%d in %ss",
                        attempt + 1,
                        MAX_RETRIES + 1,
                        delay,
                    )
                    try:
                        bounded_sleep(delay, sleep=time.sleep)
                    except SpotifyDeadlineError as error:
                        raise SpotifyRateLimitError(
                            "Spotify rate limit exceeds the remaining request deadline.",
                            retry_after=retry_after,
                        ) from error
                    attempt += 1
                    continue

                # --- 5xx Server Error ---
                if response.status_code >= 500:
                    if method != "GET":
                        raise SpotifyWriteUnverifiedError(
                            f"Spotify write returned {response.status_code}; "
                            "its outcome is unverified"
                        )
                    if attempt >= MAX_RETRIES:
                        raise SpotifyReadTransientError(
                            f"Server error {response.status_code} "
                            f"after {MAX_RETRIES + 1} attempts"
                        )
                    delay = _calculate_backoff_delay(attempt)
                    logger.warning(
                        "Server error %d, retry %d/%d in %ss",
                        response.status_code,
                        attempt + 1,
                        MAX_RETRIES + 1,
                        delay,
                    )
                    bounded_sleep(delay, sleep=time.sleep)
                    attempt += 1
                    continue

                # --- Other client errors (400, 403, etc.) ---
                try:
                    body = response.json()
                    msg = body.get("error", {}).get("message", response.text)
                except Exception:
                    msg = response.text
                if response.status_code == 403:
                    error = SpotifyForbiddenError
                elif method != "GET" and 400 <= response.status_code < 500:
                    error = SpotifyWriteRejectedError
                elif method != "GET":
                    error = SpotifyWriteUnverifiedError
                else:
                    error = SpotifyAPIError
                raise error(f"API error {response.status_code}: {msg}")

            except (ConnectionError, Timeout, RequestException) as e:
                if method != "GET":
                    raise SpotifyWriteUnverifiedError(
                        "Spotify write lost its response; its outcome is unverified"
                    ) from e
                if attempt >= MAX_RETRIES:
                    raise SpotifyReadTransientError(
                        f"Network error after {MAX_RETRIES + 1} attempts: {e}"
                    )
                delay = _calculate_backoff_delay(attempt)
                logger.warning(
                    "Network error, retry %d/%d in %ss: %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    delay,
                    e,
                )
                bounded_sleep(delay, sleep=time.sleep)
                attempt += 1

            except (
                SpotifyNotFoundError,
                SpotifyTokenExpiredError,
                SpotifyRateLimitError,
                SpotifyAPIError,
            ):
                raise

        # Should not reach here
        raise SpotifyAPIError(f"Request failed after {MAX_RETRIES + 1} attempts")
