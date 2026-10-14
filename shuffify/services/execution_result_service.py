"""Bounded, model-independent durable summaries of routine executions.

Only observed facts enter this module.  The live operation's track arrays and
exception text must never reach ``JobExecution.result_json``.
"""

from collections import Counter
from copy import deepcopy

VERSION = 1
MAX_STEPS = 4
MAX_FACTS = 12
MAX_ID = 128
OPERATIONS = frozenset({"raid", "shuffle", "raid_and_shuffle", "raid_and_drip", "rotate", "drip"})
STEPS = frozenset({"raid", "shuffle", "rotate", "drip"})
STATUSES = frozenset({"complete", "partial", "unknown", "failed", "restored", "not_started", "pending"})
FACT_STATUSES = frozenset({"complete", "failed", "unknown", "restored", "not_required"})
COUNTS = frozenset({
    "tracks_collected", "tracks_moved_to_target", "tracks_removed_from_collection",
    "tracks_reordered", "tracks_archived", "tracks_returned", "archive_overlaps_removed",
    "tracks_removed_from_target", "tracks_removed_from_archive",
})
RESOURCE_KINDS = frozenset({"target", "collection", "archive"})
BOOKKEEPING = frozenset({"pending_stage", "pending_promotion", "pending_restore", "execution_record"})
COMPOSITE_STEPS = {"raid_and_shuffle": ("raid", "shuffle"), "raid_and_drip": ("raid", "drip")}


def _choice(value, choices):
    return isinstance(value, str) and value in choices


def _ident(value):
    return value if isinstance(value, str) and 0 < len(value) <= MAX_ID else None


def _count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def _resource(item):
    if not isinstance(item, dict) or not _choice(item.get("status"), FACT_STATUSES):
        return None
    resource_id = _ident(item.get("resource"))
    if resource_id is None:
        return None
    fact = {"resource": resource_id, "status": item["status"]}
    if _choice(item.get("step"), STEPS | {"archive_purge"}):
        fact["phase"] = item["step"]
    if _choice(item.get("kind"), RESOURCE_KINDS):
        fact["kind"] = item["kind"]
    if _choice(item.get("mutation_type"), ("track_order", "track_membership")):
        fact["mutation_type"] = item["mutation_type"]
    count = _count(item.get("affected_count"))
    if count is not None and item["status"] in ("complete", "not_required"):
        fact["affected_count"] = count
    fact["dispatched"] = item.get("dispatched") is True
    return fact


def _bookkeeping(item):
    if not isinstance(item, dict) or not _choice(item.get("kind"), BOOKKEEPING):
        return None
    if not _choice(item.get("status"), FACT_STATUSES):
        return None
    fact = {"kind": item["kind"], "status": item["status"]}
    resource_id = _ident(item.get("resource"))
    if resource_id:
        fact["resource"] = resource_id
    count = _count(item.get("affected_count"))
    if count is not None and item["status"] in ("complete", "not_required"):
        fact["affected_count"] = count
    return fact


def _step(item):
    if (not isinstance(item, dict) or not _choice(item.get("step"), STEPS)
            or not _choice(item.get("status"), STATUSES)):
        return None
    result = {"step": item["step"], "status": item["status"]}
    counts = item.get("counts", {})
    if not isinstance(counts, dict):
        return None
    cleaned = {key: value for key, value in counts.items()
               if _choice(key, COUNTS) and _count(value) is not None}
    if cleaned:
        result["counts"] = cleaned
    raw_resources = item.get("resources", [])
    raw_bookkeeping = item.get("bookkeeping", [])
    if not isinstance(raw_resources, list) or not isinstance(raw_bookkeeping, list):
        return None
    for fact in raw_resources[:MAX_FACTS]:
        if (not isinstance(fact, dict) or not isinstance(fact.get("status"), str)
                or any(key in fact and fact[key] is not None and not isinstance(fact[key], str)
                       for key in ("step", "kind", "mutation_type"))):
            return None
    for fact in raw_bookkeeping[:MAX_FACTS]:
        if (not isinstance(fact, dict) or not isinstance(fact.get("status"), str)
                or not isinstance(fact.get("kind"), str)):
            return None
    resources = [_resource(r) for r in raw_resources[:MAX_FACTS]]
    bookkeeping = [_bookkeeping(b) for b in raw_bookkeeping[:MAX_FACTS]]
    result["resources"] = [r for r in resources if r is not None]
    result["bookkeeping"] = [b for b in bookkeeping if b is not None]
    return result


def serialize_execution_detail(result_json):
    """Return the stable public DTO, treating invalid/legacy JSON as unavailable."""
    unavailable = {"detail_status": "unavailable", "version": None, "steps": None}
    if (not isinstance(result_json, dict) or type(result_json.get("version")) is not int
            or result_json["version"] != VERSION):
        return unavailable
    if (not _choice(result_json.get("operation"), OPERATIONS)
            or not _choice(result_json.get("outcome"), ("complete", "partial", "unknown"))):
        return unavailable
    execution_id = result_json.get("execution_id")
    if type(execution_id) is not int or execution_id <= 0:
        return unavailable
    raw_steps = result_json.get("steps")
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_STEPS:
        return unavailable
    steps = [_step(step) for step in raw_steps]
    if any(step is None for step in steps):
        return unavailable
    result = {
        "detail_status": "available", "version": VERSION,
        "operation": result_json["operation"], "execution_id": execution_id,
        "outcome": result_json["outcome"], "steps": steps,
    }
    if "run_state" in result_json and not _choice(result_json["run_state"], ("running", "finished")):
        return unavailable
    if "execution_record" in result_json and not _choice(result_json["execution_record"], ("complete", "unknown")):
        return unavailable
    if _choice(result_json.get("run_state"), ("running", "finished")):
        result["run_state"] = result_json["run_state"]
    ids = result_json.get("resource_ids")
    if "resource_ids" in result_json and not isinstance(ids, dict):
        return unavailable
    if isinstance(ids, dict):
        result["resource_ids"] = {key: value for key, value in ids.items()
                                  if _choice(key, RESOURCE_KINDS) and _ident(value)}
    if _choice(result_json.get("execution_record"), ("complete", "unknown")):
        result["execution_record"] = result_json["execution_record"]
    return result


def build_execution_detail(*, operation, execution_id, outcome, steps, resource_ids=None,
                           execution_record="complete", run_state="finished"):
    """Create storage JSON through the same allowlist used for reads."""
    candidate = {
        "version": VERSION, "operation": operation, "execution_id": execution_id,
        "outcome": outcome, "steps": steps, "resource_ids": resource_ids or {},
        "execution_record": execution_record,
        "run_state": run_state,
    }
    public = serialize_execution_detail(candidate)
    if public["detail_status"] != "available":
        raise ValueError("Invalid execution detail")
    return deepcopy({key: value for key, value in public.items() if key != "detail_status"})


def build_live_execution_detail(operation, execution_id, target_id, *, execution_record="complete",
                                run_state="finished"):
    """Summarize an invocation journal without retaining its track snapshots."""
    target_id = _ident(target_id)
    resources = operation.step_resources if hasattr(operation, "step_resources") else None
    raw_steps = operation.steps or [{"step": operation.kind, "status": operation.step_status(
        operation.step, default="failed" if operation.error_code else "complete"
    )}]
    steps = []
    resource_ids = {"target": target_id} if target_id else {}
    for source_step in raw_steps[:MAX_STEPS]:
        name = source_step.get("step")
        if not _choice(name, STEPS):
            continue
        step_resources = resources(name) if resources else []
        books = [b for b in operation.bookkeeping if b.get("step") == name]
        for r in step_resources:
            rid = _ident(r.get("resource"))
            if rid and rid != target_id:
                resource_ids["archive" if name == "rotate" else "collection"] = rid
        facts = []
        for r in step_resources:
            fact = dict(r)
            fact["kind"] = ("target" if r.get("resource") == target_id else
                            "archive" if name == "rotate" else "collection")
            facts.append(fact)
        counts = _observed_counts(name, source_step, step_resources, books, target_id)
        steps.append({
            "step": name, "status": source_step.get("status", "unknown"),
            "counts": counts, "resources": facts, "bookkeeping": books,
        })
    if not steps:
        raise ValueError("No execution steps")
    if run_state == "running":
        for pending in COMPOSITE_STEPS.get(operation.kind, ())[len(steps):]:
            steps.append({"step": pending, "status": "pending", "counts": {}, "resources": [], "bookkeeping": []})
    outcome = operation.outcome()["aggregate_status"]
    if run_state == "running" and outcome == "complete":
        outcome = "partial"
    return build_execution_detail(
        operation=operation.kind, execution_id=execution_id,
        outcome=outcome, steps=steps,
        resource_ids=resource_ids, execution_record=execution_record,
        run_state=run_state,
    )


def _observed_counts(name, step, resources, books, target_id):
    counts = {}
    if name == "raid":
        stage = next((b for b in books if b.get("kind") == "pending_stage"), None)
        if stage and stage.get("status") in ("complete", "not_required"):
            counts["tracks_collected"] = stage.get("affected_count")
        elif step.get("status") == "complete":
            counts["tracks_collected"] = step.get("tracks_added")
    elif name in ("drip", "shuffle"):
        for r in resources:
            if r.get("status") not in ("complete", "not_required"):
                continue
            is_target = r.get("resource") == target_id
            key = ("tracks_reordered" if name == "shuffle" else
                   "tracks_moved_to_target" if is_target else "tracks_removed_from_collection")
            counts[key] = r.get("affected_count")
        if name == "drip" and (step.get("status") == "not_started" or
                               (step.get("status") == "complete" and step.get("tracks_added") == 0)):
            counts.setdefault("tracks_moved_to_target", 0)
            counts.setdefault("tracks_removed_from_collection", 0)
        if name == "shuffle" and step.get("status") == "complete" and not resources:
            counts["tracks_reordered"] = 0
    elif name == "rotate":
        for r in resources:
            if r.get("status") != "complete":
                continue
            before, after = r.get("baseline_track_uris"), r.get("current_track_uris")
            if not isinstance(before, list) or not isinstance(after, list):
                continue
            removed = sum((Counter(before) - Counter(after)).values())
            added = sum((Counter(after) - Counter(before)).values())
            if r.get("step") == "archive_purge":
                counts["archive_overlaps_removed"] = removed
            elif r.get("resource") == target_id:
                counts["tracks_returned"] = added
                counts["tracks_removed_from_target"] = removed
            else:
                counts["tracks_archived"] = added
                counts["tracks_removed_from_archive"] = removed
        if step.get("status") == "not_started" or (
            step.get("status") == "complete" and step.get("tracks_added") == 0 and not resources
        ):
            counts["tracks_archived"] = 0
            counts["tracks_returned"] = 0
            counts["tracks_removed_from_target"] = 0
            counts["tracks_removed_from_archive"] = 0
    return counts
