"""Durable worker control cannot be converted into a business-service outcome."""

from shuffify.spotify.observation import current_observer


def propagate_control(error):
    # Local imports avoid loading the database/operations layer on ordinary Spotify reads.
    from .journal import NeedsAttention, OperationAborted, OperationDeferred
    from .repository import LeaseLost

    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (LeaseLost, OperationDeferred, OperationAborted, NeedsAttention)):
            raise current
        current = current.__cause__ or current.__context__
    observer = current_observer()
    if observer is not None and hasattr(observer, 'check_pending_control'):
        observer.check_pending_control()


def guard_legacy_compensation():
    observer = current_observer()
    if observer is not None and hasattr(observer, 'request_abort'):
        observer.request_abort('legacy_compensation_requested')
