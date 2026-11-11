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
    """There are no Spotify app credentials to build a client with."""

    pass


class _OAuthDetail:
    """Keeps the OAuth ``error`` and ``error_description`` a typed failure
    was raised from, so the code that acts on it never parses a message."""

    error = None
    error_description = None

    def __init__(self, message="", *, error=None, error_description=None):
        super().__init__(message)
        self.error = error
        self.error_description = error_description


class SpotifyAppRejected(_OAuthDetail, SpotifyTokenExpiredError):
    """Spotify's token endpoint answered ``invalid_client``: it does not
    recognise the app.

    A token that cannot be refreshed, so ``except SpotifyTokenExpiredError``
    and ``except SpotifyTokenError`` still match it."""

    def __init__(self, message="", *, error="invalid_client", error_description=None):
        super().__init__(message, error=error, error_description=error_description)


class SpotifyGrantRevoked(_OAuthDetail, SpotifyTokenExpiredError):
    """Spotify's token endpoint answered ``invalid_grant``: the grant behind
    the refresh token (or the authorization code) no longer works.

    A token that cannot be refreshed, so ``except SpotifyTokenExpiredError``
    and ``except SpotifyTokenError`` still match it."""

    def __init__(self, message="", *, error="invalid_grant", error_description=None):
        super().__init__(message, error=error, error_description=error_description)


class SpotifyAPIError(SpotifyError):
    """Raised when a Spotify API call fails."""

    pass


class SpotifyReadTransientError(SpotifyAPIError):
    """A safe read exhausted transport retries and may be resumed later."""


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


class SpotifyUserNotAllowed(_OAuthDetail, SpotifyForbiddenError):
    """Spotify answered 403 and said the user is not registered for the app.

    Still a 403, so existing ``except SpotifyForbiddenError`` handling matches
    it; code that tells the two apart tests for this subclass first. A
    playlist-level 403 stays a plain ``SpotifyForbiddenError``."""


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


# The failures that say the app, the grant or the account is the problem, not
# the request. Phase 08 acts on them; nothing else parses a message for them.
TYPED_FAILURES = (SpotifyAppRejected, SpotifyGrantRevoked, SpotifyUserNotAllowed)

# The error code a failed worker run records for each.
SPOTIFY_FAILURE_CODES = (
    (SpotifyAppRejected, "spotify_app_rejected"),
    (SpotifyGrantRevoked, "spotify_grant_revoked"),
    (SpotifyUserNotAllowed, "spotify_user_not_allowed"),
)


def spotify_failure_of(error):
    """The first typed failure on ``error`` or its ``cause``/``__cause__``
    chain, or None.

    The wrappers a failure passes through on its way up (the worker's
    ``JobExecutionError``, the web's ``AuthenticationError``, a partial-batch
    error) keep it on one of those two links."""
    seen, pending = set(), [error]
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, TYPED_FAILURES):
            return current
        pending.extend((getattr(current, "cause", None), getattr(current, "__cause__", None)))
    return None


def spotify_failure_code(failure):
    """The worker's error code for a typed failure."""
    for kind, code in SPOTIFY_FAILURE_CODES:
        if isinstance(failure, kind):
            return code
    raise TypeError(f"{type(failure).__name__} is not a typed Spotify failure")
