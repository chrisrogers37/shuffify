"""
Spotify authentication and token management.

Handles OAuth flow, token exchange, validation, and refresh.
This module is responsible for all authentication concerns,
separating them from data operations.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from urllib.parse import urlencode

import requests

from .credentials import SpotifyCredentials
from .exceptions import (
    SpotifyAuthError,
    SpotifyTokenError,
    SpotifyTokenExpiredError,
)

logger = logging.getLogger(__name__)


# Default OAuth scopes for Shuffify
DEFAULT_SCOPES = [
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-read-private",
    "user-read-playback-state",
    "user-read-email",
    "user-read-currently-playing",
    "user-read-recently-played",
    "user-top-read",
]


@dataclass
class TokenInfo:
    """
    Structured container for OAuth token information.

    Provides type-safe access to token data with validation
    and expiration checking.
    """

    access_token: str
    token_type: str
    expires_at: float
    refresh_token: Optional[str] = None
    scope: Optional[str] = None
    expires_in: Optional[int] = None

    # Original dict for compatibility
    _raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenInfo":
        """
        Create TokenInfo from a dictionary.

        Args:
            data: Token dictionary from Spotify OAuth.

        Returns:
            TokenInfo instance.

        Raises:
            SpotifyTokenError: If required fields are missing.
        """
        if not isinstance(data, dict):
            raise SpotifyTokenError(
                f"Token data must be a dictionary, got {type(data)}"
            )

        required = ["access_token", "token_type"]
        missing = [k for k in required if k not in data]
        if missing:
            raise SpotifyTokenError(f"Token missing required fields: {missing}")

        # Handle expires_at - compute if not present
        expires_at = data.get("expires_at")
        if expires_at is None:
            expires_in = data.get("expires_in", 3600)
            expires_at = time.time() + expires_in

        return cls(
            access_token=data["access_token"],
            token_type=data["token_type"],
            expires_at=expires_at,
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope"),
            expires_in=data.get("expires_in"),
            _raw=data.copy(),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/transmission."""
        result = {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
        }
        if self.refresh_token:
            result["refresh_token"] = self.refresh_token
        if self.scope:
            result["scope"] = self.scope
        if self.expires_in:
            result["expires_in"] = self.expires_in
        return result

    @property
    def is_expired(self) -> bool:
        """Check if the token has expired."""
        return self.expires_at < time.time()

    @property
    def expires_in_seconds(self) -> int:
        """Get seconds until expiration (negative if expired)."""
        return int(self.expires_at - time.time())

    def validate(self) -> None:
        """
        Validate that the token is usable.

        Raises:
            SpotifyTokenExpiredError: If the token has expired.
            SpotifyTokenError: If the token is invalid.
        """
        if not self.access_token:
            raise SpotifyTokenError("Token has no access_token")
        if self.is_expired:
            raise SpotifyTokenExpiredError(
                f"Token expired {abs(self.expires_in_seconds)} seconds ago"
            )


class SpotifyAuthManager:
    """
    Manages Spotify OAuth authentication.

    Handles authorization URL generation, token exchange,
    validation, and refresh. This class is stateless regarding
    tokens - it operates on tokens passed to it.

    Example:
        credentials = SpotifyCredentials.from_flask_config(app.config)
        auth_manager = SpotifyAuthManager(credentials)

        # Get auth URL for user to visit
        auth_url = auth_manager.get_auth_url()

        # Exchange code for token
        token_info = auth_manager.exchange_code(code)

        # Refresh if needed
        if token_info.is_expired:
            token_info = auth_manager.refresh_token(token_info)
    """

    def __init__(self, credentials: SpotifyCredentials, scopes: Optional[list] = None):
        """
        Initialize the auth manager.

        Args:
            credentials: SpotifyCredentials instance with OAuth credentials.
            scopes: Optional list of OAuth scopes. Defaults to DEFAULT_SCOPES.
        """
        self._credentials = credentials
        self._scopes = scopes or DEFAULT_SCOPES
        self._scope_string = " ".join(self._scopes)

    # Spotify OAuth endpoints
    _AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
    _TOKEN_URL = "https://accounts.spotify.com/api/token"

    def get_auth_url(self, state: Optional[str] = None) -> str:
        """
        Generate the Spotify authorization URL.

        Args:
            state: Optional state parameter for CSRF protection.

        Returns:
            The authorization URL to redirect users to.

        Raises:
            SpotifyAuthError: If URL generation fails.
        """
        try:
            params = {
                "client_id": self._credentials.client_id,
                "response_type": "code",
                "redirect_uri": self._credentials.redirect_uri,
                "scope": self._scope_string,
            }
            if state:
                params["state"] = state

            url = f"{self._AUTHORIZE_URL}?{urlencode(params)}"
            logger.debug(f"Generated auth URL: {url[:50]}...")
            return url
        except Exception as e:
            logger.error(f"Failed to generate auth URL: {e}")
            raise SpotifyAuthError(
                f"Failed to generate authorization URL: {e}"
            )

    def exchange_code(self, code: str) -> TokenInfo:
        """
        Exchange an authorization code for tokens.

        Args:
            code: The authorization code from OAuth callback.

        Returns:
            TokenInfo with access and refresh tokens.

        Raises:
            SpotifyAuthError: If code is missing or invalid.
            SpotifyTokenError: If token exchange fails.
        """
        if not code:
            raise SpotifyAuthError("Authorization code is required")

        try:
            response = requests.post(
                self._TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._credentials.redirect_uri,
                },
                auth=(
                    self._credentials.client_id,
                    self._credentials.client_secret,
                ),
                timeout=30,
            )

            if response.status_code != 200:
                error_msg = response.json().get(
                    "error_description", response.text
                )
                raise SpotifyTokenError(
                    f"Token exchange failed: {error_msg}"
                )

            token_data = response.json()
            if not token_data:
                raise SpotifyTokenError(
                    "No token returned from Spotify"
                )

            token_info = TokenInfo.from_dict(token_data)
            logger.info("Successfully exchanged code for token")
            return token_info

        except SpotifyTokenError:
            raise
        except SpotifyAuthError:
            raise
        except Exception as e:
            logger.error(
                f"Token exchange failed: {e}", exc_info=True
            )
            raise SpotifyTokenError(
                f"Token exchange failed: {e}"
            )

    def refresh_token(self, token_info: TokenInfo) -> TokenInfo:
        """
        Refresh an expired token.

        Args:
            token_info: The TokenInfo with a refresh_token.

        Returns:
            New TokenInfo with fresh access token.

        Raises:
            SpotifyTokenError: If refresh fails or no refresh_token available.
        """
        if not token_info.refresh_token:
            raise SpotifyTokenError(
                "Cannot refresh: no refresh_token available"
            )

        try:
            response = requests.post(
                self._TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token_info.refresh_token,
                },
                auth=(
                    self._credentials.client_id,
                    self._credentials.client_secret,
                ),
                timeout=30,
            )

            if response.status_code != 200:
                error_msg = response.json().get(
                    "error_description", response.text
                )
                raise SpotifyTokenError(
                    f"Token refresh failed: {error_msg}"
                )

            new_token_data = response.json()
            if not new_token_data:
                raise SpotifyTokenError(
                    "No token returned from refresh"
                )

            # Spotify may not return a new refresh_token.
            # Preserve the original so we can refresh again later.
            if "refresh_token" not in new_token_data:
                new_token_data["refresh_token"] = (
                    token_info.refresh_token
                )

            new_token_info = TokenInfo.from_dict(new_token_data)
            logger.info("Successfully refreshed token")
            return new_token_info

        except SpotifyTokenError:
            raise
        except Exception as e:
            logger.error(
                f"Token refresh failed: {e}", exc_info=True
            )
            raise SpotifyTokenError(
                f"Token refresh failed: {e}"
            )

    def ensure_valid_token(self, token_info: TokenInfo) -> TokenInfo:
        """
        Ensure a token is valid, refreshing if necessary.

        Args:
            token_info: The token to validate/refresh.

        Returns:
            Valid TokenInfo (original or refreshed).

        Raises:
            SpotifyTokenError: If token cannot be made valid.
        """
        if not token_info.is_expired:
            return token_info

        logger.info("Token expired, attempting refresh")
        return self.refresh_token(token_info)

    def validate_token(self, token_data: Optional[Dict[str, Any]]) -> bool:
        """
        Check if token data is structurally valid (not expired check).

        Args:
            token_data: Token dictionary to validate.

        Returns:
            True if token has valid structure, False otherwise.
        """
        if not token_data or not isinstance(token_data, dict):
            return False

        try:
            TokenInfo.from_dict(token_data)
            return True
        except SpotifyTokenError:
            return False
