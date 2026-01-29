"""
Tests for AuthService.

Tests cover OAuth flow, token validation, and client creation.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import time

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
        token = {'token_type': 'Bearer'}
        assert AuthService.validate_session_token(token) is False

    def test_validate_session_token_missing_token_type(self):
        """Token missing token_type should return False."""
        token = {'access_token': 'some_token'}
        assert AuthService.validate_session_token(token) is False

    def test_validate_token_structure_raises_on_non_dict(self):
        """Non-dict token should raise TokenValidationError."""
        with pytest.raises(TokenValidationError) as exc_info:
            AuthService._validate_token_structure("not a dict")
        assert "not a dictionary" in str(exc_info.value)

    def test_validate_token_structure_raises_on_missing_keys(self):
        """Token with missing keys should raise TokenValidationError."""
        with pytest.raises(TokenValidationError) as exc_info:
            AuthService._validate_token_structure({'token_type': 'Bearer'})
        assert "missing required keys" in str(exc_info.value)


class TestAuthServiceGetAuthUrl:
    """Tests for get_auth_url method."""

    @patch('shuffify.services.auth_service.SpotifyClient')
    def test_get_auth_url_success(self, mock_client_class, app_context):
        """Should return authorization URL from SpotifyClient."""
        mock_instance = Mock()
        mock_instance.get_auth_url.return_value = 'https://accounts.spotify.com/authorize?test=1'
        mock_client_class.return_value = mock_instance

        url = AuthService.get_auth_url()

        assert url == 'https://accounts.spotify.com/authorize?test=1'
        mock_client_class.assert_called_once()
        mock_instance.get_auth_url.assert_called_once()

    @patch('shuffify.services.auth_service.SpotifyClient')
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

    @patch('shuffify.services.auth_service.SpotifyClient')
    def test_exchange_code_success(self, mock_client_class, app_context, sample_token):
        """Should exchange code for valid token."""
        mock_instance = Mock()
        mock_instance.get_token.return_value = sample_token
        mock_client_class.return_value = mock_instance

        result = AuthService.exchange_code_for_token('auth_code_123')

        assert result == sample_token
        mock_instance.get_token.assert_called_once_with('auth_code_123')

    def test_exchange_code_with_empty_code(self, app_context):
        """Should raise AuthenticationError for empty code."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.exchange_code_for_token('')
        assert "No authorization code provided" in str(exc_info.value)

    def test_exchange_code_with_none_code(self, app_context):
        """Should raise AuthenticationError for None code."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.exchange_code_for_token(None)
        assert "No authorization code provided" in str(exc_info.value)

    @patch('shuffify.services.auth_service.SpotifyClient')
    def test_exchange_code_invalid_token_response(self, mock_client_class, app_context):
        """Should raise TokenValidationError for invalid token structure."""
        mock_instance = Mock()
        mock_instance.get_token.return_value = {'invalid': 'token'}
        mock_client_class.return_value = mock_instance

        with pytest.raises(TokenValidationError):
            AuthService.exchange_code_for_token('auth_code_123')

    @patch('shuffify.services.auth_service.SpotifyClient')
    def test_exchange_code_api_failure(self, mock_client_class, app_context):
        """Should raise AuthenticationError on API failure."""
        mock_instance = Mock()
        mock_instance.get_token.side_effect = Exception("Spotify API error")
        mock_client_class.return_value = mock_instance

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.exchange_code_for_token('auth_code_123')
        assert "Failed to exchange code for token" in str(exc_info.value)


class TestAuthServiceGetAuthenticatedClient:
    """Tests for get_authenticated_client method."""

    @patch('shuffify.services.auth_service.SpotifyClient')
    def test_get_authenticated_client_success(self, mock_client_class, sample_token):
        """Should return SpotifyClient instance."""
        mock_instance = Mock()
        mock_client_class.return_value = mock_instance

        client = AuthService.get_authenticated_client(sample_token)

        assert client == mock_instance
        mock_client_class.assert_called_once_with(token=sample_token)

    @patch('shuffify.services.auth_service.SpotifyClient')
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

    @patch('shuffify.services.auth_service.SpotifyClient')
    def test_authenticate_and_get_user_success(self, mock_client_class, sample_token, sample_user):
        """Should return tuple of client and user data."""
        mock_instance = Mock()
        mock_instance.get_current_user.return_value = sample_user
        mock_client_class.return_value = mock_instance

        client, user = AuthService.authenticate_and_get_user(sample_token)

        assert client == mock_instance
        assert user == sample_user
        mock_client_class.assert_called_once_with(token=sample_token)

    @patch('shuffify.services.auth_service.SpotifyClient')
    def test_authenticate_and_get_user_client_failure(self, mock_client_class, sample_token):
        """Should raise AuthenticationError if client creation fails."""
        mock_client_class.side_effect = Exception("Client error")

        with pytest.raises(AuthenticationError):
            AuthService.authenticate_and_get_user(sample_token)

    @patch('shuffify.services.auth_service.SpotifyClient')
    def test_authenticate_and_get_user_user_fetch_failure(self, mock_client_class, sample_token):
        """Should raise AuthenticationError if user fetch fails."""
        mock_instance = Mock()
        mock_instance.get_current_user.side_effect = Exception("User fetch error")
        mock_client_class.return_value = mock_instance

        with pytest.raises(AuthenticationError):
            AuthService.authenticate_and_get_user(sample_token)
