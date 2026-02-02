"""
Authentication service for Spotify OAuth flow.

Handles OAuth URL generation, token exchange, validation, and session management.
"""

import logging
from typing import Dict, Any, Optional, Tuple

from shuffify.spotify.client import SpotifyClient

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass


class TokenValidationError(AuthenticationError):
    """Raised when token validation fails."""

    pass


class AuthService:
    """Service for managing Spotify OAuth authentication."""

    @staticmethod
    def get_auth_url() -> str:
        """
        Generate the Spotify authorization URL.

        Returns:
            The authorization URL to redirect users to.

        Raises:
            AuthenticationError: If URL generation fails.
        """
        try:
            client = SpotifyClient()
            return client.get_auth_url()
        except Exception as e:
            logger.error(f"Failed to generate auth URL: {e}", exc_info=True)
            raise AuthenticationError(f"Failed to generate authorization URL: {e}")

    @staticmethod
    def exchange_code_for_token(code: str) -> Dict[str, Any]:
        """
        Exchange an authorization code for an access token.

        Args:
            code: The authorization code from Spotify callback.

        Returns:
            Token data dictionary with access_token, refresh_token, etc.

        Raises:
            AuthenticationError: If token exchange fails.
            TokenValidationError: If token structure is invalid.
        """
        if not code:
            raise AuthenticationError("No authorization code provided")

        try:
            client = SpotifyClient()
            token_data = client.get_token(code)

            # Validate token structure
            AuthService._validate_token_structure(token_data)

            logger.debug("Successfully exchanged code for token")
            return token_data

        except TokenValidationError:
            raise
        except Exception as e:
            logger.error(f"Token exchange failed: {e}", exc_info=True)
            raise AuthenticationError(f"Failed to exchange code for token: {e}")

    @staticmethod
    def _validate_token_structure(token_data: Any) -> None:
        """
        Validate that token data has the required structure.

        Args:
            token_data: The token data to validate.

        Raises:
            TokenValidationError: If token structure is invalid.
        """
        if not isinstance(token_data, dict):
            raise TokenValidationError(
                f"Token data is not a dictionary: {type(token_data)}"
            )

        required_keys = ["access_token", "token_type"]
        missing_keys = [key for key in required_keys if key not in token_data]

        if missing_keys:
            raise TokenValidationError(f"Token missing required keys: {missing_keys}")

    @staticmethod
    def validate_session_token(token: Optional[Dict[str, Any]]) -> bool:
        """
        Check if a session token exists and has valid structure.

        Args:
            token: The token from session, or None.

        Returns:
            True if token is valid, False otherwise.
        """
        if not token:
            return False

        try:
            AuthService._validate_token_structure(token)
            return True
        except TokenValidationError:
            return False

    @staticmethod
    def get_authenticated_client(token: Dict[str, Any]) -> SpotifyClient:
        """
        Create an authenticated SpotifyClient from a token.

        Args:
            token: Valid token data dictionary.

        Returns:
            An authenticated SpotifyClient instance.

        Raises:
            AuthenticationError: If client creation fails.
        """
        try:
            return SpotifyClient(token=token)
        except Exception as e:
            logger.error(f"Failed to create authenticated client: {e}", exc_info=True)
            raise AuthenticationError(f"Failed to create Spotify client: {e}")

    @staticmethod
    def get_user_data(client: SpotifyClient) -> Dict[str, Any]:
        """
        Fetch the current user's profile data.

        Args:
            client: An authenticated SpotifyClient.

        Returns:
            User profile data dictionary.

        Raises:
            AuthenticationError: If fetching user data fails.
        """
        try:
            return client.get_current_user()
        except Exception as e:
            logger.error(f"Failed to get user data: {e}", exc_info=True)
            raise AuthenticationError(f"Failed to fetch user profile: {e}")

    @staticmethod
    def authenticate_and_get_user(
        token: Dict[str, Any],
    ) -> Tuple[SpotifyClient, Dict[str, Any]]:
        """
        Authenticate with a token and retrieve user data in one operation.

        Args:
            token: Valid token data dictionary.

        Returns:
            Tuple of (SpotifyClient, user_data).

        Raises:
            AuthenticationError: If authentication or user fetch fails.
        """
        client = AuthService.get_authenticated_client(token)
        user_data = AuthService.get_user_data(client)
        return client, user_data
