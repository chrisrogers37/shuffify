"""
Tests for AuthService.

Tests cover OAuth flow, token validation, and client creation.
"""

from unittest.mock import Mock, patch

import pytest

from shuffify.services import (
    AuthenticationError,
    AuthService,
    TokenValidationError,
)
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import SpotifyGrantRevoked, spotify_failure_of

CREDENTIALS = SpotifyCredentials(
    client_id="test_client_id",
    client_secret="test_client_secret",
    redirect_uri="http://localhost:5000/callback",
)


def stored_user_id(refresh_token="stored_refresh_token"):
    """A users row holding a refresh token, for the client's refresher to read."""
    from shuffify.models.db import User, db
    from shuffify.services.token_service import TokenService

    user = User(spotify_id=f"user-{db.session.query(User).count()}",
                encrypted_refresh_token=TokenService.encrypt_token(refresh_token))
    db.session.add(user)
    db.session.commit()
    return user.id


class TestAuthServiceTokenValidation:
    """Tests for token validation methods."""

    def test_validate_session_token_with_valid_token(self, sample_token):
        """Valid token should return True."""
        assert AuthService.validate_session_token(sample_token) is True

    def test_validate_session_token_with_none(self):
        """None token should return False."""
        assert AuthService.validate_session_token(None) is False

    def test_validate_session_token_with_empty_dict(self):
        """Empty dict should return False."""
        assert AuthService.validate_session_token({}) is False

    def test_validate_session_token_missing_access_token(self):
        """Token missing access_token should return False."""
        token = {"token_type": "Bearer"}
        assert AuthService.validate_session_token(token) is False

    def test_validate_session_token_missing_token_type(self):
        """Token missing token_type should return False."""
        token = {"access_token": "some_token"}
        assert AuthService.validate_session_token(token) is False

    def test_validate_token_structure_raises_on_non_dict(self):
        """Non-dict token should raise TokenValidationError."""
        with pytest.raises(TokenValidationError) as exc_info:
            AuthService._validate_token_structure("not a dict")
        assert "not a dictionary" in str(exc_info.value)

    def test_validate_token_structure_raises_on_missing_keys(self):
        """Token with missing keys should raise TokenValidationError."""
        with pytest.raises(TokenValidationError) as exc_info:
            AuthService._validate_token_structure({"token_type": "Bearer"})
        assert "missing required keys" in str(exc_info.value)


class TestAuthServiceGetAuthUrl:
    """Tests for get_auth_url method."""

    @patch.object(AuthService, "_auth_manager")
    def test_get_auth_url_success(self, mock_manager, app_context):
        """Should return the authorization URL from the auth manager."""
        mock_instance = Mock()
        mock_instance.get_auth_url.return_value = (
            "https://accounts.spotify.com/authorize?test=1"
        )
        mock_manager.return_value = mock_instance

        url = AuthService.get_auth_url(CREDENTIALS)

        assert url == "https://accounts.spotify.com/authorize?test=1"
        mock_manager.assert_called_once_with(CREDENTIALS)
        mock_instance.get_auth_url.assert_called_once_with(state=None)

    @patch.object(AuthService, "_auth_manager")
    def test_get_auth_url_with_state(self, mock_manager, app_context):
        """Should forward the state parameter to the auth manager."""
        mock_instance = Mock()
        mock_instance.get_auth_url.return_value = (
            "https://accounts.spotify.com/authorize?test=1&state=abc123"
        )
        mock_manager.return_value = mock_instance

        url = AuthService.get_auth_url(CREDENTIALS, state="abc123")

        mock_instance.get_auth_url.assert_called_once_with(state="abc123")
        assert "state=abc123" in url

    @patch.object(AuthService, "_auth_manager")
    def test_get_auth_url_raises_on_failure(self, mock_manager, app_context):
        """Should raise AuthenticationError on failure."""
        mock_instance = Mock()
        mock_instance.get_auth_url.side_effect = Exception("Network error")
        mock_manager.return_value = mock_instance

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.get_auth_url(CREDENTIALS)
        assert "Failed to generate authorization URL" in str(exc_info.value)


class TestAuthServiceExchangeCode:
    """Tests for exchange_code_for_token method."""

    @patch.object(AuthService, "_auth_manager")
    def test_exchange_code_success(self, mock_manager, app_context, sample_token):
        """Should exchange code for valid token."""
        mock_instance = Mock()
        mock_instance.exchange_code.return_value.to_dict.return_value = sample_token
        mock_manager.return_value = mock_instance

        result = AuthService.exchange_code_for_token(CREDENTIALS, "auth_code_123")

        assert result == sample_token
        mock_manager.assert_called_once_with(CREDENTIALS)
        mock_instance.exchange_code.assert_called_once_with("auth_code_123")

    def test_exchange_code_with_empty_code(self, app_context):
        """Should raise AuthenticationError for empty code."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.exchange_code_for_token(CREDENTIALS, "")
        assert "No authorization code provided" in str(exc_info.value)

    def test_exchange_code_with_none_code(self, app_context):
        """Should raise AuthenticationError for None code."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.exchange_code_for_token(CREDENTIALS, None)
        assert "No authorization code provided" in str(exc_info.value)

    @patch.object(AuthService, "_auth_manager")
    def test_exchange_code_invalid_token_response(self, mock_manager, app_context):
        """Should raise TokenValidationError for invalid token structure."""
        mock_instance = Mock()
        mock_instance.exchange_code.return_value.to_dict.return_value = {
            "invalid": "token"
        }
        mock_manager.return_value = mock_instance

        with pytest.raises(TokenValidationError):
            AuthService.exchange_code_for_token(CREDENTIALS, "auth_code_123")

    @patch.object(AuthService, "_auth_manager")
    def test_exchange_code_api_failure(self, mock_manager, app_context):
        """Should raise AuthenticationError on API failure."""
        mock_instance = Mock()
        mock_instance.exchange_code.side_effect = Exception("Spotify API error")
        mock_manager.return_value = mock_instance

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.exchange_code_for_token(CREDENTIALS, "auth_code_123")
        assert "Failed to exchange code for token" in str(exc_info.value)


class TestAuthServiceAuthManager:
    """Each call builds its auth manager on the credentials it is given."""

    def test_auth_manager_is_built_on_the_given_credentials(self):
        with patch(
            "shuffify.services.auth_service.SpotifyAuthManager", autospec=True
        ) as manager_class:
            AuthService._auth_manager(CREDENTIALS)

        manager_class.assert_called_once_with(CREDENTIALS)


class TestAuthServiceGetAuthenticatedApi:
    """Tests for get_authenticated_api method."""

    @patch("shuffify.services.auth_service.SpotifyAuthManager")
    @patch("shuffify.services.auth_service.SpotifyAPI")
    def test_get_authenticated_api_success(
        self, mock_api_class, mock_manager_class, sample_token
    ):
        """Should return a SpotifyAPI that auto-refreshes from the stored
        refresh token and writes each refreshed access token back to the session."""
        from shuffify.services.credential_refresher import CredentialRefresher

        mock_instance = Mock()
        mock_api_class.return_value = mock_instance

        api = AuthService.get_authenticated_api(CREDENTIALS, sample_token, user_id=1)

        assert api == mock_instance
        mock_manager_class.assert_called_once_with(CREDENTIALS)
        assert isinstance(mock_api_class.call_args.args[1], CredentialRefresher)
        kwargs = mock_api_class.call_args.kwargs
        assert kwargs["on_token_refresh"] is AuthService._persist_token_to_session
        assert kwargs["auto_refresh"] is True

    @patch("shuffify.services.auth_service.SpotifyAuthManager")
    @patch("shuffify.services.auth_service.SpotifyAPI")
    def test_get_authenticated_api_auto_refresh_is_on(
        self, mock_api_class, mock_manager_class, sample_token
    ):
        """Expiry must not be pre-validated: with a stored refresh token, the
        API refreshes an expired token on construction.
        """
        AuthService.get_authenticated_api(CREDENTIALS, sample_token, user_id=1)

        assert mock_api_class.call_args.kwargs["auto_refresh"] is True

    @patch("shuffify.get_spotify_cache")
    @patch("shuffify.services.auth_service.SpotifyAuthManager")
    @patch("shuffify.services.auth_service.SpotifyAPI")
    def test_get_authenticated_api_injects_cache(
        self, mock_api_class, mock_manager_class, mock_get_cache, sample_token
    ):
        """The Redis cache is injected so requests reuse cached Spotify
        responses instead of re-fetching them."""
        sentinel = object()
        mock_get_cache.return_value = sentinel

        AuthService.get_authenticated_api(CREDENTIALS, sample_token, user_id=1)

        assert mock_api_class.call_args.kwargs["cache"] is sentinel

    @patch("shuffify.services.auth_service.SpotifyAuthManager")
    @patch("shuffify.services.auth_service.SpotifyAPI")
    def test_get_authenticated_api_failure(
        self, mock_api_class, mock_manager_class, sample_token
    ):
        """Should raise AuthenticationError on construction failure."""
        mock_api_class.side_effect = Exception("API init error")

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.get_authenticated_api(CREDENTIALS, sample_token, user_id=1)
        assert "Failed to create Spotify client" in str(exc_info.value)

    def test_a_refused_refresh_keeps_its_cause(self, app_context, sample_token):
        """The AuthenticationError carries the Spotify failure that caused it."""
        from shuffify.spotify.auth import SpotifyAuthManager

        refusal = SpotifyGrantRevoked("Token refresh failed: Refresh token revoked", error="invalid_grant")
        expired = {**sample_token, "expires_at": 0}
        with patch.object(SpotifyAuthManager, "refresh_token", side_effect=refusal):
            with pytest.raises(AuthenticationError) as exc_info:
                AuthService.get_authenticated_api(CREDENTIALS, expired, user_id=stored_user_id())

        assert exc_info.value.cause is refusal
        assert exc_info.value.__cause__ is refusal
        assert spotify_failure_of(exc_info.value) is refusal

    @patch("shuffify.services.auth_service.SpotifyAPI", autospec=True)
    def test_a_session_without_a_usable_access_token_gets_an_expired_one(self, mock_api_class, app_context):
        """The client then refreshes from the stored refresh token before its first call."""
        for token in (None, {"access_token": "no-type"}):
            AuthService.get_authenticated_api(CREDENTIALS, token, user_id=1)
            assert mock_api_class.call_args.args[0].is_expired

    def test_a_database_refusal_passes_through_as_transient(self, app_context, sample_token):
        """It is not "signed out": the Flask handler answers 503."""
        from sqlalchemy.exc import OperationalError

        from shuffify.spotify.exceptions import SpotifyRefreshUnavailable

        def unavailable():
            raise OperationalError("SELECT", {}, Exception("connection lost"))

        with pytest.raises(SpotifyRefreshUnavailable):
            AuthService.get_authenticated_api(CREDENTIALS, {**sample_token, "expires_at": 0}, user_id=unavailable)


class TestAuthServiceGetUserData:
    """Tests for get_user_data method."""

    def test_get_user_data_success(self, mock_spotify_api, sample_user):
        """Should return user data from client."""
        result = AuthService.get_user_data(mock_spotify_api)

        assert result == sample_user
        mock_spotify_api.get_current_user.assert_called_once()

    def test_get_user_data_failure(self, mock_spotify_api):
        """Should raise AuthenticationError on failure."""
        mock_spotify_api.get_current_user.side_effect = Exception("API error")

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.get_user_data(mock_spotify_api)
        assert "Failed to fetch user profile" in str(exc_info.value)


class TestAuthServiceAuthenticateAndGetUser:
    """Tests for authenticate_and_get_user method."""

    @patch("shuffify.services.auth_service.SpotifyAuthManager")
    @patch("shuffify.services.auth_service.SpotifyAPI")
    def test_authenticate_and_get_user_success(
        self, mock_api_class, mock_manager_class, sample_token, sample_user
    ):
        """Should return a tuple of the API client and user data."""
        mock_instance = Mock()
        mock_instance.get_current_user.return_value = sample_user
        mock_api_class.return_value = mock_instance

        api, user = AuthService.authenticate_and_get_user(CREDENTIALS, sample_token)

        assert api == mock_instance
        assert user == sample_user

    @patch("shuffify.services.auth_service.SpotifyAuthManager")
    @patch("shuffify.services.auth_service.SpotifyAPI")
    def test_authenticate_and_get_user_client_failure(
        self, mock_api_class, mock_manager_class, sample_token
    ):
        """Should raise AuthenticationError if API construction fails."""
        mock_api_class.side_effect = Exception("Client error")

        with pytest.raises(AuthenticationError):
            AuthService.authenticate_and_get_user(CREDENTIALS, sample_token)

    @patch("shuffify.services.auth_service.SpotifyAuthManager")
    @patch("shuffify.services.auth_service.SpotifyAPI")
    def test_authenticate_and_get_user_user_fetch_failure(
        self, mock_api_class, mock_manager_class, sample_token
    ):
        """Should raise AuthenticationError if user fetch fails."""
        mock_instance = Mock()
        mock_instance.get_current_user.side_effect = Exception("User fetch error")
        mock_api_class.return_value = mock_instance

        with pytest.raises(AuthenticationError):
            AuthService.authenticate_and_get_user(CREDENTIALS, sample_token)


class TestAuthServiceTokenRefreshPersistence:
    """Refreshed tokens must be written back to the session."""

    @staticmethod
    def _expired_token():
        import time

        return {
            "access_token": "expired_access",
            "token_type": "Bearer",
            "expires_at": time.time() - 100,
            "expires_in": 0,
            "refresh_token": "test_refresh_token",
        }

    @staticmethod
    def _refreshed_token_info():
        import time

        from shuffify.spotify.auth import TokenInfo

        return TokenInfo(
            access_token="refreshed_access",
            token_type="Bearer",
            expires_at=time.time() + 3600,
            refresh_token="test_refresh_token",
        )

    def test_get_authenticated_api_persists_refreshed_token_to_session(self, app):
        """When building the client triggers a token refresh, the new token
        must be written back to session['spotify_token'] so subsequent
        requests don't refresh again or fail."""
        from flask import session

        from shuffify.spotify.auth import SpotifyAuthManager

        refreshed = self._refreshed_token_info()

        with app.test_request_context():
            session["spotify_token"] = self._expired_token()

            with patch("shuffify.spotify.api.SpotifyHTTPClient"), patch.object(
                SpotifyAuthManager,
                "refresh_token",
                return_value=refreshed,
            ):
                AuthService.get_authenticated_api(CREDENTIALS, session["spotify_token"], user_id=stored_user_id())

            assert session["spotify_token"]["access_token"] == "refreshed_access"
            assert session.modified is True

    def test_a_refresh_writes_no_refresh_token_to_the_session(self, app):
        """The session gets the refreshed access token and its expiry only:
        no refresh token, plaintext or encrypted. A rotated one is stored in
        the users row."""
        from flask import session

        from shuffify.models.db import User, db
        from shuffify.services.token_service import TokenService
        from shuffify.spotify.auth import SpotifyAuthManager

        refreshed = self._refreshed_token_info()

        with app.test_request_context():
            session["spotify_token"] = self._expired_token()
            user_id = stored_user_id()

            with patch("shuffify.spotify.api.SpotifyHTTPClient", autospec=True), patch.object(
                SpotifyAuthManager,
                "refresh_token",
                autospec=True,
                return_value=refreshed,
            ):
                AuthService.get_authenticated_api(CREDENTIALS, session["spotify_token"], user_id=user_id)

            stored = session["spotify_token"]
            row = db.session.get(User, user_id)
            db.session.refresh(row)

        assert set(stored) == {"access_token", "token_type", "expires_at"}
        assert TokenService.decrypt_token(row.encrypted_refresh_token) == refreshed.refresh_token

    def test_refresh_callback_is_noop_without_request_context(self, app):
        """Background executors have no request context; a refresh during
        client creation must not raise."""
        from shuffify.spotify.auth import SpotifyAuthManager

        refreshed = self._refreshed_token_info()

        # No request context -- callback must no-op, not raise.
        with app.app_context():
            with patch("shuffify.spotify.api.SpotifyHTTPClient"), patch.object(
                SpotifyAuthManager,
                "refresh_token",
                return_value=refreshed,
            ):
                api = AuthService.get_authenticated_api(
                    CREDENTIALS, self._expired_token(), user_id=stored_user_id(),
                )

        # Construction succeeded and carries the refreshed token: the callback
        # no-opped outside the request context instead of raising.
        assert api.token_info.access_token == "refreshed_access"
