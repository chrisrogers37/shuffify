"""
Tests for AuthService.

Tests cover OAuth flow, token validation, and client creation.
"""

import pytest
from unittest.mock import Mock, patch, ANY

from shuffify.services import (
    AuthService,
    AuthenticationError,
    TokenValidationError,
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

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_get_auth_url_success(self, mock_client_class, app_context):
        """Should return authorization URL from SpotifyClient."""
        mock_instance = Mock()
        mock_instance.get_auth_url.return_value = (
            "https://accounts.spotify.com/authorize?test=1"
        )
        mock_client_class.return_value = mock_instance

        url = AuthService.get_auth_url()

        assert url == "https://accounts.spotify.com/authorize?test=1"
        mock_client_class.assert_called_once()
        mock_instance.get_auth_url.assert_called_once_with(state=None)

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_get_auth_url_with_state(self, mock_client_class, app_context):
        """Should forward state parameter to SpotifyClient."""
        mock_instance = Mock()
        mock_instance.get_auth_url.return_value = (
            "https://accounts.spotify.com/authorize?test=1&state=abc123"
        )
        mock_client_class.return_value = mock_instance

        url = AuthService.get_auth_url(state="abc123")

        mock_instance.get_auth_url.assert_called_once_with(state="abc123")
        assert "state=abc123" in url

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_get_auth_url_raises_on_failure(self, mock_client_class, app_context):
        """Should raise AuthenticationError on failure."""
        mock_instance = Mock()
        mock_instance.get_auth_url.side_effect = Exception("Network error")
        mock_client_class.return_value = mock_instance

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.get_auth_url()
        assert "Failed to generate authorization URL" in str(exc_info.value)


class TestAuthServiceExchangeCode:
    """Tests for exchange_code_for_token method."""

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_exchange_code_success(self, mock_client_class, app_context, sample_token):
        """Should exchange code for valid token."""
        mock_instance = Mock()
        mock_instance.get_token.return_value = sample_token
        mock_client_class.return_value = mock_instance

        result = AuthService.exchange_code_for_token("auth_code_123")

        assert result == sample_token
        mock_instance.get_token.assert_called_once_with("auth_code_123")

    def test_exchange_code_with_empty_code(self, app_context):
        """Should raise AuthenticationError for empty code."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.exchange_code_for_token("")
        assert "No authorization code provided" in str(exc_info.value)

    def test_exchange_code_with_none_code(self, app_context):
        """Should raise AuthenticationError for None code."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.exchange_code_for_token(None)
        assert "No authorization code provided" in str(exc_info.value)

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_exchange_code_invalid_token_response(self, mock_client_class, app_context):
        """Should raise TokenValidationError for invalid token structure."""
        mock_instance = Mock()
        mock_instance.get_token.return_value = {"invalid": "token"}
        mock_client_class.return_value = mock_instance

        with pytest.raises(TokenValidationError):
            AuthService.exchange_code_for_token("auth_code_123")

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_exchange_code_api_failure(self, mock_client_class, app_context):
        """Should raise AuthenticationError on API failure."""
        mock_instance = Mock()
        mock_instance.get_token.side_effect = Exception("Spotify API error")
        mock_client_class.return_value = mock_instance

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.exchange_code_for_token("auth_code_123")
        assert "Failed to exchange code for token" in str(exc_info.value)


class TestAuthServiceGetAuthenticatedClient:
    """Tests for get_authenticated_client method."""

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_get_authenticated_client_success(self, mock_client_class, sample_token):
        """Should return SpotifyClient instance."""
        mock_instance = Mock()
        mock_client_class.return_value = mock_instance

        client = AuthService.get_authenticated_client(sample_token)

        assert client == mock_instance
        mock_client_class.assert_called_once_with(
            token=sample_token,
            on_token_refresh=AuthService._persist_token_to_session,
            cache=ANY,
        )

    @patch("shuffify.get_spotify_cache")
    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_get_authenticated_client_injects_cache(
        self, mock_client_class, mock_get_cache, sample_token
    ):
        """The Redis cache is injected into the client so caching is actually
        used in production instead of every request re-fetching (SR-005)."""
        sentinel = object()
        mock_get_cache.return_value = sentinel

        AuthService.get_authenticated_client(sample_token)

        assert mock_client_class.call_args.kwargs["cache"] is sentinel

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_get_authenticated_client_failure(self, mock_client_class, sample_token):
        """Should raise AuthenticationError on client creation failure."""
        mock_client_class.side_effect = Exception("Client init error")

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.get_authenticated_client(sample_token)
        assert "Failed to create Spotify client" in str(exc_info.value)


class TestAuthServiceGetUserData:
    """Tests for get_user_data method."""

    def test_get_user_data_success(self, mock_spotify_client, sample_user):
        """Should return user data from client."""
        result = AuthService.get_user_data(mock_spotify_client)

        assert result == sample_user
        mock_spotify_client.get_current_user.assert_called_once()

    def test_get_user_data_failure(self, mock_spotify_client):
        """Should raise AuthenticationError on failure."""
        mock_spotify_client.get_current_user.side_effect = Exception("API error")

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.get_user_data(mock_spotify_client)
        assert "Failed to fetch user profile" in str(exc_info.value)


class TestAuthServiceAuthenticateAndGetUser:
    """Tests for authenticate_and_get_user method."""

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_authenticate_and_get_user_success(
        self, mock_client_class, sample_token, sample_user
    ):
        """Should return tuple of client and user data."""
        mock_instance = Mock()
        mock_instance.get_current_user.return_value = sample_user
        mock_client_class.return_value = mock_instance

        client, user = AuthService.authenticate_and_get_user(sample_token)

        assert client == mock_instance
        assert user == sample_user
        mock_client_class.assert_called_once_with(
            token=sample_token,
            on_token_refresh=AuthService._persist_token_to_session,
            cache=ANY,
        )

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_authenticate_and_get_user_client_failure(
        self, mock_client_class, sample_token
    ):
        """Should raise AuthenticationError if client creation fails."""
        mock_client_class.side_effect = Exception("Client error")

        with pytest.raises(AuthenticationError):
            AuthService.authenticate_and_get_user(sample_token)

    @patch("shuffify.services.auth_service.SpotifyClient")
    def test_authenticate_and_get_user_user_fetch_failure(
        self, mock_client_class, sample_token
    ):
        """Should raise AuthenticationError if user fetch fails."""
        mock_instance = Mock()
        mock_instance.get_current_user.side_effect = Exception("User fetch error")
        mock_client_class.return_value = mock_instance

        with pytest.raises(AuthenticationError):
            AuthService.authenticate_and_get_user(sample_token)


class TestAuthServiceTokenRefreshPersistence:
    """Refreshed tokens must be written back to the session (SR-003 + SR-004)."""

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
        from shuffify.spotify.credentials import SpotifyCredentials

        return SpotifyCredentials(
            client_id="test_client_id",
            client_secret="test_client_secret",
            redirect_uri="http://localhost:5000/callback",
        )

    def test_get_authenticated_client_persists_refreshed_token_to_session(self, app):
        """When building the client triggers a token refresh, the new token
        must be written back to session['spotify_token'] so subsequent
        requests don't refresh again or fail (SR-004)."""
        from flask import session
        from shuffify.spotify.auth import SpotifyAuthManager

        refreshed = self._refreshed_token_info()

        with app.test_request_context():
            session["spotify_token"] = self._expired_token()

            with patch("shuffify.spotify.api.SpotifyHTTPClient"), patch(
                "shuffify.spotify.client.SpotifyCredentials.from_flask_config",
                return_value=self._test_credentials(),
            ), patch.object(
                SpotifyAuthManager,
                "ensure_valid_token",
                return_value=refreshed,
            ):
                AuthService.get_authenticated_client(session["spotify_token"])

            assert session["spotify_token"]["access_token"] == "refreshed_access"
            assert session.modified is True

    def test_refresh_callback_is_noop_without_request_context(self, app):
        """Background executors have no request context; a refresh during
        client creation must not raise (SR-004)."""
        from shuffify.spotify.auth import SpotifyAuthManager

        refreshed = self._refreshed_token_info()

        # No request context -- callback must no-op, not raise.
        with app.app_context():
            with patch("shuffify.spotify.api.SpotifyHTTPClient"), patch(
                "shuffify.spotify.client.SpotifyCredentials.from_flask_config",
                return_value=self._test_credentials(),
            ), patch.object(
                SpotifyAuthManager,
                "ensure_valid_token",
                return_value=refreshed,
            ):
                client = AuthService.get_authenticated_client(
                    self._expired_token()
                )

        assert client.is_authenticated
