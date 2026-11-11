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
from shuffify.spotify.exceptions import (
    SpotifyAppRejected,
    SpotifyGrantRevoked,
    SpotifyRateLimitError,
    SpotifyTokenError,
    SpotifyUserNotAllowed,
    spotify_failure_of,
)

CREDENTIALS = SpotifyCredentials(
    client_id="test_client_id",
    client_secret="test_client_secret",
    redirect_uri="http://localhost:5000/callback",
)


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


class TestAuthServiceUsesTheCredentialsItIsGiven:
    """Every AuthService call that reaches Spotify takes its credentials as an
    argument. Nothing here reads the Flask config or the environment: that is
    ``credentials_for``'s job, in the routes and the worker."""

    def test_the_auth_manager_is_built_on_the_credentials_it_is_given(self):
        with patch("shuffify.services.auth_service.SpotifyAuthManager", autospec=True) as manager_class:
            AuthService._auth_manager(CREDENTIALS)

        manager_class.assert_called_once_with(CREDENTIALS)

    def test_there_is_no_fallback_credential_lookup(self):
        assert not hasattr(AuthService, "_credentials")

    def test_each_call_builds_its_manager_on_the_credentials_passed(self, sample_token):
        with patch.object(AuthService, "_auth_manager") as manager:
            manager.return_value.get_auth_url.return_value = "https://example.test/authorize"
            manager.return_value.exchange_code.return_value.to_dict.return_value = sample_token
            manager.return_value.revoke_token.return_value = True
            AuthService.get_auth_url(CREDENTIALS)
            AuthService.exchange_code_for_token(CREDENTIALS, "code")
            AuthService.revoke_access_token(CREDENTIALS, "token")

        assert [call.args for call in manager.call_args_list] == [(CREDENTIALS,)] * 3


class TestAuthServiceGetAuthenticatedApi:
    """Tests for get_authenticated_api method."""

    @patch("shuffify.services.auth_service.SpotifyAuthManager")
    @patch("shuffify.services.auth_service.SpotifyAPI")
    def test_get_authenticated_api_success(
        self, mock_api_class, mock_manager_class, sample_token
    ):
        """Should return a SpotifyAPI that auto-refreshes and writes each
        refreshed token back to the session."""
        mock_instance = Mock()
        mock_api_class.return_value = mock_instance

        api = AuthService.get_authenticated_api(CREDENTIALS, sample_token)

        assert api == mock_instance
        kwargs = mock_api_class.call_args.kwargs
        assert kwargs["on_token_refresh"] is AuthService._persist_token_to_session
        assert kwargs["auto_refresh"] is True

    @patch("shuffify.services.auth_service.SpotifyAuthManager")
    @patch("shuffify.services.auth_service.SpotifyAPI")
    def test_get_authenticated_api_auto_refresh_is_on(
        self, mock_api_class, mock_manager_class, sample_token
    ):
        """Expiry must not be pre-validated: with a valid refresh_token, the
        API refreshes an expired token on construction.
        """
        AuthService.get_authenticated_api(CREDENTIALS, sample_token)

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

        AuthService.get_authenticated_api(CREDENTIALS, sample_token)

        assert mock_api_class.call_args.kwargs["cache"] is sentinel

    @patch("shuffify.services.auth_service.SpotifyAuthManager")
    @patch("shuffify.services.auth_service.SpotifyAPI")
    def test_get_authenticated_api_failure(
        self, mock_api_class, mock_manager_class, sample_token
    ):
        """Should raise AuthenticationError on construction failure."""
        mock_api_class.side_effect = Exception("API init error")

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.get_authenticated_api(CREDENTIALS, sample_token)
        assert "Failed to create Spotify client" in str(exc_info.value)


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

    @staticmethod
    def _test_credentials():
        return CREDENTIALS

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
                "ensure_valid_token",
                return_value=refreshed,
            ):
                AuthService.get_authenticated_api(
                    self._test_credentials(), session["spotify_token"]
                )

            assert session["spotify_token"]["access_token"] == "refreshed_access"
            assert session.modified is True

    def test_refreshed_token_is_persisted_encrypted(self, app):
        """A refresh token Spotify issues mid-request reaches the session
        encrypted, never in plaintext."""
        from flask import session

        from shuffify.services.token_service import TokenService
        from shuffify.spotify.auth import SpotifyAuthManager

        refreshed = self._refreshed_token_info()

        with app.test_request_context():
            session["spotify_token"] = self._expired_token()

            with patch("shuffify.spotify.api.SpotifyHTTPClient", autospec=True), patch.object(
                SpotifyAuthManager,
                "ensure_valid_token",
                autospec=True,
                return_value=refreshed,
            ):
                AuthService.get_authenticated_api(
                    self._test_credentials(), session["spotify_token"]
                )

            stored = session["spotify_token"]

        assert "refresh_token" not in stored
        assert TokenService.decrypt_token(stored["encrypted_refresh_token"]) == refreshed.refresh_token

    def test_refresh_callback_is_noop_without_request_context(self, app):
        """Background executors have no request context; a refresh during
        client creation must not raise."""
        from shuffify.spotify.auth import SpotifyAuthManager

        refreshed = self._refreshed_token_info()

        # No request context -- callback must no-op, not raise.
        with app.app_context():
            with patch("shuffify.spotify.api.SpotifyHTTPClient"), patch.object(
                SpotifyAuthManager,
                "ensure_valid_token",
                return_value=refreshed,
            ):
                api = AuthService.get_authenticated_api(
                    self._test_credentials(), self._expired_token()
                )

        # Construction succeeded and carries the refreshed token: the callback
        # no-opped outside the request context instead of raising.
        assert api.token_info.access_token == "refreshed_access"


class TestAuthenticationErrorKeepsItsCause:
    """The web wraps a Spotify failure in AuthenticationError. The wrapper
    keeps its message and class, and now also the failure that triggered it,
    both as ``cause`` and as ``__cause__``, so ``spotify_failure_of`` finds it."""

    @patch("shuffify.services.auth_service.SpotifyAuthManager", autospec=True)
    @patch("shuffify.services.auth_service.SpotifyAPI", autospec=True)
    @pytest.mark.parametrize("kind", [SpotifyAppRejected, SpotifyGrantRevoked])
    def test_a_failed_refresh_at_construction(self, mock_api_class, mock_manager_class, kind, sample_token):
        failure = kind("Token refresh failed: nope")
        mock_api_class.side_effect = failure

        with pytest.raises(AuthenticationError) as caught:
            AuthService.get_authenticated_api(CREDENTIALS, sample_token)

        assert str(caught.value) == "Invalid or expired token: Token refresh failed: nope"
        assert caught.value.cause is failure
        assert caught.value.__cause__ is failure
        assert spotify_failure_of(caught.value) is failure

    @patch("shuffify.services.auth_service.SpotifyAuthManager", autospec=True)
    @patch("shuffify.services.auth_service.SpotifyAPI", autospec=True)
    def test_an_ordinary_token_failure_is_kept_too(self, mock_api_class, mock_manager_class, sample_token):
        failure = SpotifyTokenError("plain")
        mock_api_class.side_effect = failure

        with pytest.raises(AuthenticationError) as caught:
            AuthService.get_authenticated_api(CREDENTIALS, sample_token)

        assert caught.value.cause is failure
        assert spotify_failure_of(caught.value) is None

    @patch("shuffify.services.auth_service.SpotifyAuthManager", autospec=True)
    @patch("shuffify.services.auth_service.SpotifyAPI", autospec=True)
    def test_a_failure_while_creating_the_client(self, mock_api_class, mock_manager_class, sample_token):
        failure = RuntimeError("init error")
        mock_api_class.side_effect = failure

        with pytest.raises(AuthenticationError) as caught:
            AuthService.get_authenticated_api(CREDENTIALS, sample_token)

        assert str(caught.value) == "Failed to create Spotify client: init error"
        assert caught.value.cause is failure and caught.value.__cause__ is failure

    @patch.object(AuthService, "_auth_manager")
    def test_a_failed_code_exchange(self, mock_manager, app_context):
        failure = SpotifyGrantRevoked("Token exchange failed: Invalid authorization code")
        mock_manager.return_value.exchange_code.side_effect = failure

        with pytest.raises(AuthenticationError) as caught:
            AuthService.exchange_code_for_token(CREDENTIALS, "code")

        assert str(caught.value) == (
            "Failed to exchange code for token: Token exchange failed: Invalid authorization code"
        )
        assert spotify_failure_of(caught.value) is failure

    def test_a_refused_profile_fetch(self, mock_spotify_api):
        failure = SpotifyUserNotAllowed("API error 403: User not registered in the Developer Dashboard")
        mock_spotify_api.get_current_user.side_effect = failure

        with pytest.raises(AuthenticationError) as caught:
            AuthService.get_user_data(mock_spotify_api)

        assert "not registered" in str(caught.value)
        assert caught.value.cause is failure and caught.value.__cause__ is failure
        assert spotify_failure_of(caught.value) is failure

    @patch("shuffify.services.auth_service.remaining_seconds", autospec=True, return_value=None)
    def test_a_rate_limit_outside_a_deadline(self, _remaining, mock_spotify_api):
        failure = SpotifyRateLimitError("slow down", retry_after=3)
        mock_spotify_api.get_current_user.side_effect = failure

        with pytest.raises(AuthenticationError) as caught:
            AuthService.get_user_data(mock_spotify_api)

        assert caught.value.cause is failure

    def test_the_cause_defaults_to_none_and_the_subclass_still_takes_a_message(self):
        assert AuthenticationError("plain").cause is None
        assert TokenValidationError("bad token").cause is None
        assert str(TokenValidationError("bad token")) == "bad token"
