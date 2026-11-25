"""
Spotify credentials management.

Provides a dataclass for Spotify OAuth credentials that does not depend on
Flask, and ``credentials_for``, the one place any code gets them.
"""

from dataclasses import dataclass, field

from .exceptions import SpotifyAppUnavailable


@dataclass(frozen=True)
class SpotifyCredentials:
    """
    Immutable container for Spotify OAuth credentials.

    This class encapsulates all credentials needed for Spotify OAuth,
    enabling dependency injection without depending on Flask.

    Attributes:
        client_id: The Spotify application client ID.
        client_secret: The Spotify application client secret.
        redirect_uri: The OAuth callback URL.

    Example:
        # Get them for whoever the call acts for
        credentials = credentials_for(user)

        # Or create directly
        credentials = SpotifyCredentials(
            client_id='your_client_id',
            client_secret='your_client_secret',
            redirect_uri='http://127.0.0.1:8000/callback'
        )
    """

    client_id: str
    # repr=False keeps the secret out of every string rendering of this
    # object — tracebacks, log lines, and error-reporter frame locals all
    # serialize via repr, so excluding it here is what makes those safe.
    client_secret: str = field(repr=False)
    redirect_uri: str

    def __post_init__(self):
        """Validate credentials on creation."""
        if not self.client_id:
            raise ValueError("client_id is required")
        if not self.client_secret:
            raise ValueError("client_secret is required")
        if not self.redirect_uri:
            raise ValueError("redirect_uri is required")

    @classmethod
    def from_flask_config(cls, config: dict) -> "SpotifyCredentials":
        """
        Create credentials from Flask app config.

        Args:
            config: Flask application config dictionary.

        Returns:
            SpotifyCredentials instance.

        Raises:
            ValueError: If required config keys are missing.
        """
        return cls(
            client_id=config.get("SPOTIFY_CLIENT_ID", ""),
            client_secret=config.get("SPOTIFY_CLIENT_SECRET", ""),
            redirect_uri=config.get("SPOTIFY_REDIRECT_URI", ""),
        )


def credentials_for(owner) -> SpotifyCredentials:
    """The Spotify app credentials to act for ``owner``: the only way any code gets them.

    ``owner`` is the signed-in ``User``, or None where no user row is at hand
    (signing in, and a request before its row is loaded). Every owner gets the
    site's app, from the Flask config of the running app.

    Raises:
        SpotifyAppUnavailable: If the app's client ID, secret or redirect URI is not set.
    """
    from flask import current_app

    try:
        return SpotifyCredentials.from_flask_config(current_app.config)
    except ValueError as error:
        raise SpotifyAppUnavailable(f"The Spotify app is not configured: {error}") from error
