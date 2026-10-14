"""Per-playlist execution and configuration locking.

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

Configuration writes use a separate namespace so a running Collect can be
paused or resumed. Operations that need both locks acquire execution first.
On other databases keyed threading locks coordinate callers in this process
only. They cannot serialize other processes or external Spotify writers.
"""

import asyncio
import hashlib
import inspect
import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from shuffify.models.db import db

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60.0
MUTATION_TIMEOUT_S = 2.0
_local_guard = threading.Lock()
# Entries count holders and waiters, so unused keys can be safely discarded.
_local_locks = {}
_held_targets = ContextVar("playlist_lock_held_targets", default=frozenset())


class PlaylistBusyError(RuntimeError):
    """A target mutation could not acquire its playlist lock in time."""

    error_code = "playlist_busy"


def _owner():
    """Identify a logical caller without sharing ownership across tasks/threads."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.current_thread(), task


@contextmanager
def _mark_owned(key, acquired):
    if not acquired:
        yield False
        return
    token = _held_targets.set(_held_targets.get() | {key})
    try:
        yield True
    finally:
        _held_targets.reset(token)


def locked_commit_target(target_argument, *, fallback=None):
    """Lock a committing mutator's full read/write/commit body by target.

    ``commit=False`` belongs to a caller transaction and deliberately does
    not acquire a second lock. A fallback may read only to discover the target;
    the mutator must repeat its authoritative lookup while holding the lock.
    """
    def decorate(func):
        signature = inspect.signature(func)

        @wraps(func)
        def wrapper(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            if not bound.arguments["commit"]:
                return func(*args, **kwargs)
            target = bound.arguments.get(target_argument)
            inferred = False
            if target is None and fallback is not None:
                target = fallback(bound.arguments)
                # The locked call must validate the exact target inferred
                # before waiting; an ID could be deleted and reused elsewhere.
                bound.arguments[target_argument] = target
                inferred = True
            with playlist_configuration_lock(target, timeout_s=MUTATION_TIMEOUT_S) as acquired:
                if not acquired:
                    raise PlaylistBusyError("This playlist is busy. Try again shortly.")
                if inferred:
                    return func(*bound.args, **bound.kwargs)
                return func(*args, **kwargs)

        return wrapper

    return decorate


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
def _target_lock(playlist_id: str, namespace: str, timeout_s: float) -> Iterator[bool]:
    """Acquire one target in the execution or configuration namespace.

    Yields ``True`` while the caller holds the lock, ``False`` if the
    timeout elapsed before another holder released it. Callers MUST
    branch on the yielded value and skip work when it is ``False`` —
    proceeding without the lock defeats the purpose.

    Non-PostgreSQL backends use a process-local keyed mutex with the same
    timeout contract. This fallback does not coordinate separate processes.

    The advisory lock lives on a dedicated connection borrowed from
    the engine pool, not on ``db.session``. SQLAlchemy may return a
    session's connection to the pool across commits, and a
    session-scoped advisory lock is released when its connection
    returns to the pool — so reusing ``db.session`` for the lock
    would silently drop it the first time the executor commits.
    """
    ownership_key = (_owner(), namespace, playlist_id)
    if ownership_key in _held_targets.get():
        yield True
        return

    local_key = playlist_id if namespace == "execution" else (namespace, playlist_id)
    if not _is_postgres():
        with _local_guard:
            lock, users = _local_locks.get(local_key, (threading.Lock(), 0))
            _local_locks[local_key] = (lock, users + 1)
        acquired = False
        try:
            acquired = lock.acquire(timeout=max(0.0, timeout_s))
            with _mark_owned(ownership_key, acquired) as held:
                yield held
        finally:
            if acquired:
                lock.release()
            with _local_guard:
                _, users = _local_locks[local_key]
                if users == 1:
                    del _local_locks[local_key]
                else:
                    _local_locks[local_key] = (lock, users - 1)
        return

    key = _playlist_lock_key(
        playlist_id if namespace == "execution" else f"configuration:{playlist_id}"
    )
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
                "playlist_lock acquired: namespace=%s playlist_id=%s key=%d",
                namespace,
                playlist_id,
                key,
            )
        except OperationalError as e:
            pgcode = getattr(getattr(e, "orig", None), "pgcode", None)
            if pgcode != _PG_LOCK_NOT_AVAILABLE:
                raise
            logger.warning(
                "playlist_lock timeout: namespace=%s playlist_id=%s key=%d after %.1fs",
                namespace,
                playlist_id,
                key,
                timeout_s,
            )
        with _mark_owned(ownership_key, acquired) as held:
            yield held
    finally:
        if acquired:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                logger.debug(
                    "playlist_lock released: namespace=%s playlist_id=%s key=%d",
                    namespace,
                    playlist_id,
                    key,
                )
            except Exception as e:
                logger.warning(
                    "playlist_lock release failed: namespace=%s playlist_id=%s key=%d err=%s",
                    namespace,
                    playlist_id,
                    key,
                    e,
                )
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def playlist_lock(
    playlist_id: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Iterator[bool]:
    """Serialize executions against one target playlist."""
    with _target_lock(playlist_id, "execution", timeout_s) as acquired:
        yield acquired


@contextmanager
def playlist_configuration_lock(
    playlist_id: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Iterator[bool]:
    """Serialize configuration writes independently of an active execution."""
    with _target_lock(playlist_id, "configuration", timeout_s) as acquired:
        yield acquired
