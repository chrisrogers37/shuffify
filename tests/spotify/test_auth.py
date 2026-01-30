"""
Tests for SpotifyAuthManager and TokenInfo.

Tests cover token validation, OAuth flow, and token refresh.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from shuffify.spotify.auth import (
    SpotifyAuthManager,
    TokenInfo,
    DEFAULT_SCOPES,
)
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyTokenError,
    SpotifyTokenExpiredError,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def credentials():
    """Valid SpotifyCredentials for testing."""
    return SpotifyCredentials(
        client_id='test_client_id',
        client_secret='test_client_secret',
        redirect_uri='http://localhost:5000/callback'
    )


@pytest.fixture
def auth_manager(credentials):
    """SpotifyAuthManager instance for testing."""
    return SpotifyAuthManager(credentials)


@pytest.fixture
def valid_token_data():
    """Valid token dictionary."""
    return {
        'access_token': 'test_access_token',
        'token_type': 'Bearer',
        'expires_at': time.time() + 3600,
        'expires_in': 3600,
        'refresh_token': 'test_refresh_token',
        'scope': 'playlist-read-private'
    }


@pytest.fixture
def expired_token_data():
    """Expired token dictionary."""
    return {
        'access_token': 'expired_access_token',
        'token_type': 'Bearer',
        'expires_at': time.time() - 100,  # Expired
        'refresh_token': 'test_refresh_token',
    }


# =============================================================================
# TokenInfo Tests
# =============================================================================

class TestTokenInfoFromDict:
    """Tests for TokenInfo.from_dict factory method."""

    def test_from_dict_with_valid_data(self, valid_token_data):
        """Should create TokenInfo from valid dictionary."""
        token = TokenInfo.from_dict(valid_token_data)

        assert token.access_token == 'test_access_token'
        assert token.token_type == 'Bearer'
        assert token.refresh_token == 'test_refresh_token'
        assert not token.is_expired

    def test_from_dict_computes_expires_at_if_missing(self):
        """Should compute expires_at from expires_in if not provided."""
        data = {
            'access_token': 'test',
            'token_type': 'Bearer',
            'expires_in': 3600
        }

        before = time.time()
        token = TokenInfo.from_dict(data)
        after = time.time()

        # expires_at should be approximately now + 3600
        assert before + 3600 <= token.expires_at <= after + 3600

    def test_from_dict_with_missing_access_token(self):
        """Should raise SpotifyTokenError for missing access_token."""
        data = {'token_type': 'Bearer'}

        with pytest.raises(SpotifyTokenError) as exc_info:
            TokenInfo.from_dict(data)
        assert 'access_token' in str(exc_info.value)

    def test_from_dict_with_missing_token_type(self):
        """Should raise SpotifyTokenError for missing token_type."""
        data = {'access_token': 'test'}

        with pytest.raises(SpotifyTokenError) as exc_info:
            TokenInfo.from_dict(data)
        assert 'token_type' in str(exc_info.value)

    def test_from_dict_with_non_dict_input(self):
        """Should raise SpotifyTokenError for non-dict input."""
        with pytest.raises(SpotifyTokenError) as exc_info:
            TokenInfo.from_dict("not a dict")
        assert 'must be a dictionary' in str(exc_info.value)

    def test_from_dict_with_none_input(self):
        """Should raise SpotifyTokenError for None input."""
        with pytest.raises(SpotifyTokenError):
            TokenInfo.from_dict(None)


class TestTokenInfoProperties:
    """Tests for TokenInfo properties."""

    def test_is_expired_false_for_valid_token(self, valid_token_data):
        """Should return False for non-expired token."""
        token = TokenInfo.from_dict(valid_token_data)
        assert token.is_expired is False

    def test_is_expired_true_for_expired_token(self, expired_token_data):
        """Should return True for expired token."""
        token = TokenInfo.from_dict(expired_token_data)
        assert token.is_expired is True

    def test_expires_in_seconds_positive_for_valid_token(self, valid_token_data):
        """Should return positive seconds for valid token."""
        token = TokenInfo.from_dict(valid_token_data)
        assert token.expires_in_seconds > 0

    def test_expires_in_seconds_negative_for_expired_token(self, expired_token_data):
        """Should return negative seconds for expired token."""
        token = TokenInfo.from_dict(expired_token_data)
        assert token.expires_in_seconds < 0


class TestTokenInfoValidate:
    """Tests for TokenInfo.validate method."""

    def test_validate_success_for_valid_token(self, valid_token_data):
        """Should not raise for valid token."""
        token = TokenInfo.from_dict(valid_token_data)
        token.validate()  # Should not raise

    def test_validate_raises_for_expired_token(self, expired_token_data):
        """Should raise SpotifyTokenExpiredError for expired token."""
        token = TokenInfo.from_dict(expired_token_data)

        with pytest.raises(SpotifyTokenExpiredError):
            token.validate()


class TestTokenInfoToDict:
    """Tests for TokenInfo.to_dict method."""

    def test_to_dict_includes_required_fields(self, valid_token_data):
        """Should include all required fields."""
        token = TokenInfo.from_dict(valid_token_data)
        result = token.to_dict()

        assert 'access_token' in result
        assert 'token_type' in result
        assert 'expires_at' in result

    def test_to_dict_includes_optional_fields_when_present(self, valid_token_data):
        """Should include optional fields when present."""
        token = TokenInfo.from_dict(valid_token_data)
        result = token.to_dict()

        assert 'refresh_token' in result
        assert 'scope' in result


# =============================================================================
# SpotifyAuthManager Tests
# =============================================================================

class TestSpotifyAuthManagerInit:
    """Tests for SpotifyAuthManager initialization."""

    def test_init_with_credentials(self, credentials):
        """Should initialize with credentials."""
        manager = SpotifyAuthManager(credentials)
        assert manager._credentials == credentials

    def test_init_with_custom_scopes(self, credentials):
        """Should accept custom scopes."""
        custom_scopes = ['playlist-read-private', 'user-read-email']
        manager = SpotifyAuthManager(credentials, scopes=custom_scopes)
        assert manager._scopes == custom_scopes

    def test_init_uses_default_scopes(self, credentials):
        """Should use default scopes when not provided."""
        manager = SpotifyAuthManager(credentials)
        assert manager._scopes == DEFAULT_SCOPES


class TestSpotifyAuthManagerGetAuthUrl:
    """Tests for get_auth_url method."""

    def test_get_auth_url_returns_url(self, auth_manager):
        """Should return a Spotify authorization URL."""
        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.get_authorize_url.return_value = 'https://accounts.spotify.com/authorize?...'

            url = auth_manager.get_auth_url()

            assert url.startswith('https://accounts.spotify.com/authorize')

    def test_get_auth_url_with_state(self, auth_manager):
        """Should pass state parameter to OAuth."""
        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.get_authorize_url.return_value = 'https://accounts.spotify.com/authorize?state=abc'

            url = auth_manager.get_auth_url(state='abc')

            mock_oauth.return_value.get_authorize_url.assert_called_with(state='abc')

    def test_get_auth_url_raises_on_failure(self, auth_manager):
        """Should raise SpotifyAuthError on failure."""
        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.get_authorize_url.side_effect = Exception('OAuth error')

            with pytest.raises(SpotifyAuthError):
                auth_manager.get_auth_url()


class TestSpotifyAuthManagerExchangeCode:
    """Tests for exchange_code method."""

    def test_exchange_code_success(self, auth_manager, valid_token_data):
        """Should exchange code for token successfully."""
        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.get_access_token.return_value = valid_token_data

            token = auth_manager.exchange_code('test_code')

            assert isinstance(token, TokenInfo)
            assert token.access_token == valid_token_data['access_token']

    def test_exchange_code_with_empty_code(self, auth_manager):
        """Should raise SpotifyAuthError for empty code."""
        with pytest.raises(SpotifyAuthError) as exc_info:
            auth_manager.exchange_code('')
        assert 'code is required' in str(exc_info.value)

    def test_exchange_code_with_none_code(self, auth_manager):
        """Should raise SpotifyAuthError for None code."""
        with pytest.raises(SpotifyAuthError):
            auth_manager.exchange_code(None)

    def test_exchange_code_returns_none_from_api(self, auth_manager):
        """Should raise SpotifyTokenError when API returns None."""
        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.get_access_token.return_value = None

            with pytest.raises(SpotifyTokenError) as exc_info:
                auth_manager.exchange_code('test_code')
            assert 'No token returned' in str(exc_info.value)

    def test_exchange_code_api_failure(self, auth_manager):
        """Should raise SpotifyTokenError on API failure."""
        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.get_access_token.side_effect = Exception('API error')

            with pytest.raises(SpotifyTokenError):
                auth_manager.exchange_code('test_code')


class TestSpotifyAuthManagerRefreshToken:
    """Tests for refresh_token method."""

    def test_refresh_token_success(self, auth_manager, valid_token_data):
        """Should refresh token successfully."""
        token = TokenInfo.from_dict(valid_token_data)
        new_token_data = {**valid_token_data, 'access_token': 'new_access_token'}

        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.refresh_access_token.return_value = new_token_data

            new_token = auth_manager.refresh_token(token)

            assert new_token.access_token == 'new_access_token'

    def test_refresh_token_without_refresh_token(self, auth_manager):
        """Should raise SpotifyTokenError when no refresh_token."""
        token_data = {
            'access_token': 'test',
            'token_type': 'Bearer',
            'expires_at': time.time() + 3600
        }
        token = TokenInfo.from_dict(token_data)

        with pytest.raises(SpotifyTokenError) as exc_info:
            auth_manager.refresh_token(token)
        assert 'no refresh_token' in str(exc_info.value)

    def test_refresh_token_api_failure(self, auth_manager, valid_token_data):
        """Should raise SpotifyTokenError on API failure."""
        token = TokenInfo.from_dict(valid_token_data)

        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.refresh_access_token.side_effect = Exception('API error')

            with pytest.raises(SpotifyTokenError):
                auth_manager.refresh_token(token)


class TestSpotifyAuthManagerEnsureValidToken:
    """Tests for ensure_valid_token method."""

    def test_ensure_valid_token_returns_valid_token(self, auth_manager, valid_token_data):
        """Should return original token if not expired."""
        token = TokenInfo.from_dict(valid_token_data)

        result = auth_manager.ensure_valid_token(token)

        assert result.access_token == token.access_token

    def test_ensure_valid_token_refreshes_expired_token(self, auth_manager, expired_token_data):
        """Should refresh expired token."""
        token = TokenInfo.from_dict(expired_token_data)
        new_token_data = {
            'access_token': 'new_token',
            'token_type': 'Bearer',
            'expires_at': time.time() + 3600,
            'refresh_token': 'new_refresh'
        }

        with patch.object(auth_manager, '_create_oauth') as mock_oauth:
            mock_oauth.return_value.refresh_access_token.return_value = new_token_data

            result = auth_manager.ensure_valid_token(token)

            assert result.access_token == 'new_token'
            assert not result.is_expired


class TestSpotifyAuthManagerValidateToken:
    """Tests for validate_token method."""

    def test_validate_token_returns_true_for_valid(self, auth_manager, valid_token_data):
        """Should return True for valid token structure."""
        result = auth_manager.validate_token(valid_token_data)
        assert result is True

    def test_validate_token_returns_false_for_none(self, auth_manager):
        """Should return False for None."""
        result = auth_manager.validate_token(None)
        assert result is False

    def test_validate_token_returns_false_for_empty_dict(self, auth_manager):
        """Should return False for empty dict."""
        result = auth_manager.validate_token({})
        assert result is False

    def test_validate_token_returns_false_for_missing_fields(self, auth_manager):
        """Should return False for missing required fields."""
        result = auth_manager.validate_token({'token_type': 'Bearer'})
        assert result is False
