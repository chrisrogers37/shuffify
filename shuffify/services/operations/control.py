"""Durable worker control cannot be converted into a business-service outcome."""

from shuffify.spotify.observation import current_observer


def read_retry_delay(error, *, include_deadline=True):
    """Recognize safe read exhaustion without treating authorization or quota as transient."""
    from shuffify.spotify.deadline import SpotifyDeadlineError
    from shuffify.spotify.exceptions import (
        SpotifyPartialBatchError,
        SpotifyQuotaExceededError,
        SpotifyRateLimitError,
        SpotifyReadTransientError,
        SpotifyWriteUnverifiedError,
    )

    current, seen, delay = error, set(), None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (SpotifyQuotaExceededError, SpotifyPartialBatchError, SpotifyWriteUnverifiedError)):
            return None
        if isinstance(current, SpotifyRateLimitError):
            delay = max(1, 60 if current.retry_after is None else current.retry_after)
        elif (isinstance(current, SpotifyReadTransientError)
              or include_deadline and isinstance(current, SpotifyDeadlineError)):
            delay = 5 if delay is None else delay
        current = current.__cause__ or current.__context__ or getattr(current, "cause", None)
    return delay


def propagate_control(error):
    # Local imports avoid loading the database/operations layer on ordinary Spotify reads.
    from .journal import NeedsAttention, OperationAborted, OperationDeferred
    from .repository import LeaseLost

    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (LeaseLost, OperationDeferred, OperationAborted, NeedsAttention)):
            raise current
        current = current.__cause__ or current.__context__ or getattr(current, "cause", None)
    observer = current_observer()
    if (observer is not None and hasattr(observer, 'defer_read')
            and read_retry_delay(error, include_deadline=False) is not None):
        observer.defer_read(error)
    if observer is not None and hasattr(observer, 'check_pending_control'):
        observer.check_pending_control()


def propagate_refused_refresh(error, journal):
    """Re-raise ``error`` when a token refresh the database refused caused it, so the worker defers the run.

    A command that settles its own read failures still lets this one through:
    the stored refresh token could not be read just now, and nothing about the
    command has failed. It does so only while no write is ambiguous, as the
    worker defers a failed read only then.
    """
    from shuffify.spotify.exceptions import SpotifyRefreshUnavailable

    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, SpotifyRefreshUnavailable):
            if not journal.has_ambiguous_write():
                raise error
            return
        current = current.__cause__ or current.__context__ or getattr(current, "cause", None)
