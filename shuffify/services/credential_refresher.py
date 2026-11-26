"""Refresh a person's Spotify access token from the refresh token stored in their users row.

The users row is the refresh token's only home: the session keeps none, and the
web and the worker both refresh through ``CredentialRefresher``. Spotify may
answer a refresh with a new refresh token and stop accepting the one it
replaced, so a refresh reads the stored token, refreshes and stores the new
one as one serialised step: a short transaction of its own that locks the row
and commits before the refresh returns. Nothing holds a lock around a whole
run for it.
"""

import logging
import threading
from dataclasses import replace
from typing import Callable, Optional, Union

from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from shuffify.models.db import User, db
from shuffify.services.token_service import TokenEncryptionError, TokenService
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.exceptions import (
    SpotifyAppRejected,
    SpotifyGrantRevoked,
    SpotifyRefreshUnavailable,
    SpotifyTokenError,
)

logger = logging.getLogger(__name__)

# How long a refresh waits for another transaction's lock on the row, in
# seconds. A refresh that would wait longer raises SpotifyRefreshUnavailable,
# with this as its retry_after.
LOCK_TIMEOUT_SECONDS = 10

# The access token of a client built without one: already expired, so the
# client refreshes from the stored refresh token before its first call.
_EXPIRED_ACCESS_TOKEN = "expired_placeholder"


def expired_token() -> TokenInfo:
    """A token whose access token has expired, for a client that must refresh first."""
    return TokenInfo(access_token=_EXPIRED_ACCESS_TOKEN, token_type="Bearer", expires_at=0)


class _KeptToken:
    """A rotated refresh token this process has not stored, and the stored ciphertext it replaces."""

    __slots__ = ("token", "replaces")

    def __init__(self, token: str, replaces: str):
        self.token = token
        self.replaces = replaces

    def __repr__(self) -> str:
        return "<_KeptToken>"


# Rotated refresh tokens this process has not stored, by users row id. Each is
# kept from the moment Spotify issues it, under the row lock, so the row's next
# refresh in this process uses it, one already waiting for the lock included,
# while the stored token is still the one it replaced. It is dropped once a
# store succeeds or the stored token changes. The other process can't see them.
_kept: dict = {}
_kept_lock = threading.Lock()


def _kept_for(user_id: int, stored: str) -> Optional[str]:
    """The kept token for this row while it still replaces ``stored``; a stale one is dropped."""
    with _kept_lock:
        kept = _kept.get(user_id)
        if kept is not None and kept.replaces != stored:
            del _kept[user_id]
            kept = None
        return kept.token if kept is not None else None


def _keep(user_id: int, kept: _KeptToken) -> None:
    with _kept_lock:
        _kept[user_id] = kept


def _holds(user_id: int, kept: _KeptToken) -> bool:
    """Whether ``kept`` is still the row's kept token, not used since by a later refresh that kept its own."""
    with _kept_lock:
        return _kept.get(user_id) is kept


def _forget(user_id: int, kept: _KeptToken) -> None:
    """Drop ``kept`` while it is the row's kept token; one a later refresh kept in its place stays."""
    with _kept_lock:
        if _kept.get(user_id) is kept:
            del _kept[user_id]


def _bound_lock_wait(storage: Session) -> None:
    """Set the lock timeout for this transaction only.

    Production connects through a transaction-mode pooler, where a
    session-level setting would stay on the server connection and reach
    whichever client gets it next.
    """
    if storage.get_bind().dialect.name == "postgresql":
        storage.execute(
            text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": f"{LOCK_TIMEOUT_SECONDS * 1000}ms"},
        )


def _locked_stored_token(storage: Session, user_id: int) -> Optional[str]:
    """Lock the users row and read its stored refresh token.

    FOR NO KEY UPDATE, the lock ``lock_user_inventory`` takes
    (services/base.py), so inserts into the tables that reference the user
    don't wait for the refresh.
    """
    return storage.execute(
        select(User.encrypted_refresh_token).where(User.id == user_id).with_for_update(key_share=True)
    ).scalar_one_or_none()


def _store(storage: Session, user_id: int, refresh_token: str) -> None:
    storage.execute(
        update(User).where(User.id == user_id).values(
            encrypted_refresh_token=TokenService.encrypt_token(refresh_token)
        )
    )


def _decrypt(stored: str, user_id: int) -> str:
    try:
        return TokenService.decrypt_token(stored)
    except TokenEncryptionError as error:
        raise SpotifyTokenError(f"The stored refresh token for user {user_id} cannot be decrypted") from error


def _unavailable(error: SQLAlchemyError, user_id: Optional[int]) -> SpotifyRefreshUnavailable:
    logger.warning("Token refresh for user %s could not use the database (%s)", user_id, type(error).__name__)
    return SpotifyRefreshUnavailable(
        "The stored refresh token could not be read just now.", retry_after=LOCK_TIMEOUT_SECONDS,
    )


class CredentialRefresher:
    """Refresh a person's access token from the refresh token stored in their users row.

    ``SpotifyAPI`` takes it in place of the bare ``SpotifyAuthManager``. It
    calls only ``refresh_token`` and ``ensure_valid_token``, and the refresh
    token in the token it passes is ignored. The token it gets back carries no
    refresh token, so no client holds one.

    Each refresh is one transaction: the lock timeout set for it alone, the
    users row locked, the stored token read and refreshed with Spotify, a
    rotated token stored, then commit, which releases the lock. Any database
    error in it raises ``SpotifyRefreshUnavailable``, which is transient.

    ``user_id`` is the users row, or a callable that finds it when a refresh
    first needs it and returns None when the person has none.
    ``on_typed_failure`` is called with a ``SpotifyAppRejected`` or
    ``SpotifyGrantRevoked`` once the refresh's transaction has ended.
    """

    def __init__(
        self,
        auth_manager: SpotifyAuthManager,
        user_id: Union[Optional[int], Callable[[], Optional[int]]],
        *,
        on_typed_failure: Optional[Callable[[Exception], None]] = None,
    ):
        self._auth_manager = auth_manager
        self._user_id = user_id
        self._on_typed_failure = on_typed_failure

    def ensure_valid_token(self, token_info: TokenInfo) -> TokenInfo:
        """``token_info`` while its access token is unexpired, else a refreshed token."""
        if not token_info.is_expired:
            return token_info
        logger.info("Token expired, refreshing from the stored refresh token")
        return self.refresh_token(token_info)

    def refresh_token(self, token_info: Optional[TokenInfo] = None) -> TokenInfo:
        """Refresh with the stored refresh token, whatever ``token_info`` holds, and store a rotated one.

        Raises:
            SpotifyRefreshUnavailable: The database refused it: a lock
                timeout or a failed connection.
            SpotifyAppRejected: Spotify refused the app (``invalid_client``).
            SpotifyGrantRevoked: Spotify refused the stored token (``invalid_grant``).
            SpotifyTokenError: There is no stored token, or Spotify refused
                the refresh otherwise.
        """
        try:
            return self._refresh()
        except (SpotifyAppRejected, SpotifyGrantRevoked) as failure:
            # The transaction has ended, so a hook that updates this row
            # doesn't wait for its lock.
            self._report(failure)
            raise

    def _row(self) -> int:
        user_id = self._user_id
        if callable(user_id):
            try:
                user_id = user_id()
            except SQLAlchemyError as error:
                raise _unavailable(error, None) from error
        if user_id is None:
            raise SpotifyTokenError("There is no stored refresh token to refresh with")
        return user_id

    def _refresh(self) -> TokenInfo:
        user_id = self._row()
        rotated = None
        try:
            with Session(db.engine) as storage, storage.begin():
                _bound_lock_wait(storage)
                stored = _locked_stored_token(storage, user_id)
                if not stored:
                    raise SpotifyTokenError(f"User {user_id} has no stored refresh token")
                kept = _kept_for(user_id, stored)
                presented = kept if kept is not None else _decrypt(stored, user_id)
                fresh = self._auth_manager.refresh_token(replace(expired_token(), refresh_token=presented))
                # Store what Spotify answered with whenever the stored token is
                # behind it: after a rotation, or while a kept token stands in.
                if kept is not None or fresh.refresh_token != presented:
                    rotated = _KeptToken(fresh.refresh_token, stored)
                    # Kept while the row is locked, before the commit can fail: a
                    # refresh waiting for the lock takes it as soon as this one ends.
                    _keep(user_id, rotated)
                    _store(storage, user_id, fresh.refresh_token)
        except SQLAlchemyError as error:
            # rotated is set only once Spotify has answered, so fresh is bound whenever the refresh goes on.
            if rotated is None:
                raise _unavailable(error, user_id) from error
            self._store_again(user_id, rotated, error)
        else:
            if rotated is not None:
                _forget(user_id, rotated)
        return replace(fresh, refresh_token=None)

    @staticmethod
    def _store_again(user_id: int, rotated: _KeptToken, error: SQLAlchemyError) -> None:
        """Spotify has rotated the token and storing it failed: try once more.

        The token stays kept in this process until a store succeeds or a newer
        token takes its place.
        """
        logger.warning(
            "Storing the rotated refresh token for user %s failed (%s); trying once more",
            user_id, type(error).__name__,
        )
        try:
            with Session(db.engine) as storage, storage.begin():
                _bound_lock_wait(storage)
                if _locked_stored_token(storage, user_id) != rotated.replaces or not _holds(user_id, rotated):
                    # A newer token was stored since, or a later refresh here used this one and kept its own.
                    _forget(user_id, rotated)
                    return
                _store(storage, user_id, rotated.token)
        except SQLAlchemyError as again:
            logger.warning(
                "Storing the rotated refresh token for user %s failed again (%s); keeping it in this process",
                user_id, type(again).__name__,
            )
        else:
            _forget(user_id, rotated)

    def _report(self, failure: Exception) -> None:
        """Hand a typed failure to the hook, which can't change it."""
        if self._on_typed_failure is None:
            return
        try:
            self._on_typed_failure(failure)
        except Exception as error:
            logger.warning("The typed failure hook failed: %s", type(error).__name__)
