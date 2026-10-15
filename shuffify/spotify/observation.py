"""Invocation-local observation around high-level plans and actual HTTP attempts."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_OBSERVER = ContextVar("spotify_mutation_observer", default=None)


def current_observer():
    return _OBSERVER.get()


@contextmanager
def observe_mutations(observer):
    token = _OBSERVER.set(observer)
    try:
        yield observer
    finally:
        _OBSERVER.reset(token)


def observed_mutation(kind):
    """Let a durable observer freeze and execute a complete high-level mutation."""
    def decorate(method):
        @wraps(method)
        def observed(api, *args, **kwargs):
            observer = current_observer()
            if observer is not None:
                return observer.mutate(api, kind, *args, **kwargs)
            return method(api, *args, **kwargs)
        return observed
    return decorate
