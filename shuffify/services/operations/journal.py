"""Fenced write-ahead plans with conservative recovery and separate compensation."""

import inspect
import math
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from shuffify.spotify.deadline import SpotifyDeadlineError, check_deadline
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyRateLimitError,
    SpotifyReadTransientError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)
from shuffify.spotify.http_client import _quota_exceeded, _retry_after
from shuffify.spotify.observation import observe_mutations

from .planner import batch, content, plan, state_hash
from .recovery import CHANGED, CURRENT, STALE, STALE_READ, observation_verdict, reconcile_resource, walk

READ_DEFERRAL_LIMIT = 8
READ_DEFERRAL_WINDOW_SECONDS = 15 * 60
READ_DEADLINE_LIMIT = 3
# Successful reads release only their own budget, so reads that fail in turn
# could otherwise defer forever. Nothing but a new phase resets this total.
PHASE_DEFERRAL_LIMIT = 24
READ_BUDGET_FIELDS = ("attempts", "first_deferred_at", "deadline_expiries")


class JournalError(RuntimeError):
    """The durable mutation cannot be matched to a frozen plan."""


class NeedsAttention(SpotifyWriteUnverifiedError):
    def __init__(self, reason, result=None):
        self.reason, self.result = reason, deepcopy(result)
        super().__init__(f"The operation needs review: {reason}")


class OperationDeferred(SpotifyRateLimitError):
    """A known rejection must wait durably, without occupying an HTTP retry loop."""

    def __init__(self, next_attempt_at):
        self.next_attempt_at = next_attempt_at
        retry_after = max(1, math.ceil((next_attempt_at - datetime.now(timezone.utc)).total_seconds()))
        super().__init__("Spotify asked this operation to retry later", retry_after=retry_after)


class OperationAborted(SpotifyWriteUnverifiedError):
    """A durable service failure forbids any further forward execution."""

    def __init__(self, reason, result=None):
        self.reason, self.result = reason, deepcopy(result)
        super().__init__(f"The operation was stopped: {reason}")


class ReadRetryExhausted(OperationAborted):
    """A persisted safe-read budget cannot authorize another deferral."""


class DurableJournal:
    """Persist through injected fenced checkpoints; never hold a transaction over I/O."""

    def __init__(self, get_journal, checkpoint, read_resource, dispatch, settle_resource=None, read_metadata=None,
                 read_snapshot=None):
        self.get_journal = get_journal
        self.checkpoint = checkpoint
        self.read_resource = read_resource
        self.dispatch = dispatch
        self.settle_resource = settle_resource
        self.read_metadata = read_metadata
        self.read_snapshot = read_snapshot
        self.active_batch = None
        self.cursor = 0
        self._observation_depth = 0
        self.doc = self._document(get_journal())
        self._persisted_boundary_revision = self.doc.get("boundaries", {}).get("revision", 0)

    @staticmethod
    def _document(document):
        document = deepcopy(document or {})
        if document.get("version", 1) != 1:
            raise JournalError("Unsupported journal version")
        for key, value in {
            "version": 1, "phase": "forward", "status": "ready", "resources": {}, "plans": [],
            "compensation": [], "choices": {}, "recovery": {"available": False, "action": None, "reason": None},
        }.items():
            document.setdefault(key, deepcopy(value))
        return document

    def reload_latest(self):
        """Adopt an atomic local publication without replacing the active observer."""
        if self.active_batch is not None:
            raise JournalError("A journal cannot reload during an HTTP attempt")
        document = self._document(self.get_journal())
        if self.cursor > len(document["plans"]):
            raise JournalError("Reloaded journal lost an invocation plan")
        self.doc = document
        self._persisted_boundary_revision = document.get("boundaries", {}).get("revision", 0)
        return self

    def accepted_step(self, name):
        return next((deepcopy(item) for item in self.doc.get("boundaries", {}).get("accepted", [])
                     if item["name"] == name), None)

    def begin_step(self, name):
        """Persist a compensation boundary before this domain step's first effect."""
        if not isinstance(name, str) or not name or len(name) > 128:
            raise JournalError("A domain step requires a name of 1 to 128 characters")
        accepted = self.accepted_step(name)
        if accepted is not None:
            return accepted
        self.check_pending_control()
        if self.doc["phase"] != "forward" or self.doc["status"] == "needs_attention":
            raise JournalError("A blocked operation cannot begin another domain step")
        if "boundaries" not in self.doc and self.doc["plans"]:
            raise JournalError("Domain steps must begin before the first mutation plan")
        boundaries = self.doc.setdefault("boundaries", {"revision": 0, "accepted": [], "current": None})
        current = boundaries["current"]
        if current is not None:
            if current["name"] != name:
                raise JournalError("The preceding domain step has not been accepted")
            return deepcopy(current)
        if any(item["state"] != "acknowledged" for step in self.doc["plans"] for item in step["batches"]):
            raise JournalError("A domain step cannot skip unfinished mutation plans")
        latest = self.get_journal() or {}
        current = {"name": name, "state": "active", "start": len(self.doc["plans"]), "baselines": {},
                   "local_before": sorted(latest.get("runtime", {}).get("local_steps", {}))}
        boundaries["current"] = current
        boundaries["revision"] += 1
        self._save()
        return deepcopy(current)

    @staticmethod
    def _step_proof(document, current):
        return state_hash({"name": current["name"], "start": current["start"], "end": len(document["plans"]),
                           "plans": document["plans"], "resources": document["resources"],
                           "baselines": current["baselines"],
                           "local_before": current["local_before"]})

    def verify_step(self, name, *, on_verified=None):
        """Freshly reconcile the complete ledger without dispatching forward work."""
        self.check_pending_control()
        current = self.doc.get("boundaries", {}).get("current")
        if current is None or current["name"] != name or self.doc["phase"] != "forward":
            raise JournalError("The domain step is not active")
        if any(item["state"] != "acknowledged" for step in self.doc["plans"][current["start"]:]
               for item in step["batches"]):
            raise JournalError("Unfinished mutation plans cannot be accepted")
        if self._reconcile(on_verified=on_verified):
            raise NeedsAttention(self.doc["recovery"]["reason"])
        proof = self._step_proof(self.doc, current)
        current["verified"] = {"end": len(self.doc["plans"]), "proof": proof}
        self.doc["boundaries"]["revision"] += 1
        self._save()
        return proof

    @staticmethod
    def accept_step_document(document, name, *, proof, result=None):
        """Pure final publication for the fenced transaction containing mandatory DB effects."""
        document = DurableJournal._document(document)
        boundaries = document.get("boundaries", {})
        current = boundaries.get("current")
        if (current is None or current["name"] != name or document["phase"] != "forward"
                or document["status"] in {"needs_attention", "failed"} or document.get("abort")):
            raise JournalError("The domain step cannot be accepted")
        verified = current.get("verified", {})
        if (verified.get("proof") != proof or verified.get("end") != len(document["plans"])
                or DurableJournal._step_proof(document, current) != proof
                or any(item["state"] != "acknowledged" for step in document["plans"][current["start"]:]
                       for item in step["batches"])):
            raise JournalError("The domain step's verification is stale")
        receipts = document.get("runtime", {}).get("local_steps", {})
        accepted = {**current, "state": "accepted", "end": len(document["plans"]), "result": deepcopy(result),
                    "local_steps": sorted(set(receipts) - set(current["local_before"]))}
        accepted.pop("verified", None)
        boundaries["accepted"].append(accepted)
        boundaries["current"] = None
        boundaries["revision"] += 1
        return document

    def compensation_scope(self, document=None):
        """Describe only the failed unaccepted step; callers own any local compensation policy."""
        document = self.doc if document is None else document
        if document.get("phase") == "compensating" and document.get("compensation_scope") is not None:
            return deepcopy(document["compensation_scope"])
        boundaries = document.get("boundaries")
        current = boundaries.get("current") if boundaries else None
        start = current["start"] if current else len(document.get("plans", [])) if boundaries else 0
        receipts = document.get("runtime", {}).get("local_steps", {})
        local_steps = sorted(set(receipts) - set(current["local_before"])) if current else []
        return {"step": current["name"] if current else None, "start": start, "end": len(document.get("plans", [])),
                "retained_steps": [item["name"] for item in boundaries["accepted"]] if boundaries else [],
                "local_steps": local_steps}

    def compensation_has_effects(self, document=None):
        document = self.doc if document is None else document
        if document.get("phase") == "compensating":
            return bool(document.get("compensation"))
        scope = self.compensation_scope(document)
        return any(item.get("state") in {"dispatched", "acknowledged"}
                   for step in document.get("plans", [])[scope["start"]:scope["end"]] for item in step["batches"])

    @contextmanager
    def observe(self):
        if self._observation_depth == 0:
            self.cursor = 0
        self._observation_depth += 1
        try:
            with observe_mutations(self):
                yield self
        finally:
            self._observation_depth -= 1

    def skip_to(self, position):
        """Advance past plans covered by an already-checkpointed command step."""
        if type(position) is not int or not self.cursor <= position <= len(self.doc["plans"]):
            raise JournalError("Invalid command step plan range")
        if any(item["state"] != "acknowledged" for step in self.doc["plans"][self.cursor:position]
               for item in step["batches"]):
            raise JournalError("A command step cannot skip unfinished mutations")
        self.cursor = position

    def _plans(self):
        return self.doc["compensation"] if self.doc["phase"] == "compensating" else self.doc["plans"]

    def check_pending_control(self):
        self._check_publication(self.get_journal() or {})
        self._defer_if_needed()
        if self.doc.get("abort") and self.doc["phase"] == "forward":
            raise OperationAborted(**self.doc["abort"])
        if self.doc["status"] == "failed":
            raise OperationAborted("operation_failed")

    def request_abort(self, reason, result=None):
        self.doc.setdefault("abort", {"reason": reason, "result": deepcopy(result)})
        self._save()
        raise OperationAborted(**self.doc["abort"])

    def _defer_if_needed(self):
        now = datetime.now(timezone.utc)
        deadlines = []
        read_retry = self.doc.get("read_retry", {})
        first = read_retry.get("first_deferred_at")
        if first and not read_retry.get("exhausted"):
            started = datetime.fromisoformat(first)
            if started.tzinfo is None:
                raise JournalError("A retry start must include its timezone")
            if (now - started).total_seconds() >= READ_DEFERRAL_WINDOW_SECONDS:
                self._exhaust_read_retry()
        retry = read_retry.get("next_attempt_at")
        if retry:
            due = datetime.fromisoformat(retry)
            if due.tzinfo is None:
                raise JournalError("A retry deadline must include its timezone")
            if due > now:
                deadlines.append(due)
        deadlines += self._rejected_batch_deadlines(
            () if self.doc.get("compensation_retry_started") else self._plans(), now,
        )
        if deadlines:
            raise OperationDeferred(max(deadlines))

    @staticmethod
    def _rejected_batch_deadlines(steps, now):
        deadlines = []
        for step in steps:
            for item in step["batches"]:
                if item["state"] == "rejected" and item.get("next_attempt_at"):
                    due = datetime.fromisoformat(item["next_attempt_at"])
                    if due.tzinfo is None:
                        raise JournalError("A retry deadline must include its timezone")
                    if due > now:
                        deadlines.append(due)
        return deadlines

    def _progress(self):
        batches = [item for step in self._plans() for item in step["batches"]]
        self.doc["progress"] = {
            "batches_done": sum(item["state"] == "acknowledged" for item in batches),
            "total_batches": len(batches), "phase": self.doc["phase"],
        }
        for step in self._plans():
            step["batches_done"] = sum(item["state"] == "acknowledged" for item in step["batches"])

    def _save(self):
        self._progress()
        owned = {"version", "phase", "status", "resources", "plans", "compensation", "compensation_resources",
                 "choices", "recovery", "observations", "progress", "abort", "boundaries",
                 "compensation_scope", "compensation_retry_started", "read_retry", "read_deferrals"}
        latest = self.get_journal() or {}
        self._check_publication(latest)
        for key, value in latest.items():
            if key not in owned:
                self.doc[key] = deepcopy(value)
        # Only pass detached copies: a failed checkpoint cannot mutate the persisted document.
        self.checkpoint(deepcopy(self.doc))
        self._persisted_boundary_revision = self.doc.get("boundaries", {}).get("revision", 0)

    def _check_publication(self, latest):
        # Compare the loaded revision, never a proposed revision that local edits can increment.
        if latest.get("boundaries", {}).get("revision", 0) != self._persisted_boundary_revision:
            raise JournalError("An atomic domain publication requires reloading the journal")

    @staticmethod
    def _observed(raw):
        observed = {**deepcopy(raw), **content(raw)}
        if observed.get("scope") != "metadata" and (
                not isinstance(observed.get("snapshot_id"), str) or not observed["snapshot_id"]):
            raise JournalError("A complete observation requires a playlist snapshot")
        return observed

    def has_ambiguous_write(self, document=None):
        """A dispatched batch without an acknowledgement still requires complete reconciliation."""
        document = self.doc if document is None else document
        return any(item.get("state") == "dispatched"
                   for step in document.get("plans", []) + document.get("compensation", [])
                   for item in step.get("batches", []))

    def _retry_phase(self):
        # Compensation, including the reconciliation that plans it, owns a
        # budget separate from the forward direction's.
        if self.doc["phase"] == "compensating" or self.doc.get("compensation_retry_started"):
            return "compensation"
        return "forward"

    def _read_key(self, kind, *parts):
        return ":".join([self._retry_phase(), kind, *map(str, parts)])

    def defer_read(self, error, key=None):
        """Persist a safe read's retry deadline without changing the mutation direction.

        Each failing read keeps its own budget, keyed by phase and resource or
        batch, until that same read succeeds. A failure raised outside a keyed
        read shares the phase's operation key.
        """
        from .control import read_retry_delay

        if isinstance(error, OperationDeferred):
            raise error
        delay = read_retry_delay(error)
        if delay is None:
            raise error
        now = datetime.now(timezone.utc)
        key = key or self._read_key("operation")
        previous = self.doc.get("read_retry", {})
        parked = deepcopy(previous.get("parked", {}))
        held = {name: previous[name] for name in READ_BUDGET_FIELDS if name in previous}
        # A budget persisted before reads were keyed belongs to the next failing read.
        holder = previous.get("key", key)
        if held and holder != key:
            parked[holder] = held
            held = {}
        budget = held or parked.pop(key, {})
        first = datetime.fromisoformat(budget["first_deferred_at"]) if budget.get("first_deferred_at") else now
        if first.tzinfo is None:
            raise JournalError("A retry start must include its timezone")
        attempts, deadlines = budget.get("attempts", 0), budget.get("deadline_expiries", 0)
        if type(attempts) is not int or attempts < 0 or type(deadlines) is not int or deadlines < 0:
            raise JournalError("A retry counter must be a nonnegative integer")
        totals = self.doc.get("read_deferrals", {})
        deferrals = totals.get("count", 0) if totals.get("phase") == self._retry_phase() else 0
        if type(deferrals) is not int or deferrals < 0:
            raise JournalError("A retry counter must be a nonnegative integer")
        current, seen = error, set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, SpotifyDeadlineError):
                deadlines += 1
                break
            current = current.__cause__ or current.__context__ or getattr(current, "cause", None)
        cutoff = first + timedelta(seconds=READ_DEFERRAL_WINDOW_SECONDS)
        # Clamp before constructing a timedelta: even an enormous Retry-After
        # must not overflow or reserve this target beyond its durable window.
        due = now + timedelta(seconds=min(delay, max(0, (cutoff - now).total_seconds())))
        if previous.get("next_attempt_at"):
            due = min(cutoff, max(due, datetime.fromisoformat(previous["next_attempt_at"])))
        self.doc["read_retry"] = {"key": key, "next_attempt_at": due.isoformat(), "phase": self.doc["phase"],
                                  "attempts": attempts + 1, "first_deferred_at": first.isoformat(),
                                  "deadline_expiries": deadlines,
                                  "error_code": "read_rate_limited" if isinstance(error, SpotifyRateLimitError)
                                  else "read_temporarily_unavailable"}
        if parked:
            self.doc["read_retry"]["parked"] = parked
        self.doc["read_deferrals"] = {"phase": self._retry_phase(), "count": deferrals + 1}
        if (attempts + 1 >= READ_DEFERRAL_LIMIT or deadlines >= READ_DEADLINE_LIMIT or now >= cutoff
                or deferrals + 1 >= PHASE_DEFERRAL_LIMIT):
            self._exhaust_read_retry()
        self._save()
        raise OperationDeferred(due) from error

    def _release_read_budget(self, key):
        """Release the budget of the read that just succeeded, and nothing else."""
        retry = self.doc.get("read_retry")
        if not retry:
            return
        # Failures raised outside a keyed read, and budgets persisted before
        # reads were keyed, cannot be matched to one read: any successful read
        # releases them. The phase total still bounds their deferrals.
        released = {key, self._read_key("operation")}
        parked = {name: budget for name, budget in retry.get("parked", {}).items() if name not in released}
        if retry.get("key", key) in released:
            remaining = {"parked": parked} if parked else None
        else:
            remaining = {name: value for name, value in retry.items() if name != "parked"}
            if parked:
                remaining["parked"] = parked
        if remaining == retry:
            return
        if remaining is None:
            self.doc.pop("read_retry")
        else:
            self.doc["read_retry"] = remaining
        self._save()

    def _exhaust_read_retry(self):
        retry = self.doc["read_retry"]
        retry["exhausted"] = True
        retry.pop("next_attempt_at", None)
        result = {"success": False, "error_code": "read_retry_exhausted", "category": "error",
                  "message": "Spotify remained unavailable. The operation stopped; review its result before retrying.",
                  "retryable": False}
        if self.doc["phase"] == "forward":
            self.doc.setdefault("abort", {"reason": "read_retry_exhausted", "result": deepcopy(result)})
        self._save()
        raise ReadRetryExhausted("read_retry_exhausted", result)

    def _read_call(self, read, resource):
        self._defer_if_needed()
        try:
            check_deadline()
            result = read(resource)
        except Exception as error:
            self.defer_read(error, self._read_key("resource", resource))
        self._release_read_budget(self._read_key("resource", resource))
        return result

    def _settle_lag(self, resource, reason):
        """Defer, within the read budget, a read that lags an acknowledged write."""
        key = self._read_key("stale", resource)
        if reason == STALE_READ:
            self.defer_read(SpotifyReadTransientError("The playlist read lags the last acknowledged write"), key)
        self._release_read_budget(key)

    def _verdict(self, resource, observed, expected, snapshot=None, *, chain_snapshot=True):
        """Judge an observation against `expected` using the resource's own known snapshot chain.

        A stale read defers within the read budget. `snapshot` overrides the chain's expected
        snapshot for callers that carry their own frozen expectation.
        """
        base, batches = self._resource_ledger(resource)
        _, chain, earlier, _ = walk(base, batches)
        if not chain_snapshot and chain:
            # The caller's snapshot may predate an acknowledged write; the latest one is as good.
            earlier = earlier + [(expected, chain)]
        verdict = observation_verdict(observed, expected, chain if chain_snapshot else snapshot, earlier)
        self._settle_lag(resource, STALE_READ if verdict == STALE else None)
        return verdict

    def observation_is_current(self, resource, observed, expected, snapshot):
        """True when `observed` is the expected state of a resource this run wrote; defers a stale read."""
        if resource not in self.doc["resources"]:
            return content(observed) == content(expected) and (
                not snapshot or observed.get("snapshot_id") == snapshot)
        return self._verdict(resource, observed, content(expected), snapshot, chain_snapshot=False) == CURRENT

    def _read(self, resource, *, scope=None):
        scope = scope or self.doc["resources"].get(resource, {}).get("scope", "tracks")
        if scope == "metadata":
            if self.read_metadata is None:
                raise JournalError("No authorized metadata reader is configured")
            return self._observed({**self._read_call(self.read_metadata, resource), "scope": "metadata"})
        return self._observed(self._read_call(self.read_resource, resource))

    def _before_batch(self, step, item):
        resource = step["resource"]
        snapshot = self._expected_snapshot(resource)
        if (self.read_snapshot is not None and self.doc["resources"][resource].get("scope", "tracks") == "tracks"
                and item["path"].endswith("/items") and item["state"] in {"prepared", "rejected"}
                and snapshot and not any(batch["state"] == "dispatched" for batch in self._resource_batches(resource))):
            observed = self._read_call(self.read_snapshot, resource)
            # Production readers return both fields from one authorized GET.
            # A narrow injected snapshot reader can provide only the revision.
            observed_snapshot = observed.get("snapshot_id") if isinstance(observed, dict) else observed
            metadata_matches = not isinstance(observed, dict) or observed.get("metadata") == item["before"]["metadata"]
            if observed_snapshot == snapshot and metadata_matches:
                return {**deepcopy(item["before"]), "snapshot_id": observed_snapshot}
        return self._read(resource)

    def _base(self, resource, *, scope="tracks", expected_baseline=None):
        self.check_pending_control()
        if self.doc["status"] == "needs_attention" or self.doc["phase"] != "forward":
            raise NeedsAttention(self.doc["recovery"].get("reason") or "operation_blocked")
        if any(item["state"] == "dispatched" for step in self.doc["plans"] for item in step["batches"]):
            raise NeedsAttention("unfinished_dispatch")
        boundaries = self.doc.get("boundaries")
        if boundaries is not None and boundaries["current"] is None:
            raise JournalError("A new mutation requires an active domain step")
        stored = self.doc["resources"].get(resource)
        if stored and stored.get("scope") == "metadata" and scope != "metadata":
            raise NeedsAttention("metadata_scope_cannot_change_tracks")
        observed = None
        if resource not in self.doc["resources"]:
            observed = self._read(resource, scope=scope)
            if expected_baseline is not None and (content(observed) != content(expected_baseline)
                    or scope != "metadata" and expected_baseline.get("snapshot_id") != observed.get("snapshot_id")):
                self._attention("prepared_baseline_changed", {resource: observed})
                raise NeedsAttention("prepared_baseline_changed")
            self.doc["resources"][resource] = {
                "baseline": content(observed), "snapshot_id": observed.get("snapshot_id"), "scope": scope,
            }
        previous = [step for step in self.doc["plans"] if step["resource"] == resource]
        baseline = deepcopy(previous[-1]["desired"] if previous else self.doc["resources"][resource]["baseline"])
        unfinished = any(item["state"] != "acknowledged" for step in previous for item in step["batches"])
        if expected_baseline is not None:
            if content(expected_baseline) != baseline:
                self._attention("prepared_baseline_changed")
                raise NeedsAttention("prepared_baseline_changed")
            if not unfinished:
                observed = observed or self._read(resource, scope=scope)
                if not self.observation_is_current(resource, observed, baseline, expected_baseline.get("snapshot_id")):
                    self._attention("prepared_baseline_changed", {resource: observed})
                    raise NeedsAttention("prepared_baseline_changed")
        if boundaries is not None and resource not in boundaries["current"]["baselines"]:
            snapshot = observed.get("snapshot_id") if observed is not None else self._expected_snapshot(resource)
            if observed is not None and expected_baseline is not None and scope != "metadata":
                # The observation was proved current against the expectation; a lagging
                # read must not replace the acknowledged snapshot as the boundary's anchor.
                snapshot = expected_baseline.get("snapshot_id") or snapshot
            boundaries["current"]["baselines"][resource] = {
                "baseline": deepcopy(baseline), "snapshot_id": snapshot, "scope": scope,
            }
            boundaries["revision"] += 1
        return baseline

    def observe_resource(self, resource, *, expected_baseline, scope="tracks"):
        """Fence a complete read-only input without creating a mutation or rollback target."""
        if (scope not in ("tracks", "metadata")
                or (expected_baseline.get("scope") == "metadata") != (scope == "metadata")):
            raise JournalError("The observation scope must match its frozen baseline")
        self.check_pending_control()
        if self.doc['phase'] != 'forward' or self.doc['status'] == 'needs_attention':
            raise NeedsAttention('operation_blocked')
        current = self.doc.get('boundaries', {}).get('current')
        stored = self.doc['resources'].get(resource)
        if stored is None or current is not None and resource not in current['baselines']:
            self._base(resource, scope=scope, expected_baseline=expected_baseline)
            self._save()
            return deepcopy(expected_baseline)
        anchor = current['baselines'][resource] if current is not None else stored
        if (content(expected_baseline) != anchor['baseline']
                or scope != "metadata" and expected_baseline.get('snapshot_id') != anchor.get('snapshot_id')):
            self._attention('prepared_baseline_changed')
            raise NeedsAttention('prepared_baseline_changed')
        observed = self._read(resource)
        baseline, batches = self._resource_ledger(resource)
        _, reason = reconcile_resource(baseline, batches, observed)
        self._settle_lag(resource, reason)
        if reason not in {None, 'replay_settled'}:
            self._attention(reason, {resource: observed})
            raise NeedsAttention(reason)
        return observed

    def _freeze(self, kind, resource, *, expected_baseline=None, **options):
        scope = "metadata" if kind == "details" and self.read_metadata and resource not in self.doc["resources"] else (
            self.doc["resources"].get(resource, {}).get("scope", "tracks"))
        baseline = self._base(resource, scope=scope if kind == "details" else "tracks",
                              expected_baseline=expected_baseline)
        desired, batches = plan(kind, resource, baseline, **options)
        plan_id = len(self.doc["plans"])
        self.doc["plans"].append({
            "id": plan_id, "kind": kind, "resource": resource, "baseline": baseline,
            "desired": desired, "batches": batches, "batches_done": 0,
            "intent": self._intent(kind, {"resource": resource, "track_uris": options.get("uris", []),
                                          "position": options.get("position"), "details": options.get("details", {})}),
        })
        self.doc["status"] = "ready"
        self._save()
        return plan_id

    def freeze_replace(self, resource, track_uris, *, expected_baseline=None):
        return self._freeze("replace", resource, uris=deepcopy(list(track_uris)), expected_baseline=expected_baseline)

    def freeze_add(self, resource, track_uris, position=None, *, expected_baseline=None):
        return self._freeze("add", resource, uris=deepcopy(list(track_uris)), position=position,
                            expected_baseline=expected_baseline)

    def freeze_remove(self, resource, track_uris, *, expected_baseline=None):
        return self._freeze("remove", resource, uris=deepcopy(list(track_uris)), expected_baseline=expected_baseline)

    def freeze_details(self, resource, *, expected_baseline=None, **details):
        return self._freeze("details", resource, details=details, expected_baseline=expected_baseline)

    def ensure_replace(self, resource, track_uris, *, expected_baseline):
        return self._ensure("replace", resource, expected_baseline, track_uris=track_uris)

    def ensure_details(self, resource, *, expected_baseline, **details):
        return self._ensure("details", resource, expected_baseline, details=details)

    def ensure_add(self, resource, track_uris, position=None, *, expected_baseline):
        return self._ensure("add", resource, expected_baseline, track_uris=track_uris, position=position)

    def ensure_remove(self, resource, track_uris, *, expected_baseline):
        return self._ensure("remove", resource, expected_baseline, track_uris=track_uris)

    def _ensure(self, kind, resource, expected_baseline, **values):
        self.check_pending_control()
        if self.cursor < len(self.doc["plans"]):
            stored = self.doc["plans"][self.cursor]
            intent = self._intent(kind, {"resource": resource, **values})
            if stored.get("intent") != intent or stored["baseline"] != content(expected_baseline):
                self._attention("prepared_intent_changed")
                raise NeedsAttention("prepared_intent_changed")
            return self.cursor
        options = {"uris": values["track_uris"]} if kind != "details" else values
        if kind == "add":
            options["position"] = values["position"]
        return self._freeze(kind, resource, expected_baseline=expected_baseline, **options)

    def ensure_create(self, name, public=False, description=""):
        self.check_pending_control()
        intent = self._intent("create", {"name": name, "public": public, "description": description})
        if self.cursor < len(self.doc["plans"]):
            if self.doc["plans"][self.cursor].get("intent") != intent:
                self._attention("prepared_intent_changed")
                raise NeedsAttention("prepared_intent_changed")
            return self.cursor
        return self.freeze_create(name, public=public, description=description)

    def freeze_create(self, name, public=False, description=""):
        self.check_pending_control()
        if self.doc["status"] == "needs_attention" or self.doc["phase"] != "forward":
            raise NeedsAttention("operation_blocked")
        if any(item["state"] == "dispatched" for step in self.doc["plans"] for item in step["batches"]):
            raise NeedsAttention("unfinished_dispatch")
        if self.doc.get("boundaries") is not None and self.doc["boundaries"]["current"] is None:
            raise JournalError("A new creation requires an active domain step")
        plan_id = len(self.doc["plans"])
        desired = {"track_uris": [], "metadata": {"name": name, "public": public, "description": description}}
        self.doc["plans"].append({
            "id": plan_id, "kind": "create", "resource": None, "baseline": None, "desired": desired,
            "batches": [batch("POST", "/me/playlists", desired["metadata"], {}, desired)], "batches_done": 0,
            "intent": self._intent("create", {"name": name, "public": public, "description": description}),
        })
        self.doc["status"] = "ready"
        self._save()
        return plan_id

    def _attention(self, reason, observations=None):
        self.doc["status"] = "needs_attention"
        available = bool(self.restore_plan()["resources"])
        self.doc["recovery"] = {"available": available, "action": "restore" if available else None, "reason": reason}
        if observations:
            self.doc["observations"] = deepcopy(observations)
        self._save()
        return self.outcome()

    def outcome(self):
        self._progress()
        return {"state": self.doc["status"], "progress": deepcopy(self.doc["progress"]),
                "recovery": deepcopy(self.doc["recovery"])}

    def _resource_batches(self, resource):
        return [item for step in self._plans() if step["resource"] == resource for item in step["batches"]]

    def _resource_ledger(self, resource):
        if self.doc["phase"] == "forward":
            return self.doc["resources"][resource], self._resource_batches(resource)
        if resource in self.doc["compensation_resources"]:
            return self.doc["compensation_resources"][resource], self._resource_batches(resource)
        # Accepted resources still guard recovery, even though no compensation may touch them.
        stored = self.doc["resources"][resource]
        expected, snapshot = stored["baseline"], stored.get("snapshot_id")
        for step in self.doc["plans"]:
            if step["resource"] != resource:
                continue
            for item in step["batches"]:
                if item["state"] != "acknowledged":
                    raise JournalError("An unfinished resource was omitted from compensation")
                expected, snapshot = item["after"], item.get("snapshot_id")
        return {"baseline": expected, "snapshot_id": snapshot}, []

    def _reconcile(self, *, on_verified=None):
        observations, updates = {}, []
        for step in self._plans():
            if step["kind"] == "create" and step["batches"][0]["state"] == "dispatched":
                return self._attention("unidentified_creation")
        # Read every resource before acknowledging or dispatching any resumed work.
        for resource, stored in sorted(self.doc["resources"].items()):
            base, batches = self._resource_ledger(resource)
            ambiguous = any(item["state"] == "dispatched" for item in batches)
            if ambiguous and self.settle_resource and stored.get("scope", "tracks") == "tracks":
                observed = self._observed(self._read_call(self.settle_resource, resource))
            else:
                observed = self._read(resource)
            observations[resource] = observed
            acknowledgements, reason = reconcile_resource(base, batches, observed)
            self._settle_lag(resource, reason)
            if reason and reason != "replay_settled":
                return self._attention(reason, observations)
            updates.append((resource, acknowledgements, reason))
        for resource, acknowledgements, reason in updates:
            for item in acknowledgements:
                item.update(state="acknowledged", snapshot_id=observations[resource].get("snapshot_id"), inferred=True)
            if reason == "replay_settled":
                _, batches = self._resource_ledger(resource)
                pending = next(item for item in batches if item["state"] != "acknowledged")
                pending["state"] = "prepared"
            _, batches = self._resource_ledger(resource)
            acknowledged = [item for item in batches if item["state"] == "acknowledged"]
            if acknowledged and acknowledged[-1].get("snapshot_id") is None:
                acknowledged[-1]["snapshot_id"] = observations[resource].get("snapshot_id")
        # Local publication can fail after every resource was freshly proved.
        # Let the runtime retain that boundary before the first checkpoint.
        if on_verified is not None:
            on_verified()
        if updates:
            self._save()
        return None

    def _expected_snapshot(self, resource):
        if self.doc["resources"][resource].get("scope") == "metadata":
            return None
        resources = self.doc["resources"] if self.doc["phase"] == "forward" else self.doc["compensation_resources"]
        base = resources[resource]
        snapshot = base["snapshot_id"]
        for item in self._resource_batches(resource):
            if item["state"] != "acknowledged":
                break
            snapshot = item.get("snapshot_id")
        return snapshot

    def _ack(self, step, item, response):
        if item["state"] == "acknowledged":
            return
        if step["kind"] == "create":
            if not isinstance(response, dict) or not isinstance(response.get("id"), str) or not response["id"]:
                raise SpotifyWriteUnverifiedError("Playlist creation has no confirmed identity")
            step["result"] = deepcopy(response)
            item["created_id"] = response["id"]
        elif item["path"].endswith("/items"):
            snapshot = response.get("snapshot_id") if isinstance(response, dict) else None
            if not isinstance(snapshot, str) or not snapshot:
                raise SpotifyWriteUnverifiedError("The playlist write has no confirmed snapshot")
        snapshot = response.get("snapshot_id") if isinstance(response, dict) else None
        item.update(state="acknowledged", snapshot_id=snapshot)
        self._save()

    def resume(self, *, on_verified=None):
        if self.doc["status"] in {"needs_attention", "failed"}:
            return self.outcome()
        self.check_pending_control()
        if any(item.get("terminal_rejection") for step in self._plans() for item in step["batches"]):
            if self.doc["phase"] == "compensating":
                return self._attention("compensation_rejected")
            raise OperationAborted("quota_exceeded", {
                "success": False, "error_code": "write_rejected", "category": "error",
                "message": "Spotify rejected the write because its quota is exhausted.", "retryable": False,
            })
        conflict = self._reconcile(on_verified=on_verified)
        if conflict:
            return conflict
        self.doc["status"] = "running"
        for step in self._plans():
            for position, item in enumerate(step["batches"]):
                if item["state"] == "acknowledged":
                    continue
                batch_key = self._read_key("batch", step["id"], position)
                resource = step["resource"]
                if resource is not None:
                    observed = self._before_batch(step, item)
                    if self._verdict(resource, observed, item["before"]) != CURRENT:
                        return self._attention("external_edit", {resource: observed})
                    item["snapshot_before"] = observed.get("snapshot_id")
                try:
                    check_deadline()
                except SpotifyDeadlineError as error:
                    self.defer_read(error, batch_key)
                prior_state, prior_attempts = item["state"], item["attempts"]
                item.update(state="dispatched", dispatched_at=datetime.now(timezone.utc).isoformat())
                self._save()
                self.active_batch = (step, item)
                try:
                    with self.observe():
                        response = self.dispatch(item["method"], item["path"], deepcopy(item["body"]))
                    self._ack(step, item, response)
                except SpotifyDeadlineError as error:
                    if item["attempts"] == prior_attempts:
                        item["state"] = prior_state
                        self._save()
                        self.defer_read(error, batch_key)
                    raise
                except (SpotifyAuthError, SpotifyForbiddenError, SpotifyNotFoundError,
                        SpotifyRateLimitError, SpotifyWriteRejectedError):
                    if item["state"] != "acknowledged":
                        item["state"] = "rejected"
                        self._save()
                    raise
                finally:
                    self.active_batch = None
                self._release_read_budget(batch_key)
        conflict = self._reconcile(on_verified=on_verified)
        if conflict:
            return conflict
        self.doc["status"] = "rolled_back" if self.doc["phase"] == "compensating" else "succeeded"
        self.doc["recovery"] = {"available": False, "action": None, "reason": None}
        self._save()
        return self.outcome()

    def verify_terminal(self, *, on_verified=None):
        """Reconcile terminal candidates without forward execution or compensation."""
        self._check_publication(self.get_journal() or {})
        self._defer_if_needed()
        if self.doc['status'] == 'needs_attention':
            return self.outcome()
        conflict = self._reconcile(on_verified=on_verified)
        return conflict or self.outcome()

    def compensate(self):
        if self.doc["phase"] == "compensating":
            self._defer_if_needed()
            return self.resume()
        if self.doc["status"] == "needs_attention":
            return self.outcome()
        # A rejected forward batch's retry hint fences rollback I/O too: wait
        # for it before any compensation read or write.
        pending = self._rejected_batch_deadlines(self.doc["plans"], datetime.now(timezone.utc))
        if pending:
            raise OperationDeferred(max(pending))
        # Compensation starts its own bounded outage window. Persist this
        # transition before reconciling so a deferred reconciliation keeps its
        # compensation budget across worker replacement.
        if not self.doc.get("compensation_retry_started"):
            self.doc.pop("read_retry", None)
            self.doc.pop("read_deferrals", None)
            self.doc["compensation_retry_started"] = True
            self._save()
        self._defer_if_needed()
        conflict = self._reconcile()
        if conflict:
            return conflict
        scope = self.compensation_scope()
        boundaries = self.doc.get("boundaries")
        if boundaries is not None and not self.compensation_has_effects():
            self.doc.update(status="failed", compensation_scope=scope)
            self.doc.pop("compensation_retry_started", None)
            self._save()
            return self.outcome()
        selected = self.doc["plans"][scope["start"]:scope["end"]]
        targets = {step["resource"] for step in selected if step["resource"] is not None}
        originals = boundaries["current"]["baselines"] if boundaries is not None else self.doc["resources"]
        if any(step["kind"] == "create" for step in selected):
            return self._attention("created_playlist_requires_review")
        resources, plans = {}, []
        for resource in sorted(targets):
            original = originals[resource]
            observed = self._read(resource)
            # The second observation protects compensation from an edit after reconciliation.
            base, batches = self._resource_ledger(resource)
            _, reason = reconcile_resource(base, batches, observed)
            self._settle_lag(resource, reason)
            if reason:
                return self._attention(reason, {resource: observed})
            baseline = content(observed)
            forward, forward_snapshot, earlier, _ = walk(base, batches)
            # The forward chain's own snapshot is the truth; a lagging read must not replace it.
            snapshot = forward_snapshot if baseline == forward and forward_snapshot else observed.get("snapshot_id")
            chain = [[state, known] for state, known in earlier]
            if forward_snapshot:
                chain.append([forward, forward_snapshot])
            for name, value in baseline["metadata"].items():
                prior = original["baseline"]["metadata"].get(name)
                if value != prior and prior is None:
                    return self._attention("baseline_metadata_unavailable", {resource: observed})
            resources[resource] = {"baseline": baseline, "snapshot_id": snapshot, "earlier": chain}
            if original.get("scope") == "metadata":
                desired, replacement = baseline, []
            else:
                desired, replacement = plan("replace", resource, baseline, uris=original["baseline"]["track_uris"])
            desired, metadata = plan("details", resource, desired, details=original["baseline"]["metadata"])
            plans.append({"id": len(plans), "kind": "compensate", "resource": resource, "baseline": baseline,
                          "desired": desired, "batches": replacement + metadata, "batches_done": 0})
        self.doc.update(phase="compensating", status="ready", compensation=plans, compensation_resources=resources,
                        compensation_scope=scope)
        self.doc.pop("compensation_retry_started", None)
        self._save()
        return self.resume()

    def restore_plan(self):
        boundaries = self.doc.get("boundaries")
        if boundaries is None:
            written = {step['resource'] for step in self.doc['plans'] if step['resource'] is not None}
            originals = {resource: stored for resource, stored in self.doc['resources'].items() if resource in written}
        elif boundaries["current"] is None:
            originals = {}
        else:
            current = boundaries["current"]
            resources = {step["resource"] for step in self.doc["plans"][current["start"]:]
                         if step["resource"] is not None}
            originals = {resource: current["baselines"][resource] for resource in resources}
        return {"version": 1, "requires_review": True, "resources": [
            {"resource": resource, "desired": deepcopy(stored["baseline"]),
             "baseline_snapshot_id": stored["snapshot_id"],
             "observed": deepcopy(self.doc.get("observations", {}).get(resource))}
            for resource, stored in sorted(originals.items())
        ], "unresolved_creates": [step["id"] for step in self.doc["plans"]
                                   if step["kind"] == "create" and not step.get("result")]}

    @staticmethod
    def _intent(kind, values):
        if kind == "create":
            return {"kind": kind, **{key: deepcopy(values[key]) for key in ("name", "public", "description")}}
        intent = {"kind": kind, "resource": values["resource"]}
        if kind == "details":
            intent["details"] = {key: deepcopy(value) for key, value in values["details"].items()
                                 if key in ("name", "public", "description")}
        else:
            intent["track_uris"] = list(values["track_uris"])
            if kind == "add":
                intent["position"] = values["position"]
        return intent

    def mutate(self, api, kind, *args, **kwargs):
        self.check_pending_control()
        if self.doc["phase"] != "forward":
            raise NeedsAttention("operation_compensating")
        if "playlist_id" in kwargs:
            kwargs["resource"] = kwargs.pop("playlist_id")
        freeze = getattr(self, f"freeze_{kind}")
        bound = inspect.signature(freeze).bind(*args, **kwargs)
        bound.apply_defaults()
        intent = self._intent(kind, bound.arguments)
        if self.cursor < len(self.doc["plans"]):
            plan_id = self.cursor
            if self.doc["plans"][plan_id].get("intent") != intent:
                self._attention("replayed_intent_changed")
                raise NeedsAttention("replayed_intent_changed")
        else:
            if any(item["state"] != "acknowledged" for step in self.doc["plans"] for item in step["batches"]):
                raise NeedsAttention("unfinished_plan")
            plan_id = freeze(*args, **kwargs)
        self.cursor += 1
        try:
            outcome = self.resume()
        finally:
            if kind == "create":
                cache, user_id = getattr(api, "_cache", None), getattr(api, "_user_id", None)
                if cache and user_id:
                    cache.invalidate_user_playlists(user_id)
            else:
                api._invalidate_playlist_write_cache(intent["resource"])
        if outcome["state"] == "needs_attention":
            raise NeedsAttention(outcome["recovery"]["reason"])
        if kind == "create":
            return deepcopy(self.doc["plans"][plan_id]["result"])
        if kind != "details":
            return True

    def before_http(self, method, path, body):
        if self.active_batch is None:
            raise JournalError("A Spotify write has no frozen batch")
        step, item = self.active_batch
        if (method, path, body) != (item["method"], item["path"], item["body"]):
            raise JournalError("The HTTP mutation differs from its frozen batch")
        if item["attempts"] and step["resource"] is not None:
            observed = self._before_batch(step, item)
            if self._verdict(step["resource"], observed, item["before"]) == CHANGED:
                self._attention("external_edit", {step["resource"]: observed})
                raise NeedsAttention("external_edit")
        item["state"] = "dispatched"
        item["attempts"] += 1
        self._save()
        return step, item

    def after_http(self, ticket, response):
        step, item = ticket
        item["last_http_status"] = response.status_code
        if 200 <= response.status_code < 300:
            try:
                body = response.json() if response.content else None
            except (ValueError, AttributeError):
                body = None
            self._ack(step, item, body)
        elif 400 <= response.status_code < 500:
            item["state"] = "rejected"
            quota = response.status_code == 429 and _quota_exceeded(response)
            if response.status_code == 429:
                retry_after = _retry_after(response)
                item["retry_after"] = retry_after
                if quota:
                    # A terminal refusal survives a crash before the caller can
                    # record failure. Its Retry-After also fences rollback I/O.
                    item["terminal_rejection"] = "quota_exceeded"
                if not quota or retry_after is not None:
                    delay = max(1, 60 if retry_after is None else retry_after)
                    due = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    item["next_attempt_at"] = due.isoformat()
            self._save()
            if response.status_code == 429 and (not quota or retry_after is not None):
                raise OperationDeferred(due)
        else:
            self._save()

    def http_failed(self, ticket, error):
        _step, item = ticket
        item["transport_error"] = type(error).__name__
        self._save()
