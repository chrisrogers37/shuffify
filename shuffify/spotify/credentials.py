"""
Spotify credentials management.

Provides a dataclass for Spotify OAuth credentials that does not depend on
Flask, and ``credentials_for``, the one place any code gets the credentials of
the Spotify app it calls Spotify with.
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
        # The credentials of the app a request or a job calls Spotify with
        credentials = credentials_for(user)
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
    """The credentials of the Spotify app ``owner`` calls Spotify with.

    Every place that needs them (the sign-in routes, the web's client builder,
    the worker's) gets them here. For now that is the site's one app, from
    the Flask config, whoever ``owner`` is: the ``User`` a request or a job
    acts for, or None before anyone has signed in. Later the owner's own
    Spotify connection supplies them.

    Raises:
        SpotifyAppUnavailable: If the app's ID or secret (or the callback URL)
            is not set. There is no other source to fall back to.
    """
    from flask import current_app

    config = current_app.config
    if not config.get("SPOTIFY_CLIENT_ID") or not config.get("SPOTIFY_CLIENT_SECRET"):
        raise SpotifyAppUnavailable("The Spotify app's client ID and secret are not set.")
    try:
        return SpotifyCredentials.from_flask_config(config)
    except ValueError as error:
        raise SpotifyAppUnavailable("The Spotify app's credentials are incomplete.") from error
