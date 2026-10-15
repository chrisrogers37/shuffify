"""Fenced write-ahead plans with conservative recovery and separate compensation."""

import inspect
import math
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyRateLimitError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)
from shuffify.spotify.observation import observe_mutations

from .planner import batch, content, plan
from .recovery import reconcile_resource


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


class DurableJournal:
    """Persist through injected fenced checkpoints; never hold a transaction over I/O."""

    def __init__(self, get_journal, checkpoint, read_resource, dispatch, settle_resource=None, read_metadata=None):
        self.get_journal = get_journal
        self.checkpoint = checkpoint
        self.read_resource = read_resource
        self.dispatch = dispatch
        self.settle_resource = settle_resource
        self.read_metadata = read_metadata
        self.active_batch = None
        self.cursor = 0
        self._observation_depth = 0
        self.doc = deepcopy(get_journal() or {})
        if self.doc.get("version", 1) != 1:
            raise JournalError("Unsupported journal version")
        for key, value in {
            "version": 1, "phase": "forward", "status": "ready", "resources": {}, "plans": [],
            "compensation": [], "choices": {}, "recovery": {"available": False, "action": None, "reason": None},
        }.items():
            self.doc.setdefault(key, deepcopy(value))

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
        self._defer_if_needed()
        if self.doc.get("abort") and self.doc["phase"] == "forward":
            raise OperationAborted(**self.doc["abort"])

    def request_abort(self, reason, result=None):
        self.doc.setdefault("abort", {"reason": reason, "result": deepcopy(result)})
        self._save()
        raise OperationAborted(**self.doc["abort"])

    def _defer_if_needed(self):
        now = datetime.now(timezone.utc)
        deadlines = []
        for step in self._plans():
            for item in step["batches"]:
                if item["state"] == "rejected" and item.get("next_attempt_at"):
                    due = datetime.fromisoformat(item["next_attempt_at"])
                    if due.tzinfo is None:
                        raise JournalError("A retry deadline must include its timezone")
                    if due > now:
                        deadlines.append(due)
        if deadlines:
            raise OperationDeferred(max(deadlines))

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
                 "choices", "recovery", "observations", "progress", "abort"}
        for key, value in (self.get_journal() or {}).items():
            if key not in owned:
                self.doc[key] = deepcopy(value)
        # Only pass detached copies: a failed checkpoint cannot mutate the persisted document.
        self.checkpoint(deepcopy(self.doc))

    @staticmethod
    def _observed(raw):
        observed = {**deepcopy(raw), **content(raw)}
        if observed.get("scope") != "metadata" and (
                not isinstance(observed.get("snapshot_id"), str) or not observed["snapshot_id"]):
            raise JournalError("A complete observation requires a playlist snapshot")
        return observed

    def _read(self, resource, *, scope=None):
        scope = scope or self.doc["resources"].get(resource, {}).get("scope", "tracks")
        if scope == "metadata":
            if self.read_metadata is None:
                raise JournalError("No authorized metadata reader is configured")
            return self._observed({**self.read_metadata(resource), "scope": "metadata"})
        return self._observed(self.read_resource(resource))

    def _base(self, resource, *, scope="tracks", expected_baseline=None):
        self.check_pending_control()
        if self.doc["status"] == "needs_attention" or self.doc["phase"] != "forward":
            raise NeedsAttention(self.doc["recovery"].get("reason") or "operation_blocked")
        if any(item["state"] == "dispatched" for step in self.doc["plans"] for item in step["batches"]):
            raise NeedsAttention("unfinished_dispatch")
        stored = self.doc["resources"].get(resource)
        if stored and stored.get("scope") == "metadata" and scope != "metadata":
            raise NeedsAttention("metadata_scope_cannot_change_tracks")
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
        return deepcopy(previous[-1]["desired"] if previous else self.doc["resources"][resource]["baseline"])

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

    def freeze_add(self, resource, track_uris, position=None):
        return self._freeze("add", resource, uris=deepcopy(list(track_uris)), position=position)

    def freeze_remove(self, resource, track_uris):
        return self._freeze("remove", resource, uris=deepcopy(list(track_uris)))

    def freeze_details(self, resource, *, expected_baseline=None, **details):
        return self._freeze("details", resource, details=details, expected_baseline=expected_baseline)

    def ensure_replace(self, resource, track_uris, *, expected_baseline):
        return self._ensure("replace", resource, expected_baseline, track_uris=track_uris)

    def ensure_details(self, resource, *, expected_baseline, **details):
        return self._ensure("details", resource, expected_baseline, details=details)

    def _ensure(self, kind, resource, expected_baseline, **values):
        self.check_pending_control()
        if self.cursor < len(self.doc["plans"]):
            stored = self.doc["plans"][self.cursor]
            intent = self._intent(kind, {"resource": resource, **values})
            if stored.get("intent") != intent or stored["baseline"] != content(expected_baseline):
                self._attention("prepared_intent_changed")
                raise NeedsAttention("prepared_intent_changed")
            return self.cursor
        return self._freeze(kind, resource, expected_baseline=expected_baseline,
                            **({"uris": values["track_uris"]} if kind == "replace" else values))

    def freeze_create(self, name, public=False, description=""):
        self.check_pending_control()
        if self.doc["status"] == "needs_attention" or self.doc["phase"] != "forward":
            raise NeedsAttention("operation_blocked")
        if any(item["state"] == "dispatched" for step in self.doc["plans"] for item in step["batches"]):
            raise NeedsAttention("unfinished_dispatch")
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
        self.doc["recovery"] = {"available": bool(self.doc["resources"]), "action": "restore" if self.doc["resources"]
                                else None, "reason": reason}
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

    def _reconcile(self):
        observations, updates = {}, []
        for step in self._plans():
            if step["kind"] == "create" and step["batches"][0]["state"] == "dispatched":
                return self._attention("unidentified_creation")
        # Read every resource before acknowledging or dispatching any resumed work.
        for resource, stored in sorted(self.doc["resources"].items()):
            batches = self._resource_batches(resource)
            ambiguous = any(item["state"] == "dispatched" for item in batches)
            if ambiguous and self.settle_resource and stored.get("scope", "tracks") == "tracks":
                observed = self._observed(self.settle_resource(resource))
            else:
                observed = self._read(resource)
            observations[resource] = observed
            base = stored if self.doc["phase"] == "forward" else self.doc["compensation_resources"][resource]
            acknowledgements, reason = reconcile_resource(base, batches, observed)
            if reason and reason != "replay_settled":
                return self._attention(reason, observations)
            updates.append((resource, acknowledgements, reason))
        for resource, acknowledgements, reason in updates:
            for item in acknowledgements:
                item.update(state="acknowledged", snapshot_id=observations[resource].get("snapshot_id"), inferred=True)
            if reason == "replay_settled":
                pending = next(item for item in self._resource_batches(resource) if item["state"] != "acknowledged")
                pending["state"] = "prepared"
            acknowledged = [item for item in self._resource_batches(resource) if item["state"] == "acknowledged"]
            if acknowledged and acknowledged[-1].get("snapshot_id") is None:
                acknowledged[-1]["snapshot_id"] = observations[resource].get("snapshot_id")
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

    def resume(self):
        if self.doc["status"] == "needs_attention":
            return self.outcome()
        self.check_pending_control()
        conflict = self._reconcile()
        if conflict:
            return conflict
        self.doc["status"] = "running"
        for step in self._plans():
            for item in step["batches"]:
                if item["state"] == "acknowledged":
                    continue
                resource = step["resource"]
                if resource is not None:
                    observed = self._read(resource)
                    matches = content(observed) == item["before"]
                    expected_snapshot = self._expected_snapshot(resource)
                    if not matches or expected_snapshot and observed.get("snapshot_id") != expected_snapshot:
                        return self._attention("external_edit", {resource: observed})
                    item["snapshot_before"] = observed.get("snapshot_id")
                item.update(state="dispatched", dispatched_at=datetime.now(timezone.utc).isoformat())
                self._save()
                self.active_batch = (step, item)
                try:
                    with self.observe():
                        response = self.dispatch(item["method"], item["path"], deepcopy(item["body"]))
                    self._ack(step, item, response)
                except (SpotifyAuthError, SpotifyForbiddenError, SpotifyNotFoundError,
                        SpotifyRateLimitError, SpotifyWriteRejectedError):
                    if item["state"] != "acknowledged":
                        item["state"] = "rejected"
                        self._save()
                    raise
                finally:
                    self.active_batch = None
        conflict = self._reconcile()
        if conflict:
            return conflict
        self.doc["status"] = "rolled_back" if self.doc["phase"] == "compensating" else "succeeded"
        self.doc["recovery"] = {"available": False, "action": None, "reason": None}
        self._save()
        return self.outcome()

    def compensate(self):
        self._defer_if_needed()
        if self.doc["phase"] == "compensating":
            return self.resume()
        if self.doc["status"] == "needs_attention":
            return self.outcome()
        conflict = self._reconcile()
        if conflict:
            return conflict
        resources, plans = {}, []
        for resource, original in sorted(self.doc["resources"].items()):
            observed = self._read(resource)
            # The second observation protects compensation from an edit after reconciliation.
            batches = self._resource_batches(resource)
            _, reason = reconcile_resource(original, batches, observed)
            if reason:
                return self._attention(reason, {resource: observed})
            baseline = content(observed)
            for name, value in baseline["metadata"].items():
                prior = original["baseline"]["metadata"].get(name)
                if value != prior and prior is None:
                    return self._attention("baseline_metadata_unavailable", {resource: observed})
            resources[resource] = {"baseline": baseline, "snapshot_id": observed.get("snapshot_id")}
            if original.get("scope") == "metadata":
                desired, replacement = baseline, []
            else:
                desired, replacement = plan("replace", resource, baseline, uris=original["baseline"]["track_uris"])
            desired, metadata = plan("details", resource, desired, details=original["baseline"]["metadata"])
            plans.append({"id": len(plans), "kind": "compensate", "resource": resource, "baseline": baseline,
                          "desired": desired, "batches": replacement + metadata, "batches_done": 0})
        if any(step["kind"] == "create" for step in self.doc["plans"]):
            return self._attention("created_playlist_requires_review")
        self.doc.update(phase="compensating", status="ready", compensation=plans, compensation_resources=resources)
        self._save()
        return self.resume()

    def restore_plan(self):
        return {"version": 1, "requires_review": True, "resources": [
            {"resource": resource, "desired": deepcopy(stored["baseline"]),
             "baseline_snapshot_id": stored["snapshot_id"],
             "observed": deepcopy(self.doc.get("observations", {}).get(resource))}
            for resource, stored in sorted(self.doc["resources"].items())
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
            observed = self._read(step["resource"])
            metadata_only = self.doc["resources"][step["resource"]].get("scope") == "metadata"
            if content(observed) != item["before"] or (
                    not metadata_only and observed.get("snapshot_id") != item["snapshot_before"]):
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
            if response.status_code == 429:
                try:
                    retry_after = max(0, int(response.headers.get("Retry-After")))
                except (ValueError, TypeError, AttributeError):
                    retry_after = None
                item["retry_after"] = retry_after
                due = datetime.now(timezone.utc) + timedelta(seconds=max(1, 60 if retry_after is None else retry_after))
                item["next_attempt_at"] = due.isoformat()
            self._save()
            if response.status_code == 429:
                raise OperationDeferred(due)
        else:
            self._save()

    def http_failed(self, ticket, error):
        _step, item = ticket
        item["transport_error"] = type(error).__name__
        self._save()
