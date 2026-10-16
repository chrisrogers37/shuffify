"""Monotonic Spotify I/O budgets shared by retries, pages and token refresh."""

import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .exceptions import SpotifyAPIError

_deadline: ContextVar[float | None] = ContextVar('spotify_deadline', default=None)


class SpotifyDeadlineError(SpotifyAPIError):
    """The caller's Spotify I/O budget is exhausted before another safe attempt."""


@contextmanager
def spotify_deadline(seconds: float) -> Iterator[None]:
    """Bound a complete Spotify workflow; nested scopes cannot extend its budget."""
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Spotify deadline must be finite and nonnegative.')
    expires_at = time.monotonic() + seconds
    parent = _deadline.get()
    if parent is not None:
        expires_at = min(parent, expires_at)
    token = _deadline.set(expires_at)
    try:
        yield
    finally:
        _deadline.reset(token)


@contextmanager
def fresh_spotify_deadline(seconds: float) -> Iterator[None]:
    """Bound an independent compensation workflow and restore its caller's budget."""
    token = _deadline.set(None)
    try:
        with spotify_deadline(seconds):
            yield
    finally:
        _deadline.reset(token)


def remaining_seconds() -> float | None:
    """Return remaining monotonic seconds, or None when the caller set no budget."""
    expires_at = _deadline.get()
    return None if expires_at is None else max(0.0, expires_at - time.monotonic())


def check_deadline() -> None:
    """Refuse a new attempt when its enclosing Spotify workflow has expired."""
    remaining = remaining_seconds()
    if remaining is not None and remaining <= 0:
        raise SpotifyDeadlineError('Spotify request deadline exceeded.')


def remaining_timeout(default: float) -> float:
    """Cap one transport timeout without resetting the surrounding workflow budget."""
    remaining = remaining_seconds()
    if remaining is not None and remaining <= 0:
        raise SpotifyDeadlineError("Spotify request deadline exceeded.")
    return default if remaining is None else min(default, remaining)


def bounded_sleep(seconds: float, *, sleep: Callable[[float], None] | None = None) -> None:
    """Wait only when the budget leaves time for a subsequent request attempt."""
    remaining = remaining_seconds()
    if remaining is not None and seconds >= remaining:
        raise SpotifyDeadlineError('Spotify retry would exceed the request deadline.')
    (sleep or time.sleep)(seconds)
    check_deadline()
