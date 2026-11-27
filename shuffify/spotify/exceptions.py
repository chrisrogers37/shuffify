"""
Spotify module exceptions.

Provides a clean exception hierarchy for Spotify API operations.
"""


class SpotifyError(Exception):
    """Base exception for all Spotify-related errors."""

    pass


class SpotifyAuthError(SpotifyError):
    """Raised when authentication/authorization fails."""

    pass


class SpotifyTokenError(SpotifyAuthError):
    """Raised when token operations fail."""

    pass


class SpotifyTokenExpiredError(SpotifyTokenError):
    """Raised when a token has expired and cannot be refreshed."""

    pass


class SpotifyAppUnavailable(SpotifyError):
    """There is no Spotify app to act with: its client ID, secret or redirect URI is not set."""


class _TypedFailure:
    """A failure the app acts on, keeping what Spotify said about it.

    ``error`` is the OAuth error code (``invalid_client``, ``invalid_grant``)
    when Spotify sent one, and ``error_description`` its text. ``code`` is the
    error code recorded for a run that ends with this failure.
    """

    code: str

    def __init__(
        self,
        message: str,
        *,
        error: str | None = None,
        error_description: str | None = None,
    ):
        super().__init__(message)
        self.error = error
        self.error_description = error_description


class SpotifyAppRejected(_TypedFailure, SpotifyTokenExpiredError):
    """Spotify refused the app itself (OAuth ``invalid_client``), so no token can be refreshed or issued."""

    code = "spotify_app_rejected"


class SpotifyGrantRevoked(_TypedFailure, SpotifyTokenExpiredError):
    """Spotify refused the refresh token or authorization code (OAuth ``invalid_grant``)."""

    code = "spotify_grant_revoked"


class SpotifyAPIError(SpotifyError):
    """Raised when a Spotify API call fails."""

    pass


class SpotifyReadTransientError(SpotifyAPIError):
    """A safe read exhausted transport retries and may be resumed later."""


class SpotifyRefreshUnavailable(SpotifyReadTransientError):
    """A token refresh could not reach the stored refresh token just now.

    The database refused it: the row lock was held past the lock timeout, or a
    connection failed. It is transient, never "signed out": the web answers
    503 and the worker defers the run. ``retry_after`` is the number of
    seconds to wait before trying again.
    """

    def __init__(self, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class SpotifyWriteUnverifiedError(SpotifyAPIError):
    """A dispatched write may have applied; it must not be replayed."""

    pass


class SpotifyWriteRejectedError(SpotifyAPIError):
    """Spotify definitively rejected a write before applying it."""

    pass


class SpotifyRateLimitError(SpotifyAPIError):
    """Raised when rate limited by Spotify API."""

    def __init__(self, message: str, retry_after: int = None):
        super().__init__(message)
        self.retry_after = retry_after


class SpotifyQuotaExceededError(SpotifyRateLimitError):
    """Raised when Spotify's 429 says the quota, not the rate, is spent.

    Such a 429 carries ``"reason": "QUOTA_EXCEEDED"`` in its error object.
    Retrying cannot succeed until the quota resets.
    """

    pass


class SpotifyNotFoundError(SpotifyAPIError):
    """Raised when a requested resource is not found."""

    pass


class SpotifyForbiddenError(SpotifyAPIError):
    """Raised when Spotify answers 403 Forbidden.

    That is how Spotify answers a request for the items of a playlist the
    user neither owns nor collaborates on.
    """

    pass


class SpotifyUserNotAllowed(_TypedFailure, SpotifyForbiddenError):
    """A 403 saying the user isn't registered for the app, on any call.

    It is still a 403, so code that handles one handles this too; code that
    must tell it apart from a playlist 403 tests for this class first.
    """

    code = "spotify_user_not_allowed"


# The failures the app acts on, each kept through the wrappers that carry it.
SPOTIFY_FAILURES = (SpotifyAppRejected, SpotifyGrantRevoked, SpotifyUserNotAllowed)


def spotify_failure_of(error: BaseException | None):
    """The typed Spotify failure behind ``error``, or None.

    Looks at ``error`` itself, then along its ``__cause__`` and ``cause``
    links (``cause`` is how ``JobExecutionError``, ``AuthenticationError`` and
    ``SpotifyPartialBatchError`` keep theirs), and returns the first
    ``SpotifyAppRejected``, ``SpotifyGrantRevoked`` or ``SpotifyUserNotAllowed``.
    """
    current, seen = error, set()
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, SPOTIFY_FAILURES):
            return current
        current = current.__cause__ or getattr(current, "cause", None)
    return None


class SpotifyPartialBatchError(SpotifyAPIError):
    """Raised when a multi-batch playlist write fails mid-flight.

    The Spotify write helpers (`update_playlist_tracks`,
    `playlist_add_items`, `playlist_remove_items`) PUT/POST/DELETE
    in 100-track batches. A failure on batch N is caught per batch
    and re-raised as this type, so the executor rollback path can act
    on the partially written playlist.

    Attributes:
        playlist_id: Target playlist.
        method: One of 'update', 'add', 'remove'.
        completed_batches: Number of batches that succeeded
            before the failure (0 if the first batch failed).
        total_batches: Total batches attempted for this call.
        completed_uris: URIs whose write was confirmed by a
            2xx response.
        remaining_uris: URIs whose write was not attempted or
            could not be confirmed.
        cause: The underlying HTTP/Spotify error, if any.
    """

    def __init__(
        self,
        playlist_id: str,
        method: str,
        completed_batches: int,
        total_batches: int,
        completed_uris: list,
        remaining_uris: list,
        cause: "SpotifyError | None" = None,
    ):
        self.playlist_id = playlist_id
        self.method = method
        self.completed_batches = completed_batches
        self.total_batches = total_batches
        self.completed_uris = list(completed_uris)
        self.remaining_uris = list(remaining_uris)
        self.cause = cause
        super().__init__(
            f"Partial {method} on playlist {playlist_id}: "
            f"batch {completed_batches}/{total_batches} failed; "
            f"{len(self.completed_uris)} written, "
            f"{len(self.remaining_uris)} remaining. "
            f"Cause: {cause}"
        )
