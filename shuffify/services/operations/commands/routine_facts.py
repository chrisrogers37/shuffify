"""Pure projections of accepted steps and settled journal batches into run facts."""

from collections import Counter
from copy import deepcopy

from shuffify.services.execution_result_service import build_live_execution_detail, serialize_execution_detail
from shuffify.services.live_operation_service import LiveOperation, operation_result
from shuffify.services.workshop_commit_service import ObservedPlaylist

BINDING_KEY = "routine.binding.v1"


def plan_key(step):
    return f"routine.{step}.plan.v1"


def input_key(step):
    return f"routine.{step}.inputs.v1"


def _changed(before, after, order=False):
    if order:
        return sum(left != right for left, right in zip(before, after)) + abs(len(before) - len(after))
    return sum((Counter(before) - Counter(after)).values()) + sum((Counter(after) - Counter(before)).values())


def _actions(prepared, name, target):
    if not prepared:
        return []
    if "actions" in prepared:
        return prepared["actions"]
    return (
        [
            {
                "kind": "replace",
                "resource": target,
                "track_uris": prepared["desired"][target]["track_uris"],
                "before": prepared["before"][target],
            }
        ]
        if name == "shuffle"
        else []
    )


def _after(action):
    values, before = action["track_uris"], deepcopy(action["before"]["track_uris"])
    if action["kind"] == "remove":
        return [value for value in before if value not in set(values)]
    if action["kind"] == "replace":
        return list(values)
    position = action.get("position")
    position = len(before) if position is None else position
    before[position:position] = values
    return before


def build_result(runtime, command, state, *, error_code=None, message=None, http_status=None, retryable=False):
    """Use durable observations only; never read Spotify or mutate a history/session."""
    document = runtime.get_journal()
    private = document.get("runtime", {})
    prepared = private.get("prepared", {})
    boundaries = document.get("boundaries", {})
    accepted = {item["name"]: item for item in boundaries.get("accepted", [])}
    current = boundaries.get("current")
    kind, target = command.definition.job_type, command.playlist_id
    operation = LiveOperation(kind, None, runtime.user)
    operation.locked_target, operation.triggered_by = target, command.triggered_by
    operation.error_code = error_code
    operation.book("execution_record", "complete", affected_count=0)
    run_started = prepared.get(BINDING_KEY, {}).get("run_started") is True
    started = (
        runtime.operation.started_at.isoformat() if run_started and runtime.operation.started_at is not None else None
    )
    local_only_failure = (
        private.get("routine_db_failure")
        or private.get("history_unconfirmed")
        or error_code in {"terminal_finalization_failed", "verified_step_publication_failed"}
    )
    uncertainty = (
        state == "needs_attention" and (not local_only_failure or document.get("status") == "needs_attention")
    ) or error_code == "producer_result_inconsistent"
    counts, target_total, skipped = {}, 0, None
    failed_step = private.get("routine_db_failure", {}).get("step") or (current or {}).get("name")
    if failed_step is None and error_code:
        failed_step = next((name for name in command.steps if name not in accepted), None)
    for index, name in enumerate(command.steps):
        operation.step = name
        frozen = prepared.get(plan_key(name))
        accepted_step = accepted.get(name)
        child = accepted_step.get("result", {}) if accepted_step else {}
        receipt = child.get("receipt", {})
        child_result = child.get("result", frozen.get("result", {}) if frozen else {})
        for resource, value in (frozen or {}).get("before", {}).items():
            operation.observed[resource] = ObservedPlaylist(
                value.get("metadata", {}).get("name") or resource,
                tuple(value["track_uris"]),
                (),
                started,
                value.get("snapshot_id"),
                value.get("metadata", {}),
            )
        if target in (frozen or {}).get("before", {}):
            target_total = len(frozen["before"][target]["track_uris"])
        gate = child_result.get("skipped_reason") == "drip_disabled"
        if gate:
            skipped = "drip_disabled"
        active = current is not None and current["name"] == name
        publication_pending = active and error_code == "verified_step_publication_failed" and not uncertainty
        step_started = frozen is not None and not gate
        restored = active and state == "rolled_back"
        step_status = (
            "not_started"
            if not step_started
            else "complete"
            if accepted_step
            else "restored"
            if restored
            else "partial"
            if private.get("routine_db_failure", {}).get("step") == name or publication_pending
            else "unknown"
            if state == "needs_attention"
            else "failed"
        )
        if accepted_step and child_result.get("success") is False:
            step_status = "partial"
        if accepted_step:
            scope = document.get("plans", [])[accepted_step["start"] : accepted_step["end"]]
        elif active:
            scope = document.get("plans", [])[current["start"] :]
        else:
            scope = []
        groups = {}
        for offset, action in enumerate(_actions(frozen or {}, name, target)):
            resource = action["resource"]
            phase = "archive_purge" if action.get("phase") == "archive_purge" else name
            key = phase, resource
            group = groups.setdefault(key, {"baseline": action["before"]["track_uris"], "desired": [], "batches": []})
            group["desired"] = _after(action)
            if offset < len(scope):
                group["batches"].extend(scope[offset]["batches"])
        for (phase, resource), group in groups.items():
            batches = group["batches"]
            attempted = any(
                item.get("attempts", 0) or item["state"] in {"acknowledged", "dispatched"} for item in batches
            )
            ack = [item for item in batches if item["state"] == "acknowledged"]
            unknown = uncertainty or any(item["state"] == "dispatched" for item in batches)
            actual = ack[-1]["after"]["track_uris"] if ack else group["baseline"]
            status = (
                "unknown"
                if unknown and attempted
                else "restored"
                if restored and ack
                else (
                    "complete"
                    if actual == group["desired"] and attempted
                    else "not_required"
                    if not attempted and group["baseline"] == group["desired"]
                    else "failed"
                )
            )
            if restored and resource in current.get("baselines", {}):
                actual = current["baselines"][resource]["baseline"]["track_uris"]
            fact = {
                "resource": resource,
                "resource_name": operation.observed[resource].name,
                "step": phase,
                "mutation_type": "track_order" if name == "shuffle" else "track_membership",
                "status": status,
                "dispatched": bool(attempted),
                "retryable": status == "failed"
                and attempted
                and not ack
                and any(item["state"] == "rejected" for item in batches),
                "affected_count": None
                if status == "unknown"
                else _changed(group["baseline"], actual, name == "shuffle"),
                "error_code": error_code if status in {"failed", "unknown"} else None,
                "verification": "ordered",
                "baseline_track_uris": deepcopy(group["baseline"]),
                "desired_track_uris": deepcopy(group["desired"]),
            }
            if status != "unknown":
                fact.update(current_track_uris=deepcopy(actual), loaded_at=started)
            operation.resources.append(fact)
            operation.resource_steps[phase, resource] = name
            if resource == target:
                target_total = None if status == "unknown" else len(actual)
        if (
            uncertainty
            and not accepted_step
            and any(item["status"] == "unknown" for item in operation.step_resources(name))
        ):
            step_status = "unknown"
        if name in {"raid", "drip"}:
            book_kind = "pending_stage" if name == "raid" else "pending_promotion"
            entry = receipt.get(book_kind)
            if entry:
                operation.book(book_kind, entry["status"], resource=target, affected_count=entry["affected_count"])
                counts[name] = entry["affected_count"] if name == "raid" else child_result.get("tracks_added", 0)
            else:
                operation.book(
                    book_kind,
                    "unknown" if publication_pending
                    else "failed" if private.get("routine_db_failure", {}).get("step") == name else "not_required",
                    resource=target,
                    affected_count=None if publication_pending else 0,
                    error_code="required_bookkeeping_failed"
                    if private.get("routine_db_failure", {}).get("step") == name or publication_pending
                    else None,
                )
                counts[name] = 0
            if name == "raid" and entry is None:
                collection_fact = next(
                    (fact for fact in operation.step_resources(name) if fact["resource"] != target), None
                )
                if collection_fact is not None:
                    counts[name] = (
                        None
                        if collection_fact["status"] == "unknown"
                        else child_result.get("tracks_added", 0)
                        if collection_fact["status"] in {"complete", "not_required"}
                        else 0
                    )
            if name == "drip":
                target_fact = next(
                    (fact for fact in operation.step_resources(name) if fact["resource"] == target), None
                )
                if target_fact is not None:
                    counts[name] = (
                        None
                        if target_fact["status"] == "unknown"
                        else child_result.get("tracks_added", 0)
                        if target_fact["status"] in {"complete", "not_required"}
                        else 0
                    )
            failures = child_result.get("failed_sources")
            if failures:
                operation.book(
                    "source_resolution", "failed", failed_sources=failures, error_code="source_resolution_failed"
                )
        if not step_started:
            operation.record_not_started([name])
        elif not groups and name in {"shuffle", "rotate", "drip"} and target in frozen["before"]:
            baseline = frozen["before"][target]["track_uris"]
            operation.resources.append(
                {
                    "resource": target,
                    "resource_name": operation.observed[target].name,
                    "step": name,
                    "mutation_type": "track_order" if name == "shuffle" else "track_membership",
                    "status": "unknown" if uncertainty else "complete",
                    "dispatched": False,
                    "affected_count": None if uncertainty else 0,
                    "retryable": False,
                    "error_code": error_code,
                    "verification": "ordered",
                    "baseline_track_uris": deepcopy(baseline),
                    "desired_track_uris": deepcopy(baseline),
                    **({} if uncertainty else {"current_track_uris": deepcopy(baseline), "loaded_at": started}),
                }
            )
        if name == "rotate":
            counts[name] = child_result.get("tracks_added", 0) if accepted_step else 0
        step_code = child_result.get("error_code") or (error_code if name == failed_step else None)
        if (
            not step_started
            and not active
            and any(step["status"] in {"partial", "failed", "unknown"} for step in operation.steps)
        ):
            step_code = "previous_step_incomplete"
        step_outcome = operation_result(
            name,
            resources=operation.step_resources(name),
            bookkeeping=[fact for fact in operation.bookkeeping if fact.get("step") == name],
            error_code=step_code,
            steps=[{"status": step_status}],
        )
        operation.steps.append(
            {
                "aggregate_status": (
                    "complete"
                    if accepted_step and child_result.get("success") is not False
                    else step_outcome["aggregate_status"]
                ),
                "error_code": step_code,
                "step": name,
                "status": step_status,
                "step_index": index,
                "execution_id": runtime.operation.id,
                "execution_state": "recorded" if step_started else "not_started",
                "started_at": started if step_started else None,
                "tracks_added": counts.get(name, 0),
                "tracks_total": target_total,
            }
        )
    if (
        uncertainty
        or not any(plan_key(name) in prepared for name in command.steps)
        and (run_started or error_code == "database_read_failed")
    ):
        target_total = None
    operation.error_code = error_code
    operation.retryable = retryable or (
        error_code == "write_rejected" and any(fact.get("retryable") for fact in operation.resources)
    )
    detail = serialize_execution_detail(build_live_execution_detail(operation, runtime.operation.id, target))
    complete = state == "succeeded"
    legacy_status = (
        "success"
        if complete
        else "failed_rolled_back"
        if state == "rolled_back"
        else "skipped"
        if skipped and not operation.has_effects()
        else "failed"
    )
    added = counts.get("raid", counts.get("drip", counts.get("rotate", 0)))
    if state in {"needs_attention", "rolled_back"} and message == "The run completed.":
        message = None
    message = message or (
        "The run completed."
        if complete
        else "The current step was restored; completed earlier steps were retained."
        if state == "rolled_back"
        else "Review the operation before trying again."
        if state == "needs_attention"
        else "The run did not complete."
    )
    result = {
        "success": complete,
        "status": legacy_status,
        "execution_id": runtime.operation.id,
        "started_at": started,
        "execution_state": "recorded" if run_started else "not_started",
        "triggered_by": command.triggered_by,
        "tracks_added": added,
        "tracks_total": target_total,
        "operation": operation.outcome(),
        "result_detail": detail,
        "message": message,
        "category": "success" if complete else "error",
        "error_code": error_code,
        "retryable": operation.outcome()["retryable"],
        "http_status": http_status or (200 if complete else 502),
    }
    if not complete:
        result["error"] = message
    if "raid" in command.steps and "drip" in command.steps:
        result.update(tracks_raided=counts.get("raid", 0), tracks_dripped=counts.get("drip", 0))
    if skipped:
        result["skipped_reason"] = skipped
    if command.definition.schedule_id is not None:
        result["result"] = {
            key: deepcopy(value)
            for key, value in result.items()
            if key
            in {
                "execution_id",
                "started_at",
                "execution_state",
                "triggered_by",
                "tracks_added",
                "tracks_total",
                "tracks_raided",
                "tracks_dripped",
                "status",
                "error_code",
                "result_detail",
                "skipped_reason",
                "message",
                "error",
            }
        }
        result["result"]["steps"] = deepcopy(operation.steps)
    return result
