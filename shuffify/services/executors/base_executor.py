"""Spotify credentials for background work, and the errors the durable operations raise.

``JobExecutorService._get_spotify_api`` builds the Spotify client the worker uses for a user's stored
refresh token. ``JobExecutionError`` and ``PlaylistVerificationError`` are what the operation executors
and live actions raise.
"""

from collections import Counter
from typing import List

from shuffify.models.db import User
from shuffify.services.base import safe_commit
from shuffify.services.token_service import (
    TokenEncryptionError,
    TokenService,
)
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import SpotifyTokenError

# Sentinel token used when constructing a SpotifyAPI with only a
# refresh token. The access_token is intentionally invalid so the
# client triggers an auto-refresh on the first API call.
_EXPIRED_ACCESS_TOKEN = "expired_placeholder"


class JobExecutionError(Exception):
    """Raised when a scheduled job fails to execute."""


class PlaylistVerificationError(JobExecutionError):
    """Raised when post-write playlist state diverges from expected.

    Compares actual URIs to expected URIs as multisets so duplicate
    track counts must also match. Callers read `missing`, `extra`,
    `playlist_id`, `phase`, and `schedule_id` directly off the instance.
    """

    def __init__(
        self,
        playlist_id: str,
        expected: List[str],
        actual: List[str],
        schedule_id: int,
        phase: str,
    ):
        self.playlist_id = playlist_id
        self.expected = expected
        self.actual = actual
        self.schedule_id = schedule_id
        self.phase = phase

        exp_counter = Counter(expected)
        act_counter = Counter(actual)
        self.missing = list((exp_counter - act_counter).elements())
        self.extra = list((act_counter - exp_counter).elements())

        super().__init__(
            f"Schedule {schedule_id}: {phase} verification failed "
            f"— expected {len(expected)} tracks, got "
            f"{len(actual)}, missing {len(self.missing)}, "
            f"extra {len(self.extra)}"
        )


class JobExecutorService:
    """Builds the Spotify client that background work uses."""

    @staticmethod
    def _get_spotify_api(user: User) -> SpotifyAPI:
        """
        Create a SpotifyAPI client using the user's stored
        refresh token.

        Raises:
            JobExecutionError: If token decryption or refresh
                fails.
        """
        if not user.encrypted_refresh_token:
            raise JobExecutionError(
                f"User {user.spotify_id} has no stored "
                f"refresh token. User must log in to enable "
                f"scheduled operations."
            )

        try:
            refresh_token = TokenService.decrypt_token(user.encrypted_refresh_token)
        except TokenEncryptionError as e:
            raise JobExecutionError(f"Failed to decrypt refresh token for user {user.spotify_id}: {e}")

        try:
            from flask import current_app

            credentials = SpotifyCredentials.from_flask_config(current_app.config)
            auth_manager = SpotifyAuthManager(credentials)

            # Create a token_info with an expired access token
            # and valid refresh token. SpotifyAPI will
            # auto-refresh on first call.
            token_info = TokenInfo(
                access_token=_EXPIRED_ACCESS_TOKEN,
                token_type="Bearer",
                expires_at=0,
                refresh_token=refresh_token,
            )

            # Inject the Redis cache (None when Redis is unavailable) so
            # background jobs share the cached playlist/user data.
            from shuffify import get_spotify_cache

            def store_rotated_refresh_token(new_token: TokenInfo) -> None:
                # Spotify may issue a new refresh token on any refresh -- the
                # one at construction, a 401 retry, an expiry mid-job -- and
                # the old one can stop working. Store each the moment it
                # arrives, or the user's next job fails until they log in.
                nonlocal refresh_token
                if not new_token.refresh_token or new_token.refresh_token == refresh_token:
                    return
                user.encrypted_refresh_token = TokenService.encrypt_token(new_token.refresh_token)
                # On failure safe_commit rolls back, logs and raises. SpotifyAPI
                # swallows that, so the job runs on, and the next refresh,
                # still differing from the stored token, retries the store.
                safe_commit(f"store the rotated refresh token for user {user.spotify_id}")
                refresh_token = new_token.refresh_token

            return SpotifyAPI(
                token_info,
                auth_manager,
                auto_refresh=True,
                cache=get_spotify_cache(),
                on_token_refresh=store_rotated_refresh_token,
            )

        except SpotifyTokenError as e:
            raise JobExecutionError(f"Failed to refresh Spotify token for user {user.spotify_id}: {e}")
