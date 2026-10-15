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
