"""
Tests for JobExecutorService._get_spotify_api.

Covers how a worker's Spotify client is built from a user's stored
refresh token, and that a refresh token Spotify rotates is stored.
"""

import time
from unittest.mock import Mock, patch

import pytest

from shuffify.models.db import User, db
from shuffify.services.executors import (
    JobExecutionError,
    JobExecutorService,
)
from shuffify.services.token_service import TokenService
from shuffify.spotify.auth import TokenInfo

# TestRotatedRefreshTokenIsStored stores tokens through db.session, so every
# test in this module needs the shared app_ctx fixture (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("app_ctx")


@pytest.fixture
def mock_user():
    """Create a mock User model instance."""
    user = Mock()
    user.id = 1
    user.spotify_id = "test_user"
    user.encrypted_refresh_token = "encrypted_token_data"
    return user


class TestGetSpotifyApi:
    """Tests for _get_spotify_api token management."""

    @patch(
        "shuffify.services.executors.base_executor"
        ".TokenService"
    )
    @patch(
        "shuffify.services.executors.base_executor"
        ".SpotifyAPI"
    )
    @patch(
        "shuffify.services.executors.base_executor"
        ".SpotifyAuthManager"
    )
    @patch(
        "shuffify.services.executors.base_executor"
        ".SpotifyCredentials"
    )
    def test_creates_api_with_decrypted_token(
        self,
        mock_creds,
        mock_auth,
        mock_api_class,
        mock_token_svc,
        mock_user,
        app_context,
    ):
        """Should decrypt token and create SpotifyAPI."""
        mock_token_svc.decrypt_token.return_value = (
            "decrypted_refresh"
        )
        mock_api_instance = Mock()
        mock_api_class.return_value = mock_api_instance

        result = JobExecutorService._get_spotify_api(
            mock_user
        )
        mock_token_svc.decrypt_token.assert_called_once_with(
            "encrypted_token_data"
        )
        assert result == mock_api_instance

    @patch("shuffify.get_spotify_cache")
    @patch(
        "shuffify.services.executors.base_executor.TokenService"
    )
    @patch(
        "shuffify.services.executors.base_executor.SpotifyAPI"
    )
    @patch(
        "shuffify.services.executors.base_executor"
        ".SpotifyAuthManager"
    )
    @patch(
        "shuffify.services.executors.base_executor"
        ".SpotifyCredentials"
    )
    def test_injects_spotify_cache(
        self,
        mock_creds,
        mock_auth,
        mock_api_class,
        mock_token_svc,
        mock_get_cache,
        mock_user,
        app_context,
    ):
        """The Redis cache is injected into the executor's SpotifyAPI so
        background jobs share cached playlist/user data."""
        mock_token_svc.decrypt_token.return_value = "decrypted_refresh"
        mock_api_instance = Mock()
        mock_api_class.return_value = mock_api_instance
        sentinel = object()
        mock_get_cache.return_value = sentinel

        JobExecutorService._get_spotify_api(mock_user)

        assert mock_api_class.call_args.kwargs["cache"] is sentinel

    def test_no_refresh_token_raises(self, mock_user):
        """Should raise when user has no stored token."""
        mock_user.encrypted_refresh_token = None

        with pytest.raises(
            JobExecutionError,
            match="no stored refresh token",
        ):
            JobExecutorService._get_spotify_api(mock_user)


class TestRotatedRefreshTokenIsStored:
    """Spotify may issue a new refresh token on any refresh, and the old one
    can stop working. A job stores each new one the moment it arrives: at
    construction, on expiry before a call, and on a 401 retry."""

    @staticmethod
    def _token(refresh_token, expired=False):
        return TokenInfo(
            access_token="fresh_access",
            token_type="Bearer",
            expires_at=time.time() + (-1 if expired else 3600),
            refresh_token=refresh_token,
        )

    @pytest.fixture
    def user(self):
        user = User(
            spotify_id="rotating_user",
            display_name="Rotating",
            encrypted_refresh_token=TokenService.encrypt_token("refresh_v1"),
        )
        db.session.add(user)
        db.session.commit()
        return user

    def _job_api(self, user, at_construction, on_refresh):
        """A real SpotifyAPI from _get_spotify_api with its Spotify calls
        stubbed, and the 401 callback the HTTP client would invoke.

        ``at_construction`` is the token the first refresh returns, or a list
        of tokens for successive ``ensure_valid_token`` calls; ``on_refresh``
        is what a 401's outright refresh returns.
        """
        with patch(
            "shuffify.services.executors.base_executor.SpotifyCredentials",
            autospec=True,
        ), patch(
            "shuffify.services.executors.base_executor.SpotifyAuthManager",
            autospec=True,
        ) as auth_cls, patch(
            "shuffify.spotify.api.SpotifyHTTPClient", autospec=True,
        ) as http_cls:
            tokens = at_construction if isinstance(at_construction, list) else [at_construction]
            auth_cls.return_value.ensure_valid_token.side_effect = tokens
            auth_cls.return_value.refresh_token.return_value = self._token(on_refresh)
            api = JobExecutorService._get_spotify_api(user)
            return api, http_cls.call_args.kwargs["on_token_refresh"]

    @staticmethod
    def _stored(user):
        db.session.expire_all()
        return db.session.get(User, user.id).encrypted_refresh_token

    def test_a_token_rotated_at_construction_is_stored(self, user):
        self._job_api(user, self._token("refresh_v2"), on_refresh="refresh_v2")
        assert TokenService.decrypt_token(self._stored(user)) == "refresh_v2"

    def test_a_token_rotated_on_a_401_mid_job_is_stored(self, user):
        _, on_401 = self._job_api(user, self._token("refresh_v1"), on_refresh="refresh_v2")
        assert TokenService.decrypt_token(self._stored(user)) == "refresh_v1"
        on_401()  # what the HTTP client calls when Spotify answers 401
        assert TokenService.decrypt_token(self._stored(user)) == "refresh_v2"

    def test_a_token_rotated_on_expiry_mid_job_is_stored(self, user):
        api, _ = self._job_api(
            user,
            [self._token("refresh_v1", expired=True), self._token("refresh_v2")],
            on_refresh="refresh_v1",
        )
        api.get_current_user()  # finds the token expired and refreshes first
        assert TokenService.decrypt_token(self._stored(user)) == "refresh_v2"

    def test_each_new_token_is_stored_once(self, user):
        """Fernet ciphertext differs on every encryption, so a repeat write
        of the same token would show as a changed column."""
        _, on_401 = self._job_api(user, self._token("refresh_v2"), on_refresh="refresh_v2")
        stored = self._stored(user)
        on_401()
        assert self._stored(user) == stored

    def test_a_failed_store_is_retried_on_the_next_refresh(self, user):
        _, on_401 = self._job_api(user, self._token("refresh_v1"), on_refresh="refresh_v2")
        real_commit, failures = db.session.commit, [RuntimeError("database down")]

        def commit():
            if failures:
                raise failures.pop()
            real_commit()

        with patch.object(db.session, "commit", side_effect=commit):
            on_401()  # the store fails: the job carries on
            assert TokenService.decrypt_token(self._stored(user)) == "refresh_v1"
            on_401()  # the next refresh stores it
        assert TokenService.decrypt_token(self._stored(user)) == "refresh_v2"
