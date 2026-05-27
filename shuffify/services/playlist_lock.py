"""Per-playlist execution locking via Postgres advisory locks.

Two schedules that target the same playlist and share a cron firing
time race against each other: APScheduler hands them to the executor
simultaneously and they interleave reads and writes on the same
Spotify playlist. A typical symptom is a shuffle's post-write
verification failing because a concurrent rotate added or removed
tracks inside the verification window.

This module provides a session-level advisory lock keyed on the
target playlist ID so that executor runs against the same playlist
serialize. The lock is acquired on a dedicated connection borrowed
from the engine pool (not the request-scoped Flask-SQLAlchemy
session) so SQLAlchemy commits inside the executor body don't
release the lock prematurely.

On SQLite (dev/test), the function is a no-op: SQLite has no
advisory-lock primitive and the dev process is single-threaded
anyway, so racing is impossible.
"""

import hashlib
import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from shuffify.models.db import db

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60.0

# Postgres SQLSTATE for "lock_not_available" — raised by
# pg_advisory_lock when the session's `lock_timeout` elapses
# before the lock can be acquired.
_PG_LOCK_NOT_AVAILABLE = "55P03"


def _playlist_lock_key(playlist_id: str) -> int:
    """Map a Spotify playlist ID to a stable signed 64-bit lock key.

    ``pg_try_advisory_lock`` takes a ``bigint``. Spotify playlist IDs
    are base62 strings, so we hash to a fixed-width signed int. Blake2b
    is fast and collision-resistant; 8 bytes (64 bits) is the right
    width for bigint.
    """
    digest = hashlib.blake2b(playlist_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def _is_postgres() -> bool:
    """True iff the active SQLAlchemy bind is PostgreSQL."""
    try:
        return db.engine.dialect.name == "postgresql"
    except Exception:
        return False


@contextmanager
def playlist_lock(
    playlist_id: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Iterator[bool]:
    """Serialize executor runs against a single playlist.

    Yields ``True`` while the caller holds the lock, ``False`` if the
    timeout elapsed before another holder released it. Callers MUST
    branch on the yielded value and skip work when it is ``False`` —
    proceeding without the lock defeats the purpose.

    On non-PostgreSQL backends the context manager yields ``True``
    immediately without taking any lock (see module docstring for
    rationale).

    The advisory lock lives on a dedicated connection borrowed from
    the engine pool, not on ``db.session``. SQLAlchemy may return a
    session's connection to the pool across commits, and a
    session-scoped advisory lock is released when its connection
    returns to the pool — so reusing ``db.session`` for the lock
    would silently drop it the first time the executor commits.
    """
    if not _is_postgres():
        yield True
        return

    key = _playlist_lock_key(playlist_id)
    conn = db.engine.connect()
    acquired = False
    try:
        # Bound the lock wait server-side so contended cases cost one
        # blocking call instead of N poll roundtrips. SET LOCAL scopes
        # lock_timeout to this transaction so it resets on ROLLBACK /
        # COMMIT — without LOCAL the timeout bleeds into the pool.
        timeout_ms = max(1, int(timeout_s * 1000))
        conn.execute(text(f"SET LOCAL lock_timeout = {timeout_ms}"))
        try:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
            acquired = True
            logger.debug(
                "playlist_lock acquired: playlist_id=%s key=%d",
                playlist_id,
                key,
            )
        except OperationalError as e:
            pgcode = getattr(getattr(e, "orig", None), "pgcode", None)
            if pgcode != _PG_LOCK_NOT_AVAILABLE:
                raise
            logger.warning(
                "playlist_lock timeout: playlist_id=%s key=%d after %.1fs",
                playlist_id,
                key,
                timeout_s,
            )
        yield acquired
    finally:
        if acquired:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                logger.debug(
                    "playlist_lock released: playlist_id=%s key=%d",
                    playlist_id,
                    key,
                )
            except Exception as e:
                logger.warning(
                    "playlist_lock release failed: playlist_id=%s key=%d err=%s",
                    playlist_id,
                    key,
                    e,
                )
        try:
            conn.close()
        except Exception:
            pass
