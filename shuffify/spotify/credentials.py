"""
Spotify credentials management.

Provides a clean dataclass for Spotify OAuth credentials,
eliminating hidden Flask dependencies.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SpotifyCredentials:
    """
    Immutable container for Spotify OAuth credentials.

    This class encapsulates all credentials needed for Spotify OAuth,
    enabling dependency injection and eliminating hidden Flask dependencies.

    Attributes:
        client_id: The Spotify application client ID.
        client_secret: The Spotify application client secret.
        redirect_uri: The OAuth callback URL.

    Example:
        # Create from Flask config
        credentials = SpotifyCredentials.from_flask_config(current_app.config)

        # Or create directly
        credentials = SpotifyCredentials(
            client_id='your_client_id',
            client_secret='your_client_secret',
            redirect_uri='http://localhost:5000/callback'
        )
    """

    client_id: str
    client_secret: str
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
    def from_flask_config(cls, config: dict) -> 'SpotifyCredentials':
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
            client_id=config.get('SPOTIFY_CLIENT_ID', ''),
            client_secret=config.get('SPOTIFY_CLIENT_SECRET', ''),
            redirect_uri=config.get('SPOTIFY_REDIRECT_URI', '')
        )

    @classmethod
    def from_env(cls) -> 'SpotifyCredentials':
        """
        Create credentials from environment variables.

        Returns:
            SpotifyCredentials instance.

        Raises:
            ValueError: If required environment variables are missing.
        """
        import os
        return cls(
            client_id=os.getenv('SPOTIFY_CLIENT_ID', ''),
            client_secret=os.getenv('SPOTIFY_CLIENT_SECRET', ''),
            redirect_uri=os.getenv('SPOTIFY_REDIRECT_URI', '')
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for legacy compatibility."""
        return {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri
        }
