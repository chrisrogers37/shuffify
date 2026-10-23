"""Freeze Rotate choices once, then apply only those choices through the journal.

The command orchestrator owns configuration capture/revalidation, snapshots,
history, local receipts and the composite acceptance/compensation boundary. This
helper performs authorized Spotify reads and observed writes, without database
effects or a second execution identity.
"""

import random
from copy import deepcopy
from dataclasses import asdict, dataclass

from shuffify.services.executors.base_executor import JobExecutionError
from shuffify.services.operations.executors.resources import (
    expected_baseline,
    observe_resources,
    verify_resources,
)
from shuffify.spotify.observation import current_observer


@dataclass(frozen=True)
class RotateConfig:
    """Server-captured values normalized by the existing rotation_settings helper."""

    user_id: int
    target_playlist_id: str
    archive_playlist_id: str
    rotation_mode: str = "swap"
    rotation_count: int = 5
    target_size: int | None = None
    protect_count: int = 0
    locked_uris: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "locked_uris", tuple(self.locked_uris))

    def document(self):
        value = asdict(self)
        value["locked_uris"] = list(self.locked_uris)
        return value


def _validate(runtime, config, prepare_key):
    if not isinstance(config, RotateConfig):
        raise JobExecutionError("Rotation requires captured configuration")
    if type(config.user_id) is not int or config.user_id != runtime.user.id:
        raise JobExecutionError("Rotation configuration belongs to another user")
    targets = (config.target_playlist_id, config.archive_playlist_id)
    if any(not isinstance(value, str) or not value for value in targets) or targets[0] == targets[1]:
        raise JobExecutionError("The archive must be distinct from the target")
    if not set(targets).issubset(runtime.lease.targets):
        raise JobExecutionError("Rotation configuration includes an unreserved playlist")
    if config.rotation_mode != "swap":
        raise JobExecutionError(f"Invalid rotation_mode: {config.rotation_mode}")
    if (type(config.rotation_count) is not int or config.rotation_count < 1
            or type(config.protect_count) is not int or config.protect_count < 0
            or any(not isinstance(value, str) for value in config.locked_uris)):
        raise JobExecutionError("Rotation configuration must contain normalized counts and locks")
    if not isinstance(prepare_key, str) or not prepare_key or len(prepare_key) > 120:
        raise JobExecutionError("Rotation requires a namespaced preparation key of at most 120 characters")


def _sample_at_most(pool, count):
    return list(pool) if len(pool) <= count else random.sample(pool, count)


def _prepare(runtime, config):
    target, archive = config.target_playlist_id, config.archive_playlist_id
    before = {target: runtime.read_resource(target)}
    current = deepcopy(before)
    actions = []
    choices = {"phase": "empty", "purged_uris": [], "eligible_uris": [],
               "swap_in_uris": [], "swap_out_uris": [], "overflow_uris": []}
    result = {"tracks_added": 0, "tracks_total": len(before[target]["track_uris"])}

    def prepared():
        return {"version": 1, "config": config.document(), "before": before,
                "desired": current, "actions": actions, "choices": choices, "result": result}

    # Empty targets skip the cap check and archive read.
    production = before[target]["track_uris"]
    if not production:
        return prepared()
    if type(config.target_size) is not int or config.target_size < 1:
        raise JobExecutionError("Swap rotation requires a playlist size cap (target_size) of at least 1")
    before[archive] = runtime.read_resource(archive)
    current[archive] = deepcopy(before[archive])

    def action(kind, resource, values, phase):
        if not values:
            return
        baseline = deepcopy(current[resource])
        if kind == "remove":
            selected = set(values)
            current[resource]["track_uris"] = [uri for uri in baseline["track_uris"] if uri not in selected]
        else:
            current[resource]["track_uris"].extend(values)
        actions.append({"kind": kind, "resource": resource, "track_uris": list(values),
                        "before": baseline, "phase": phase})

    production_set = set(production)
    overlaps = [uri for uri in before[archive]["track_uris"] if uri in production_set]
    choices["purged_uris"] = overlaps
    action("remove", archive, overlaps, "archive_purge")
    archived = current[archive]["track_uris"]
    archive_set = set(archived)
    locked = set(config.locked_uris)
    eligible = [uri for uri in production[config.protect_count:] if uri not in locked]
    choices["eligible_uris"] = eligible
    if not eligible:
        choices["phase"] = "protected"
        result["skipped_reason"] = "all_tracks_protected"
        return prepared()

    if len(production) > config.target_size:
        choices["phase"] = "overflow"
        selected = _sample_at_most(eligible, len(production) - config.target_size)
        choices["overflow_uris"] = selected
        # Archive first: an interruption leaves a duplicate, never a lost track.
        action("add", archive, [uri for uri in selected if uri not in archive_set], "rotate")
        action("remove", target, selected, "rotate")
    else:
        choices["phase"] = "swap"
        incoming = list(archived[:config.rotation_count])
        outgoing = _sample_at_most(eligible, len(incoming))
        choices["swap_in_uris"], choices["swap_out_uris"] = incoming, outgoing
        if incoming and outgoing:
            result["tracks_added"] = min(len(incoming), len(outgoing))
            # Both adds precede both removes so every interruption leaves a
            # track in at least one playlist (at worst in both).
            action("add", archive, [uri for uri in outgoing if uri not in archive_set], "rotate")
            # Keep the full FIFO incoming list even if fewer outgoing
            # tracks were eligible; tracks_added still reports the minimum.
            action("add", target, incoming, "rotate")
            action("remove", target, outgoing, "rotate")
            action("remove", archive, incoming, "rotate")
    result["tracks_total"] = len(current[target]["track_uris"])
    return prepared()


def prepare_rotate(runtime, config, *, prepare_key):
    """Freeze authorized reads, exact phase orders and random choices before effects."""
    _validate(runtime, config, prepare_key)
    prepared = runtime.prepare(prepare_key, lambda: _prepare(runtime, config))
    if prepared.get("version") != 1 or prepared.get("config") != config.document():
        raise JobExecutionError("The saved rotation configuration changed")
    return prepared


def execute_rotate(runtime, config, *, prepare_key):
    """Apply a prepared rotation inside the caller's composite step and observer.

    Returns private complete ``before``/verified ``after`` resources plus the
    run ``result`` and frozen ``choices`` for final fenced local bookkeeping.
    The caller must not publish these private resource baselines to clients.
    """
    _validate(runtime, config, prepare_key)
    if current_observer() is not runtime.journal:
        raise JobExecutionError("Rotation requires the operation's outer journal observer")
    prepared = prepare_rotate(runtime, config, prepare_key=prepare_key)
    observe_resources(runtime, prepared["before"])
    for action in prepared["actions"]:
        runtime.check_pending_control()
        resource, values = action["resource"], action["track_uris"]
        baseline = expected_baseline(runtime, resource, action["before"])
        if action["kind"] == "remove":
            runtime.journal.ensure_remove(resource, values, expected_baseline=baseline)
            if not runtime.api.playlist_remove_items(resource, values):
                raise JobExecutionError("Rotation playlist_remove_items returned falsy")
        else:
            runtime.journal.ensure_add(resource, values, expected_baseline=baseline)
            runtime.api.playlist_add_items(resource, values)
    runtime.check_pending_control()
    after = verify_resources(runtime, prepared["desired"], "rotate")
    return {"before": deepcopy(prepared["before"]), "after": after,
            "result": deepcopy(prepared["result"]), "choices": deepcopy(prepared["choices"])}
