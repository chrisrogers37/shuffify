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


class SpotifyRateLimitError(SpotifyAPIError):
    """Raised when rate limited by Spotify API."""

    def __init__(self, message: str, retry_after: int = None):
        super().__init__(message)
        self.retry_after = retry_after


class SpotifyNotFoundError(SpotifyAPIError):
    """Raised when a requested resource is not found."""
    pass
