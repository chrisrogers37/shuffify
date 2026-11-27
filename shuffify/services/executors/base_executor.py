"""Spotify credentials for background work, and the errors the durable operations raise.

``JobExecutorService._get_spotify_api`` builds the Spotify client the worker uses for a user's stored
refresh token. ``JobExecutionError`` and ``PlaylistVerificationError`` are what the operation executors
and live actions raise.
"""

from collections import Counter
from typing import List

from shuffify.models.db import User
from shuffify.services.credential_refresher import CredentialRefresher, expired_token
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager
from shuffify.spotify.credentials import credentials_for
from shuffify.spotify.exceptions import SpotifyTokenError


class JobExecutionError(Exception):
    """Raised when a scheduled job fails to execute.

    ``cause`` is the Spotify failure that triggered it, if any, so the run's
    error code can name it (``spotify_failure_of``).
    """

    def __init__(self, *args, cause: BaseException | None = None):
        super().__init__(*args)
        self.cause = cause


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
        Create a SpotifyAPI client that refreshes from the user's stored
        refresh token.

        The client starts with an expired access token, so it refreshes
        before its first call. Every refresh, the first included, goes through
        ``CredentialRefresher``, which reads the token stored in the users row
        and stores a rotated one.

        Raises:
            JobExecutionError: If there is no stored token, or the refresh
                fails.
            SpotifyRefreshUnavailable: If the stored token can't be reached
                just now. It is transient, so the run is deferred.
        """
        if not user.encrypted_refresh_token:
            raise JobExecutionError(
                f"User {user.id} has no stored "
                f"refresh token. User must log in to enable "
                f"scheduled operations."
            )

        try:
            refresher = CredentialRefresher(SpotifyAuthManager(credentials_for(user)), user.id)

            # Inject the Redis cache (None when Redis is unavailable) so
            # background jobs share the cached playlist/user data.
            from shuffify import get_spotify_cache

            return SpotifyAPI(
                expired_token(),
                refresher,
                auto_refresh=True,
                cache=get_spotify_cache(),
            )

        except SpotifyTokenError as e:
            raise JobExecutionError("Failed to refresh the Spotify token.", cause=e) from e
