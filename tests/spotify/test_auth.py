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


def _mock_response(status_code=200, json_data=None):
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = str(json_data) if json_data else ""
    return resp


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
        url = auth_manager.get_auth_url()
        assert url.startswith('https://accounts.spotify.com/authorize')

    def test_get_auth_url_includes_client_id(self, auth_manager):
        """Should include the client_id parameter."""
        url = auth_manager.get_auth_url()
        assert 'client_id=test_client_id' in url

    def test_get_auth_url_includes_redirect_uri(self, auth_manager):
        """Should include the redirect_uri parameter."""
        url = auth_manager.get_auth_url()
        assert 'redirect_uri=' in url

    def test_get_auth_url_includes_response_type(self, auth_manager):
        """Should include response_type=code."""
        url = auth_manager.get_auth_url()
        assert 'response_type=code' in url

    def test_get_auth_url_with_state(self, auth_manager):
        """Should include state parameter when provided."""
        url = auth_manager.get_auth_url(state='abc123')
        assert 'state=abc123' in url

    def test_get_auth_url_without_state(self, auth_manager):
        """Should not include state when not provided."""
        url = auth_manager.get_auth_url()
        assert 'state=' not in url


class TestSpotifyAuthManagerExchangeCode:
    """Tests for exchange_code method."""

    @patch('shuffify.spotify.auth.requests.post')
    def test_exchange_code_success(
        self, mock_post, auth_manager, valid_token_data,
    ):
        """Should exchange code for token successfully."""
        mock_post.return_value = _mock_response(200, valid_token_data)

        token = auth_manager.exchange_code('test_code')

        assert isinstance(token, TokenInfo)
        assert token.access_token == valid_token_data['access_token']
        mock_post.assert_called_once()

    @patch('shuffify.spotify.auth.requests.post')
    def test_exchange_code_sends_correct_data(
        self, mock_post, auth_manager, valid_token_data,
    ):
        """Should send correct grant_type and code."""
        mock_post.return_value = _mock_response(200, valid_token_data)

        auth_manager.exchange_code('test_code')

        call_kwargs = mock_post.call_args
        assert call_kwargs[1]['data']['grant_type'] == 'authorization_code'
        assert call_kwargs[1]['data']['code'] == 'test_code'

    def test_exchange_code_with_empty_code(self, auth_manager):
        """Should raise SpotifyAuthError for empty code."""
        with pytest.raises(SpotifyAuthError) as exc_info:
            auth_manager.exchange_code('')
        assert 'code is required' in str(exc_info.value)

    def test_exchange_code_with_none_code(self, auth_manager):
        """Should raise SpotifyAuthError for None code."""
        with pytest.raises(SpotifyAuthError):
            auth_manager.exchange_code(None)

    @patch('shuffify.spotify.auth.requests.post')
    def test_exchange_code_api_returns_error(
        self, mock_post, auth_manager,
    ):
        """Should raise SpotifyTokenError on non-200 response."""
        mock_post.return_value = _mock_response(
            400, {"error_description": "Invalid code"},
        )

        with pytest.raises(SpotifyTokenError, match="Invalid code"):
            auth_manager.exchange_code('bad_code')

    @patch('shuffify.spotify.auth.requests.post')
    def test_exchange_code_network_failure(
        self, mock_post, auth_manager,
    ):
        """Should raise SpotifyTokenError on network failure."""
        mock_post.side_effect = Exception('Connection refused')

        with pytest.raises(SpotifyTokenError):
            auth_manager.exchange_code('test_code')


class TestSpotifyAuthManagerRefreshToken:
    """Tests for refresh_token method."""

    @patch('shuffify.spotify.auth.requests.post')
    def test_refresh_token_success(
        self, mock_post, auth_manager, valid_token_data,
    ):
        """Should refresh token successfully."""
        token = TokenInfo.from_dict(valid_token_data)
        new_token_data = {
            **valid_token_data,
            'access_token': 'new_access_token',
        }
        mock_post.return_value = _mock_response(200, new_token_data)

        new_token = auth_manager.refresh_token(token)

        assert new_token.access_token == 'new_access_token'

    @patch('shuffify.spotify.auth.requests.post')
    def test_refresh_preserves_refresh_token_when_absent(
        self, mock_post, auth_manager, valid_token_data,
    ):
        """Should preserve original refresh_token when not in response."""
        token = TokenInfo.from_dict(valid_token_data)
        # Response without refresh_token
        new_token_data = {
            'access_token': 'new_access',
            'token_type': 'Bearer',
            'expires_in': 3600,
        }
        mock_post.return_value = _mock_response(200, new_token_data)

        new_token = auth_manager.refresh_token(token)

        assert new_token.refresh_token == 'test_refresh_token'

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

    @patch('shuffify.spotify.auth.requests.post')
    def test_refresh_token_api_failure(
        self, mock_post, auth_manager, valid_token_data,
    ):
        """Should raise SpotifyTokenError on API failure."""
        token = TokenInfo.from_dict(valid_token_data)
        mock_post.return_value = _mock_response(
            400, {"error_description": "Invalid refresh token"},
        )

        with pytest.raises(SpotifyTokenError):
            auth_manager.refresh_token(token)

    @patch('shuffify.spotify.auth.requests.post')
    def test_refresh_token_network_error(
        self, mock_post, auth_manager, valid_token_data,
    ):
        """Should raise SpotifyTokenError on network error."""
        token = TokenInfo.from_dict(valid_token_data)
        mock_post.side_effect = Exception('Connection refused')

        with pytest.raises(SpotifyTokenError):
            auth_manager.refresh_token(token)


class TestSpotifyAuthManagerEnsureValidToken:
    """Tests for ensure_valid_token method."""

    def test_ensure_valid_token_returns_valid_token(self, auth_manager, valid_token_data):
        """Should return original token if not expired."""
        token = TokenInfo.from_dict(valid_token_data)

        result = auth_manager.ensure_valid_token(token)

        assert result.access_token == token.access_token

    @patch('shuffify.spotify.auth.requests.post')
    def test_ensure_valid_token_refreshes_expired_token(
        self, mock_post, auth_manager, expired_token_data,
    ):
        """Should refresh expired token."""
        token = TokenInfo.from_dict(expired_token_data)
        new_token_data = {
            'access_token': 'new_token',
            'token_type': 'Bearer',
            'expires_at': time.time() + 3600,
            'refresh_token': 'new_refresh'
        }
        mock_post.return_value = _mock_response(200, new_token_data)

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
