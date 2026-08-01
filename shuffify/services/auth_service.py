"""
Authentication service for Spotify OAuth flow.

Handles OAuth URL generation, token exchange, validation, and session management.
"""

import logging
from typing import Dict, Any, Optional, Tuple

from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import SpotifyTokenError

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
    def _credentials() -> SpotifyCredentials:
        """Resolve OAuth credentials from Flask config, else the environment.

        Background jobs run outside a request and outside an app context, so
        the Flask lookup raises there and the environment is the fallback.
        """
        try:
            from flask import current_app

            return SpotifyCredentials.from_flask_config(current_app.config)
        except RuntimeError:
            return SpotifyCredentials.from_env()

    @staticmethod
    def _auth_manager() -> SpotifyAuthManager:
        """Build an auth manager against the resolved credentials."""
        return SpotifyAuthManager(AuthService._credentials())

    @staticmethod
    def get_auth_url(state: Optional[str] = None) -> str:
        """
        Generate the Spotify authorization URL.

        Args:
            state: Optional CSRF state token for OAuth security.

        Returns:
            The authorization URL to redirect users to.

        Raises:
            AuthenticationError: If URL generation fails.
        """
        try:
            return AuthService._auth_manager().get_auth_url(state=state)
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
            token_data = AuthService._auth_manager().exchange_code(code).to_dict()

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
    def get_authenticated_api(token: Dict[str, Any]) -> SpotifyAPI:
        """
        Build an authenticated SpotifyAPI from a session token.

        Args:
            token: Valid token data dictionary.

        Returns:
            An authenticated SpotifyAPI instance.

        Raises:
            AuthenticationError: If construction fails.
        """
        try:
            # Inject the Redis cache so responses are actually cached in
            # production (returns None when Redis is unavailable) (SR-005).
            from shuffify import get_spotify_cache

            # Expiry is deliberately not pre-validated here. SpotifyAPI with
            # auto_refresh=True refreshes an expired token on construction and
            # on 401 retries; rejecting an expired token up front caused an
            # hourly forced logout even when a valid refresh_token was present
            # (SR-003). A structurally invalid token still fails in
            # TokenInfo.from_dict, and an expired one with no refresh_token
            # still fails through the refresh path.
            return SpotifyAPI(
                TokenInfo.from_dict(token),
                AuthService._auth_manager(),
                auto_refresh=True,
                cache=get_spotify_cache(),
                on_token_refresh=AuthService._persist_token_to_session,
            )
        except SpotifyTokenError as e:
            logger.error("Token initialization failed: %s", e)
            raise AuthenticationError(f"Invalid or expired token: {e}")
        except Exception as e:
            logger.error(f"Failed to create Spotify API client: {e}", exc_info=True)
            raise AuthenticationError(f"Failed to create Spotify client: {e}")

    @staticmethod
    def _persist_token_to_session(token_info: "TokenInfo") -> None:
        """
        Write a refreshed Spotify token back to the Flask session.

        Wired as SpotifyAPI's ``on_token_refresh`` callback so a token refreshed
        mid-request is persisted for subsequent requests instead of being
        discarded (SR-004). No-ops outside a request context -- background
        scheduled jobs have no session to update, so they simply skip this.

        Best-effort: failures are logged and swallowed so token persistence can
        never break the request that triggered the refresh.

        Args:
            token_info: The freshly refreshed TokenInfo.
        """
        from flask import session, has_request_context

        if not has_request_context():
            return

        try:
            session["spotify_token"] = token_info.to_dict()
            session.modified = True
            logger.debug("Persisted refreshed Spotify token to session")
        except Exception as e:
            logger.warning("Failed to persist refreshed token to session: %s", e)

    @staticmethod
    def get_user_data(api: SpotifyAPI) -> Dict[str, Any]:
        """
        Fetch the current user's profile data.

        Args:
            api: An authenticated SpotifyAPI.

        Returns:
            User profile data dictionary.

        Raises:
            AuthenticationError: If fetching user data fails.
        """
        try:
            return api.get_current_user()
        except Exception as e:
            logger.error(f"Failed to get user data: {e}", exc_info=True)
            raise AuthenticationError(f"Failed to fetch user profile: {e}")

    @staticmethod
    def revoke_access_token(access_token: str) -> bool:
        """
        Best-effort revocation of a Spotify access token.

        Fire-and-forget: failures are logged at DEBUG and swallowed
        so the logout flow is never blocked.

        Args:
            access_token: The access token to revoke.

        Returns:
            True if the provider accepted the revocation.
        """
        try:
            return AuthService._auth_manager().revoke_token(access_token)
        except Exception as e:
            logger.debug("Token revocation skipped: %s", e)
            return False

    @staticmethod
    def authenticate_and_get_user(
        token: Dict[str, Any],
    ) -> Tuple[SpotifyAPI, Dict[str, Any]]:
        """
        Authenticate with a token and retrieve user data in one operation.

        Args:
            token: Valid token data dictionary.

        Returns:
            Tuple of (SpotifyAPI, user_data).

        Raises:
            AuthenticationError: If authentication or user fetch fails.
        """
        api = AuthService.get_authenticated_api(token)
        user_data = AuthService.get_user_data(api)
        return api, user_data
