"""Pieces every live-action review context shares.

A review (``live_action_context_service`` for Library actions and snapshot
restore, ``live_executor_context_service`` for Workshop and routine executor
actions) only reads: it names what a run would change, with fresh authorized
names, and the bindings that run compares again. A review that cannot load
changed nothing and recorded nothing.
"""

import logging

from shuffify.services.live_operation_service import (
    DirectFailure,
    direct_rejected_result,
    direct_response_payload,
)
from shuffify.services.live_playlist_service import LivePlaylistService

logger = logging.getLogger(__name__)


def context_rejected(error_code, http_status, message, retryable=False, kind="live_context"):
    """A review that could not load; nothing was changed or recorded."""
    result = direct_rejected_result(
        kind, error_code=error_code, http_status=http_status, retryable=retryable, message=message
    )
    return direct_response_payload(result, kind)


def failure_rejected(kind, failure):
    """A review refused by one DirectFailure."""
    return context_rejected(failure.code, failure.status, failure.message, failure.retryable, kind)


def context_unavailable(kind="live_context"):
    """A review that failed unexpectedly; nothing was changed."""
    return context_rejected(
        "preflight_failed", 502, "The review could not be loaded. Nothing was changed; load it again.", kind=kind,
    )


def read_failure(error, noun="playlist"):
    """The refusal of a review whose fresh read of one playlist failed."""
    status, code, message, retryable = LivePlaylistService.preflight_failure(error)
    if code == "preflight_failed":
        logger.warning("A review could not read the %s", noun, exc_info=True)
        message = f"The {noun} could not be read. Nothing was changed; load the review again."
    return DirectFailure(status, code, message, retryable)


def playlist_name(value):
    """A fresh playlist name to show, or "Playlist" when it has none."""
    return value if isinstance(value, str) and value.strip() else "Playlist"


def resource_entry(resource_id, name, role, access, mutation_type, field=None):
    """One resource a review names: a write, or a Spotify read for exclusions."""
    return {
        "resource": resource_id,
        "resource_name": name,
        "role": role,
        "domain": "inbox" if role == "inbox" else "spotify",
        "access": access,
        "mutation_type": mutation_type,
        "field": field,
    }


def review_context(kind, label, target_id, name, *, resources, steps, bookkeeping, scope, current, action,
                   schedule=None, snapshot=None, before_order=None):
    """One snapshot or executor review context; Shuffle, Undo and visibility keep their own exact shape."""
    return {
        "version": 1,
        "kind": kind,
        "label": label,
        "target": {"id": target_id, "name": name},
        "schedule": schedule,
        "snapshot": snapshot,
        "resources": resources,
        "steps": list(steps),
        "required_bookkeeping": list(bookkeeping),
        "review_binding": {"version": 1, "scope": scope, "before_order": before_order, "history": None},
        "current": current,
        "action": action,
    }
