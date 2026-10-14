"""Canonical review bindings shared by a review and its run.

A binding ties a reviewed Shuffle, Undo or visibility run, a snapshot restore
or an executor action to what was reviewed; the executor actions' scope inputs
live in ``live_review_scope``. A binding is a comparison
precondition only: the mutation still freshly authorizes and re-derives every
fact before comparing. Digests are not secrets, signatures, durable IDs or
replay protection, and a matching digest never grants access. Review and run
derive each binding through the helpers here, so the two sides cannot drift
apart.
"""

import hashlib
import json
import re
from collections.abc import Mapping

from shuffify.services.live_operation_service import (
    LIVE_ENDPOINT_KINDS,
    DirectFailure,
    direct_rejected_result,
    rejected_result,
)

SCOPE_PURPOSE = "shuffify.live.scope.v1"
BEFORE_ORDER_PURPOSE = "shuffify.live.before-order.v1"
HISTORY_PURPOSE = "shuffify.live.undo-selection.v1"
BINDING_FIELDS = ("reviewed_scope", "reviewed_before_order", "reviewed_history")
_REQUIRED_BINDINGS = {
    "shuffle": ("reviewed_scope",), "undo": BINDING_FIELDS, "privacy": ("reviewed_scope",),
    "snapshot_restore": ("reviewed_scope", "reviewed_before_order"),
    "raid": ("reviewed_scope",), "drip": ("reviewed_scope",), "raid_and_drip": ("reviewed_scope",),
    "schedule": ("reviewed_scope",),
}
# Shuffle and Undo post forms; every other reviewed action posts a JSON object.
_FORM_KINDS = frozenset(("shuffle", "undo"))
# Executor runs keep their not-started envelope when refused.
_EXECUTOR_KINDS = frozenset(LIVE_ENDPOINT_KINDS.values())
# Rejections whose own message explains that the review must be loaded again.
_REVIEW_REJECTIONS = ("invalid_review_binding", "review_changed")
# A lowercase hex SHA-256 digest, as fingerprint returns it.
DIGEST = re.compile(r"[0-9a-f]{64}")

REVIEW_CHANGED = "The reviewed action changed. Review it again."
HISTORY_CHANGED = "The saved version history changed after your review. Review it again."
RESOURCE_CHANGED = "The playlist changed after your review. Review its current contents again."
# Each binding a direct run compares, in comparison order, with its no-dispatch rejection.
_COMPARISONS = (
    ("reviewed_scope", "review_changed", REVIEW_CHANGED),
    ("reviewed_history", "history_changed", HISTORY_CHANGED),
    ("reviewed_before_order", "resource_changed", RESOURCE_CHANGED),
)


def fingerprint(value):
    """Canonical lowercase SHA-256 of one JSON value; review and run both call it."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def scope_fingerprint(kind, target_id, action):
    return fingerprint({"purpose": SCOPE_PURPOSE, "kind": kind, "target_id": target_id, "action": action})


def shuffle_scope(target_id, algorithm, params):
    return scope_fingerprint("shuffle", target_id, {"algorithm": algorithm, "params": dict(params or {})})


def privacy_scope(target_id, public):
    return scope_fingerprint("privacy", target_id, {"public": public})


def before_order_fingerprint(target_id, track_uris):
    return fingerprint({"purpose": BEFORE_ORDER_PURPOSE, "target_id": target_id, "track_uris": list(track_uris)})


def history_fingerprint(target_id, selection):
    return fingerprint({
        "purpose": HISTORY_PURPOSE,
        "target_id": target_id,
        "states": [list(uris) for uris in selection.states],
        "current_index": selection.current_index,
    })


def undo_bindings(target_id, selection, track_uris):
    """Undo's three bindings for one copied history selection and current order."""
    return {
        "reviewed_scope": scope_fingerprint("undo", target_id, {}),
        "reviewed_history": history_fingerprint(target_id, selection),
        "reviewed_before_order": before_order_fingerprint(target_id, track_uris),
    }


def snapshot_bindings(target_id, snapshot_id, desired_track_uris, track_uris):
    """A snapshot restore's two bindings: the saved order it restores and the current order it replaces."""
    return {
        "reviewed_scope": scope_fingerprint(
            "snapshot_restore", target_id,
            {"snapshot_id": snapshot_id, "desired_track_uris": list(desired_track_uris)},
        ),
        "reviewed_before_order": before_order_fingerprint(target_id, track_uris),
    }


def require_bindings(reviewed, actual):
    """Refuse a reviewed direct run, before any dispatch, at the first binding it no longer matches.

    ``actual`` holds the run's own fresh bindings; they are compared in a fixed
    order (scope, history, before-order).
    """
    for field, code, message in _COMPARISONS:
        if field in actual and reviewed[field] != actual[field]:
            raise DirectFailure(409, code, message)


class ReviewBindingError(ValueError):
    """A reviewed request carried an incomplete, misplaced or malformed binding."""


def form_binding(form, kind):
    """The reviewed binding in a form body, or None for a legacy request."""
    return _binding(kind, {name: form.getlist(name) for name in BINDING_FIELDS if name in form})


def json_binding(data, kind):
    """The reviewed binding in a JSON object body, or None for a legacy request."""
    if not isinstance(data, dict):
        return None
    return _binding(kind, {name: [data[name]] for name in BINDING_FIELDS if name in data})


def reject_misplaced(*sources):
    """A binding outside the action's own body is malformed, never a legacy request."""
    if any(isinstance(source, Mapping) and any(name in source for name in BINDING_FIELDS) for source in sources):
        raise ReviewBindingError("Review binding outside the request body")


def request_binding(request, kind):
    """The reviewed binding a mutation request carries, or None for a legacy request.

    Shuffle and Undo carry it in their form, every other action in its JSON
    object body. A malformed or misplaced binding raises ReviewBindingError,
    so the route refuses it before any database or Spotify read.
    """
    if kind in _FORM_KINDS:
        reject_misplaced(request.args, request.get_json(silent=True))
        return form_binding(request.form, kind)
    reject_misplaced(request.args, request.form)
    return json_binding(request.get_json(silent=True), kind)


def _binding(kind, submitted):
    if not submitted:
        return None
    if set(submitted) != set(_REQUIRED_BINDINGS[kind]) or any(
        len(values) != 1 or not isinstance(values[0], str) or not DIGEST.fullmatch(values[0])
        for values in submitted.values()
    ):
        raise ReviewBindingError("Invalid review binding")
    return {name: values[0] for name, values in submitted.items()}


def invalid_binding_result(kind):
    """A malformed binding, refused before any read; executor runs keep their not-started envelope."""
    reject = rejected_result if kind in _EXECUTOR_KINDS else direct_rejected_result
    return reject(
        kind, error_code="invalid_review_binding", http_status=400,
        message="This reviewed action could not be matched to its review. Review it again.",
    )


def review_message(result):
    """A reviewed rejection's own safe copy; other outcomes keep their route's copy."""
    operation = result.get("operation") if isinstance(result, dict) else None
    code = operation.get("error_code") if isinstance(operation, dict) else None
    return result.get("message") if code in _REVIEW_REJECTIONS else None
