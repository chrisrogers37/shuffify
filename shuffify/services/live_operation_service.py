"""Invocation-local live-operation facts, never a durable receipt or retry queue.

Only participating target writers hold a playlist lock. These observations do
not serialize shared sources, other tabs, or external Spotify edits.
"""

import logging
import time
from collections import Counter
from contextvars import ContextVar
from copy import deepcopy

from flask import current_app

from shuffify.services.execution_result_service import serialize_execution_detail
from shuffify.services.workshop_commit_service import WorkshopCommitService
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyRateLimitError,
    SpotifyWriteRejectedError,
)

logger = logging.getLogger(__name__)

_CURRENT = ContextVar("live_operation", default=None)
_RUN_BOOKKEEPING = frozenset(("execution_record", "coordination"))
_REJECTED = (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyRateLimitError,
    SpotifyWriteRejectedError,
)


def _has_mutation_effects(resources, bookkeeping):
    """Known or unresolved playlist/app effects; receipts alone are not effects."""
    return any(r.get("dispatched") and r["status"] != "failed" for r in resources) or any(
        b["kind"] not in _RUN_BOOKKEEPING
        and b["status"] in ("complete", "restored", "unknown")
        and b.get("affected_count") != 0
        for b in bookkeeping
    )


def operation_result(kind, *, resources=(), bookkeeping=(), error_code=None, retryable=False, steps=None):
    """Build the additive result without losing earlier resource progress."""
    resources, bookkeeping = deepcopy(list(resources)), deepcopy(list(bookkeeping))
    states = [item["status"] for item in resources + bookkeeping]
    for step in steps or ():
        states.append(step.get("aggregate_status", step.get("status")))
    if "unknown" in states:
        aggregate = "unknown"
    elif error_code or any(s not in ("complete", "not_required", "success") for s in states):
        aggregate = "partial"
    else:
        aggregate = "complete"
    applied = _has_mutation_effects(resources, bookkeeping)
    result = {
        "kind": str(kind),
        "aggregate_status": aggregate,
        "resources": resources,
        "bookkeeping": bookkeeping,
        "error_code": error_code,
        "retryable": bool(retryable and not applied and aggregate != "unknown"),
    }
    if steps is not None:
        result["steps"] = deepcopy(steps)
    return result


def rejected_result(
    kind, *, error_code="preflight_failed", retryable=False, unknown=False, http_status=400, message=None
):
    """Format a rejection without auth, database, or Spotify reads.

    unknown=True is for an unavailable prior invocation result, not evidence
    that no execution row or Spotify dispatch happened.
    """
    operation = operation_result(kind, error_code=error_code, retryable=retryable)
    if unknown:
        operation.update(aggregate_status="unknown", retryable=False)
    message = message or (
        "This run could not be verified. Do not run it again to recover."
        if unknown
        else "This attempt made no playlist changes."
    )
    return {
        "status": "failed",
        "execution_id": None,
        "started_at": None,
        "execution_state": "unknown" if unknown else "not_started",
        "result_detail": serialize_execution_detail(None),
        "tracks_added": None if unknown else 0,
        "tracks_total": None if unknown else 0,
        "operation": operation,
        "http_status": http_status,
        "message": message,
        "error": message,
    }


# Immediate executor endpoints and the live-operation kind each reports.
LIVE_ENDPOINT_KINDS = {
    "main.raid_now": "raid",
    "main.drip_now": "drip",
    "main.raid_and_drip": "raid_and_drip",
    "main.run_schedule_now": "schedule",
}


def preflight_error_code(status_code):
    if status_code == 401:
        return "authentication_required"
    if status_code == 503:
        return "database_unavailable"
    return "preflight_failed"


def normalized_operation(result, kind):
    """Return result's operation, or unknown when missing or contradictory.

    A returned dict without verification facts, or a "complete" operation
    beside a non-success legacy status, cannot establish a completed run.
    """
    operation = result.get("operation")
    if not isinstance(operation, dict):
        return rejected_result(kind, unknown=True, error_code="outcome_unavailable")["operation"]
    if operation.get("aggregate_status") == "complete" and result.get("status") != "success":
        return {**operation, "aggregate_status": "unknown", "error_code": "outcome_inconsistent", "retryable": False}
    return operation


def error_status(result):
    """The result's 4xx/5xx status, else 502 for an incomplete outcome."""
    status_code = result.get("http_status", 502)
    if not isinstance(status_code, int) or not 400 <= status_code <= 599:
        return 502
    return status_code


def current_operation():
    return _CURRENT.get()


def read_tracks(api, playlist_id, **kwargs):
    """Complete authorized reads during a runner invocation; legacy otherwise."""
    operation = current_operation()
    if operation:
        return list(operation.read(playlist_id).tracks)
    return api.get_playlist_tracks(playlist_id, **kwargs)


def plan_tracks(api, playlist_id, desired, *, step, ordered=True, mutation_type="track_membership"):
    operation = current_operation()
    if operation:
        operation.plan(playlist_id, desired, step, ordered, mutation_type)


def write_tracks(api, method, playlist_id, uris, *, step, **kwargs):
    operation = current_operation()
    if operation:
        return operation.write(method, playlist_id, list(uris), step, **kwargs)
    return getattr(api, method)(playlist_id, uris, **kwargs)


def book_not_required(kind, resource, operation=None):
    """A verified no-op still reports its bookkeeping, which a reviewed run requires."""
    operation = operation or current_operation()
    if operation:
        operation.book(kind, "not_required", resource=resource, affected_count=0)


# The inbox bookkeeping each executor step records; a review requires each one.
STEP_BOOKKEEPING = {"raid": "pending_stage", "drip": "pending_promotion"}
# How each executor step writes its target; a Raid only reads it.
STEP_TARGET_WRITES = {"drip": "track_membership", "shuffle": "track_order", "rotate": "track_membership"}


def not_started_facts(steps, target_id, *, bookkeeping=(), resources=(), name=None):
    """(bookkeeping, resources) saying that ``steps``, which never started, changed nothing.

    Each step's inbox fact and target entry, unless already reported. A
    collection or archive needs none: in current composites one is written
    only by a step that ran, or the run had no effect at all.
    """
    booked = {(item["kind"], item["step"]) for item in bookkeeping}
    reported = {(entry["resource"], entry["step"]) for entry in resources}
    return [
        {"kind": STEP_BOOKKEEPING[step], "step": step, "status": "not_required", "error_code": None,
         "resource": target_id, "affected_count": 0}
        for step in steps if step in STEP_BOOKKEEPING and (STEP_BOOKKEEPING[step], step) not in booked
    ], [
        {"resource": target_id, "resource_name": name, "step": step, "mutation_type": STEP_TARGET_WRITES[step],
         "status": "not_required", "dispatched": False, "affected_count": 0, "retryable": False,
         "error_code": None, "verification": None, "started": False}
        for step in steps if step in STEP_TARGET_WRITES and (target_id, step) not in reported
    ]


class LiveOperation:
    """Small in-memory journal of observed resources for one locked invocation."""

    def __init__(self, kind, api, user):
        self.kind, self.api, self.user = str(kind), api, user
        self.reader = WorkshopCommitService(api)
        self.resources = []
        self.bookkeeping = []
        self.steps = []
        self.observed = {}
        self.baselines = {}
        self.resource_steps = {}
        self.unconfirmed = set()  # playlists whose failed write may still land
        self.staged_ids = []
        self.promoted_ids = []
        self.error_code = None
        self.retryable = False
        self.step = str(kind)
        self.triggered_by = "scheduler"
        self.counts = {}
        # The target the runner locked, and a reviewed run's bound step inputs
        # (``live_review_scope``); None for an unreviewed run.
        self.locked_target = None
        self.reviewed_parts = None

    def activate(self):
        return _CURRENT.set(self)

    @staticmethod
    def reset(token):
        _CURRENT.reset(token)

    def read(self, playlist_id):
        observed = self.reader.read_complete(playlist_id, self.user.spotify_id)
        self.observed[playlist_id] = observed
        return observed

    def resource(self, playlist_id, step):
        return next((r for r in self.resources if r["resource"] == playlist_id and r["step"] == step), None)

    def plan(self, playlist_id, desired, step, ordered=True, mutation_type="track_membership"):
        observed = self.observed.get(playlist_id) or self.read(playlist_id)
        baseline = list(observed.track_uris)
        self.baselines.setdefault((self.step, playlist_id), baseline)
        self.resource_steps.setdefault((step, playlist_id), self.step)
        resource = self.resource(playlist_id, step)
        if resource is None:
            resource = {
                "resource": playlist_id,
                "resource_name": observed.name,
                "step": step,
                "mutation_type": mutation_type,
                "status": "not_required",
                "dispatched": False,
                "affected_count": 0,
                "retryable": False,
                "error_code": None,
                "verification": "ordered" if ordered else "occurrences",
                "baseline_track_uris": baseline,
                "current_track_uris": baseline,
                "loaded_at": observed.loaded_at,
            }
            self.resources.append(resource)
        resource["desired_track_uris"] = list(desired)
        if not self.matches(resource, baseline):
            resource["status"] = "failed"  # planned, but no dispatch yet
        return resource

    @staticmethod
    def matches(resource, actual):
        desired = resource["desired_track_uris"]
        return actual == desired if resource["verification"] == "ordered" else Counter(actual) == Counter(desired)

    def write(self, method, playlist_id, uris, step, **kwargs):
        resource = self.resource(playlist_id, step)
        previous = self.observed.get(playlist_id)
        fresh = self.read(playlist_id)
        baseline = list(fresh.track_uris)
        if previous and previous.track_uris != fresh.track_uris:
            self.error_code = "resource_changed"
            raise RuntimeError("Playlist changed before dispatch")
        if method == "update_playlist_tracks":
            expected = list(uris)
        elif method == "playlist_remove_items":
            removed = set(uris)
            expected = [u for u in baseline if u not in removed]
        else:
            position = kwargs.get("position", len(baseline))
            expected = baseline[:position] + uris + baseline[position:]
        if resource is None:
            resource = self.plan(playlist_id, expected, step)
        if expected == baseline:
            return True
        previously_applied = resource["dispatched"] and resource["status"] != "failed"
        resource.update(dispatched=True, status="unknown", affected_count=None)
        try:
            getattr(self.api, method)(playlist_id, uris, **kwargs)
        except Exception as error:
            cause, completed = error, 0
            if isinstance(error, SpotifyPartialBatchError):
                cause, completed = error.cause, error.completed_batches
            # A refused batch never lands, even after earlier batches did; a lost answer or any other error might.
            rejected = isinstance(cause, _REJECTED)
            if not rejected:
                self.unconfirmed.add(playlist_id)
            known_rejection = not completed and rejected
            resource.update(
                status="failed" if known_rejection and not previously_applied else "unknown",
                affected_count=0 if known_rejection and not previously_applied else None,
                error_code="write_rejected" if known_rejection else "write_unverified",
                retryable=known_rejection and not previously_applied,
            )
            self.error_code = resource["error_code"]
            self.retryable = resource["retryable"]
            raise
        try:
            after = self.read(playlist_id)
        except Exception:
            resource["error_code"] = self.error_code = "verification_failed"
            raise
        actual = list(after.track_uris)
        resource.update(current_track_uris=actual, loaded_at=after.loaded_at)
        # Verify each dispatched write, even during a multi-write rotation.
        if actual != expected:
            from shuffify.services.executors.base_executor import PlaylistVerificationError

            resource["error_code"] = self.error_code = "verification_failed"
            raise PlaylistVerificationError(playlist_id, expected, actual, None, step)
        complete = self.matches(resource, actual)
        before = resource["baseline_track_uris"]
        if resource["mutation_type"] == "track_order":
            affected = sum(a != b for a, b in zip(before, actual)) + abs(len(before) - len(actual))
        else:
            affected = sum((Counter(actual) - Counter(before)).values()) + sum(
                (Counter(before) - Counter(actual)).values()
            )
        resource.update(
            status="complete" if complete else "unknown",
            affected_count=affected if complete else None,
            error_code=None if complete else "effect_incomplete",
            retryable=False,
        )
        # A verified state is authoritative even if a legacy client returns None.
        return True

    def has_effects(self):
        return _has_mutation_effects(self.resources, self.bookkeeping)

    def record_not_started(self, steps):
        """Report that ``steps`` never started, so they changed nothing on the locked target or its inbox."""
        observed = self.observed.get(self.locked_target)
        bookkeeping, resources = not_started_facts(
            steps, self.locked_target, bookkeeping=self.bookkeeping, resources=self.resources,
            name=observed.name if observed else None,
        )
        self.bookkeeping.extend(bookkeeping)
        self.resources.extend(resources)

    def record_failed_step(self, step):
        """Report what ``step``, which started and failed, never attempted: its inbox change and target write.

        Staging and promotion book their own outcome once attempted, and every
        target write is journaled (``plan``/``write``) before it is dispatched,
        so a missing fact means the step changed nothing there.
        """
        kind, target = STEP_BOOKKEEPING.get(step), self.locked_target
        if kind and not any(item["kind"] == kind and item["step"] == step for item in self.bookkeeping):
            self.book(kind, "not_required", resource=target, affected_count=0)
        if step in STEP_TARGET_WRITES and not any(entry["resource"] == target for entry in self.step_resources(step)):
            observed = self.observed.get(target)
            self.resources.append({
                "resource": target, "resource_name": observed.name if observed else None, "step": step,
                "mutation_type": STEP_TARGET_WRITES[step], "status": "failed", "dispatched": False,
                "affected_count": None, "retryable": False, "error_code": self.error_code or "not_attempted",
                "verification": None,
            })

    def book(self, kind, status, *, resource=None, error_code=None, **extra):
        step = "run" if kind in _RUN_BOOKKEEPING else self.step
        item = next((b for b in self.bookkeeping if b["kind"] == kind and b["step"] == step), None)
        if item is None:
            item = {"kind": kind, "step": step}
            self.bookkeeping.append(item)
        item.update(status=status, error_code=error_code, **extra)
        if resource is not None:
            item["resource"] = resource
        return item

    def step_resources(self, step):
        """Include subphases such as archive purge in their executor step."""
        return [
            r for r in self.resources
            if self.resource_steps.get((r["step"], r["resource"]), r["step"]) == step
        ]

    def step_status(self, step, *, default="failed"):
        """Summarize this step's effects without borrowing earlier progress."""
        facts = self.step_resources(step) + [
            b for b in self.bookkeeping if b["step"] == step and b["kind"] not in _RUN_BOOKKEEPING
        ]
        if any(f["status"] == "unknown" for f in facts):
            return "unknown"
        states = [f["status"] for f in facts]
        if "restored" in states and "complete" not in states:
            return "restored"
        if any(s not in ("complete", "not_required") for s in states):
            return "partial" if "complete" in states or "restored" in states else "failed"
        if default == "failed" and "complete" in states:
            return "partial"
        return default

    def refresh_failed_step(self):
        summary = next((s for s in self.steps if s["step"] == self.step), None)
        # A refused step never started, so compensation cannot change its summary.
        if summary is not None and summary["status"] != "not_started":
            summary["status"] = self.step_status(self.step)

    def compensate(self):
        """Restore dispatched resources from only the failing executor step.

        Use that step's earliest baseline, including all its subphases. Earlier
        completed composite steps remain intact. Every restoration is freshly
        authorized and verified inside the runner's target lock. Every playlist
        is restored before any is re-checked: one whose failed write may still
        land is then watched for a while and restored again whenever it
        changes.
        """
        candidates = [r for r in self.step_resources(self.step) if r["dispatched"] and r["status"] != "failed"]
        playlist_ids = list(dict.fromkeys(r["resource"] for r in candidates))
        if not playlist_ids:
            return False
        baselines = {playlist_id: self.baselines[(self.step, playlist_id)] for playlist_id in playlist_ids}
        observed = {
            playlist_id: self._attempt(self._restore, playlist_id, baselines[playlist_id])
            for playlist_id in playlist_ids
        }
        for playlist_id in playlist_ids:
            if playlist_id in self.unconfirmed:
                observed[playlist_id] = self._attempt(
                    self._settle, playlist_id, baselines[playlist_id], observed[playlist_id]
                )
        for resource in candidates:
            after = observed[resource["resource"]]
            if after is None:
                resource.update(
                    status="unknown", error_code="rollback_unverified", retryable=False, affected_count=None
                )
            else:
                resource.update(
                    status="restored",
                    retryable=False,
                    affected_count=None,
                    current_track_uris=list(after.track_uris),
                    restored_track_uris=list(baselines[resource["resource"]]),
                    loaded_at=after.loaded_at,
                )
        return all(after is not None for after in observed.values())

    @staticmethod
    def _attempt(step, playlist_id, *args):
        """Run one rollback step for ``playlist_id``: its verified observation, or None, logged."""
        try:
            return step(playlist_id, *args)
        except Exception:
            logger.warning("Rollback of playlist %s is not verified", playlist_id, exc_info=True)
            return None

    def _restore(self, playlist_id, baseline):
        """Write ``baseline`` back if the playlist differs, and return the verified observation."""
        before = self.read(playlist_id)
        if list(before.track_uris) != baseline:
            self.api.update_playlist_tracks(playlist_id, baseline)
        after = self.read(playlist_id)
        if list(after.track_uris) != baseline:
            raise RuntimeError("Restoration not verified")
        return after

    def _settle(self, playlist_id, baseline, after):
        """Watch a restored playlist while a failed write may still land.

        Spotify can apply a request after the client stopped waiting for it,
        even during the restore, and a restore can lose its own answer too. So
        the playlist is re-read through the whole window, and restored again
        (verified) whenever it is found changed, mid-change (a read raced a
        write) or not yet restored (``after`` is None). Anything else that
        changes it meanwhile is reverted too, such as an edit made in Spotify
        or a write to a playlist this run does not lock.
        """
        checks = current_app.config.get("ROLLBACK_SETTLE_CHECKS", 0)
        seconds = current_app.config.get("ROLLBACK_SETTLE_SECONDS", 0)
        for _ in range(checks):
            time.sleep(seconds)
            try:
                after = self.read(playlist_id)
            except Exception:
                logger.warning("Could not re-check playlist %s after its rollback", playlist_id, exc_info=True)
                after = None  # the read raced a change, or failed
            if after is None or list(after.track_uris) != baseline:
                logger.warning(
                    "Playlist %s is not as restored, presumably because of a delayed write; restoring it again",
                    playlist_id,
                )
                after = self._restore(playlist_id, baseline)
        if after is None:
            raise RuntimeError("Restoration not verified")
        return after

    def outcome(self):
        return operation_result(
            self.kind,
            resources=self.resources,
            bookkeeping=self.bookkeeping,
            error_code=self.error_code,
            retryable=self.retryable,
            steps=self.steps or None,
        )


DIRECT_ENDPOINT_KINDS = {
    "main.shuffle": "shuffle",
    "main.undo": "undo",
    "main.restore_snapshot": "snapshot_restore",
    "main.toggle_visibility": "privacy",
}


class DirectFailure(Exception):
    """A direct action refused before dispatch; the review services raise it too."""

    def __init__(self, status, code, message, retryable=False):
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def direct_rejected_result(
    kind, *, error_code="preflight_failed", http_status=400, retryable=False, unknown=False, message=None
):
    """Direct-action rejection with no invented executor identity or counts."""
    operation = operation_result(kind, error_code=error_code, retryable=retryable)
    if unknown:
        operation.update(aggregate_status="unknown", retryable=False)
    message = message or (
        "The earlier result could not be verified. Do not repeat the action to recover."
        if unknown else "This attempt made no playlist changes."
    )
    return {
        "status": "failed", "http_status": http_status, "message": message,
        "error": message, "operation": operation,
    }


def direct_response_payload(result, kind):
    """JSON body and status for a direct result; only complete reports success."""
    operation = normalized_operation(result, kind)
    complete = operation.get("aggregate_status") == "complete"
    payload = {key: value for key, value in result.items() if key != "http_status"}
    payload.update(
        operation=operation, success=complete,
        category=result.get("category", "success") if complete else "error",
    )
    if not complete:
        payload["error"] = payload["message"]
    return payload, 200 if complete else error_status(result)
