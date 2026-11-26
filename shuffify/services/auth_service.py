"""
Authentication service for Spotify OAuth flow.

Handles OAuth URL generation, token exchange, validation, and session management.
"""

import logging
from typing import Any, Callable, Dict, Optional, Tuple, Union

from shuffify.services.credential_refresher import CredentialRefresher, expired_token
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.deadline import SpotifyDeadlineError, remaining_seconds
from shuffify.spotify.exceptions import SpotifyRateLimitError, SpotifyRefreshUnavailable, SpotifyTokenError

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when authentication fails.

    ``cause`` is the failure that triggered it, if any, so the Spotify
    failure behind it can be found (``spotify_failure_of``).
    """

    def __init__(self, message: str, *, cause: Optional[BaseException] = None):
        super().__init__(message)
        self.cause = cause


class TokenValidationError(AuthenticationError):
    """Raised when token validation fails."""

    pass


def session_token(token_info: TokenInfo) -> Dict[str, Any]:
    """A token as the session keeps it: the access token, its type and its expiry.

    The refresh token is stored in the users row only.
    """
    return {
        "access_token": token_info.access_token,
        "token_type": token_info.token_type,
        "expires_at": token_info.expires_at,
    }


class AuthService:
    """Service for managing Spotify OAuth authentication."""

    @staticmethod
    def _auth_manager(credentials: SpotifyCredentials) -> SpotifyAuthManager:
        """Build an auth manager against the given credentials."""
        return SpotifyAuthManager(credentials)

    @staticmethod
    def get_auth_url(credentials: SpotifyCredentials, state: Optional[str] = None) -> str:
        """
        Generate the Spotify authorization URL.

        Args:
            credentials: The app's credentials, from ``credentials_for``.
            state: Optional CSRF state token for OAuth security.

        Returns:
            The authorization URL to redirect users to.

        Raises:
            AuthenticationError: If URL generation fails.
        """
        try:
            return AuthService._auth_manager(credentials).get_auth_url(state=state)
        except Exception as e:
            logger.error(f"Failed to generate auth URL: {e}", exc_info=True)
            raise AuthenticationError(f"Failed to generate authorization URL: {e}", cause=e) from e

    @staticmethod
    def exchange_code_for_token(credentials: SpotifyCredentials, code: str) -> Dict[str, Any]:
        """
        Exchange an authorization code for an access token.

        Args:
            credentials: The app's credentials, from ``credentials_for``.
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
            token_data = AuthService._auth_manager(credentials).exchange_code(code).to_dict()

            # Validate token structure
            AuthService._validate_token_structure(token_data)

            logger.debug("Successfully exchanged code for token")
            return token_data

        except (SpotifyDeadlineError, TokenValidationError):
            raise
        except SpotifyRateLimitError as error:
            if remaining_seconds() is not None:
                raise
            raise AuthenticationError(f"Failed to exchange code for token: {error}", cause=error) from error
        except Exception as e:
            logger.error(f"Token exchange failed: {e}", exc_info=True)
            raise AuthenticationError(f"Failed to exchange code for token: {e}", cause=e) from e

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
    def get_authenticated_api(
        credentials: SpotifyCredentials,
        token: Optional[Dict[str, Any]],
        *,
        user_id: Union[Optional[int], Callable[[], Optional[int]]],
        authenticated_user_id: Optional[str] = None,
    ) -> SpotifyAPI:
        """
        Build an authenticated SpotifyAPI that refreshes from the stored refresh token.

        Args:
            credentials: The app's credentials, from ``credentials_for``.
            token: The session's access token, a cache. When it is missing or
                malformed the client refreshes before its first call.
            user_id: The users row whose stored refresh token every refresh
                uses, or a callable that finds it (``CredentialRefresher``);
                None when the person has no row yet.
            authenticated_user_id: The account verified for this signed-in session.
                Leave unset when authenticating a new token.

        Returns:
            An authenticated SpotifyAPI instance.

        Raises:
            AuthenticationError: If construction fails.
            SpotifyRefreshUnavailable: If the stored refresh token can't be
                reached just now. It is transient, not "signed out".
        """
        try:
            # Inject the Redis cache so responses are actually cached in
            # production (returns None when Redis is unavailable).
            from shuffify import get_spotify_cache

            # Expiry is deliberately not pre-validated here. SpotifyAPI with
            # auto_refresh=True refreshes an expired token on construction and
            # on 401 retries, from the refresh token stored in the users row.
            token_info = TokenInfo.from_dict(token) if AuthService.validate_session_token(token) else expired_token()
            return SpotifyAPI(
                token_info,
                CredentialRefresher(AuthService._auth_manager(credentials), user_id),
                auto_refresh=True,
                cache=get_spotify_cache(),
                authenticated_user_id=authenticated_user_id,
                on_token_refresh=AuthService._persist_token_to_session,
            )
        except (SpotifyDeadlineError, SpotifyRefreshUnavailable):
            raise
        except SpotifyRateLimitError as error:
            if remaining_seconds() is not None:
                raise
            raise AuthenticationError(f"Failed to create Spotify client: {error}", cause=error) from error
        except SpotifyTokenError as e:
            logger.error("Token initialization failed: %s", e)
            raise AuthenticationError(f"Invalid or expired token: {e}", cause=e) from e
        except Exception as e:
            logger.error(f"Failed to create Spotify API client: {e}", exc_info=True)
            raise AuthenticationError(f"Failed to create Spotify client: {e}", cause=e) from e

    @staticmethod
    def _persist_token_to_session(token_info: "TokenInfo") -> None:
        """
        Write a refreshed access token and its expiry back to the Flask session.

        Wired as SpotifyAPI's ``on_token_refresh`` callback so a token refreshed
        mid-request is reused by subsequent requests instead of being
        discarded. The refresh token is not written: the refresher has
        already stored a rotated one. No-ops outside a request context -- background
        scheduled jobs have no session to update, so they simply skip this.

        Best-effort: failures are logged and swallowed so token persistence can
        never break the request that triggered the refresh.

        Args:
            token_info: The freshly refreshed TokenInfo.
        """
        from flask import has_request_context, session

        if not has_request_context():
            return

        try:
            session["spotify_token"] = session_token(token_info)
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
        except SpotifyDeadlineError:
            raise
        except SpotifyRateLimitError as error:
            if remaining_seconds() is not None:
                raise
            raise AuthenticationError(f"Failed to fetch user profile: {error}", cause=error) from error
        except Exception as e:
            logger.error(f"Failed to get user data: {e}", exc_info=True)
            raise AuthenticationError(f"Failed to fetch user profile: {e}", cause=e) from e

    @staticmethod
    def revoke_access_token(credentials: SpotifyCredentials, access_token: str) -> bool:
        """
        Best-effort revocation of a Spotify access token.

        Fire-and-forget: failures are logged at DEBUG and swallowed
        so the logout flow is never blocked.

        Args:
            credentials: The app's credentials, from ``credentials_for``.
            access_token: The access token to revoke.

        Returns:
            True if the provider accepted the revocation.
        """
        try:
            return AuthService._auth_manager(credentials).revoke_token(access_token)
        except Exception as e:
            logger.debug("Token revocation skipped: %s", e)
            return False

    @staticmethod
    def authenticate_and_get_user(
        credentials: SpotifyCredentials,
        token: Dict[str, Any],
    ) -> Tuple[SpotifyAPI, Dict[str, Any]]:
        """
        Authenticate with a token and retrieve user data in one operation.

        The profile names the users row, which may not exist yet, so this
        client has no stored refresh token to refresh with: a token just
        exchanged needs none.

        Args:
            credentials: The app's credentials, from ``credentials_for``.
            token: Valid token data dictionary.

        Returns:
            Tuple of (SpotifyAPI, user_data).

        Raises:
            AuthenticationError: If authentication or user fetch fails.
        """
        api = AuthService.get_authenticated_api(credentials, token, user_id=None)
        user_data = AuthService.get_user_data(api)
        return api, user_data
