"""Reviewed scope of Workshop and routine executor actions.

A review (``live_executor_context_service``) and its run (the worker's
``routine_capture.require_review`` when it binds the command) derive every
fingerprinted input through these helpers, so the two sides cannot drift. Each
reader returns fresh rows and raises on a database failure; a failed read never
stands in for an absent link, no sources or no locks. Names, timestamps,
``last_*``, ``is_enabled``, schedule timing, pending rows and random track
choices are deliberately not inputs.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event
from sqlalchemy.orm import Session

from shuffify.enums import JobType, RotationMode
from shuffify.models.db import UpstreamSource
from shuffify.services.live_review_bindings import fingerprint, scope_fingerprint
from shuffify.services.playlist_pair_service import PlaylistPairService
from shuffify.services.raid_link_service import RaidLinkService
from shuffify.services.track_lock_service import TrackLockService
from shuffify.shuffle_algorithms.registry import ShuffleRegistry

PART_PURPOSE = "shuffify.live.step-part.v1"
# Executor steps per saved job type, in the order the runner dispatches them.
JOB_STEPS = {
    JobType.RAID: ("raid",),
    JobType.SHUFFLE: ("shuffle",),
    JobType.RAID_AND_SHUFFLE: ("raid", "shuffle"),
    JobType.RAID_AND_DRIP: ("raid", "drip"),
    JobType.ROTATE: ("rotate",),
    JobType.DRIP: ("drip",),
}
# The steps of each explicitly requested Workshop action.
DIRECT_STEPS = {"raid": ("raid",), "drip": ("drip",), "raid_and_drip": ("raid", "drip")}
# An explicitly requested Drip runs past the saved Drip setting.
DIRECT_DRIP_PARAMS = {"force": True}


class ReviewChanged(Exception):
    """A run's recomputed scope is not the reviewed one."""


class InvalidSettings(ValueError):
    """A routine's saved settings cannot run as saved."""


def read_sources(user_id, target_id):
    return (
        UpstreamSource.query.filter_by(user_id=user_id, target_playlist_id=target_id)
        .order_by(UpstreamSource.id)
        .all()
    )


def link_ref(link):
    return None if link is None else {"id": link.id, "collection_playlist_id": link.raid_playlist_id}


def pair_ref(pair):
    return None if pair is None else {"id": pair.id, "archive_playlist_id": pair.archive_playlist_id}


def drip_forced(params):
    """Whether a routine's parameters force its Drip past the link's saved Drip setting."""
    return bool((params or {}).get("force"))


def drip_override(params, default=None):
    """A routine's own Drip count, as saved, or ``default`` when it saves none.

    Any saved ``drip_count``, whatever its value, is the routine's own: the
    drip executor then never reads the link's count (and fails on a value it
    cannot convert).
    """
    return (params or {}).get("drip_count", default)


def drip_gate(params, link):
    """The drip executor's gate: a forced run ignores the saved Drip setting."""
    if drip_forced(params):
        return "forced"
    return "open" if link.drip_enabled else "closed"


def effective_drip_count(params, link):
    """The drip executor's count, including a saved routine's override."""
    return max(1, int(drip_override(params, link.drip_count)))


def rotation_settings(params):
    """(mode, count, cap, protect), normalized exactly as the rotate executor uses them."""
    params = params or {}
    mode = params.get("rotation_mode", RotationMode.SWAP)
    count = max(1, int(params.get("rotation_count", 5)))
    cap = params.get("target_size")
    if cap is not None:
        cap = max(1, int(cap))
    return mode, count, cap, max(0, int(params.get("protect_count", 0)))


def ordered(values):
    """A canonical order for JSON values (review inputs, setup digests) that may mix types; duplicates are kept."""
    return sorted(values, key=lambda value: json.dumps(value, sort_keys=True))


def _saved_params(params):
    """A routine's saved parameters: an object, or nothing saved."""
    params = params or {}
    if not isinstance(params, dict):
        raise InvalidSettings("Saved parameters are not an object")
    return params


def _converted(convert, *args):
    """Saved settings converted as their executor converts them, or fingerprinted; failing that, they cannot run."""
    try:
        return convert(*args)
    except (TypeError, ValueError) as error:
        raise InvalidSettings(str(error)) from error


# The rows each step consumes; locks are read fail-closed: a failed read raises, it is never an empty lock set.
STEP_ROWS = {
    "raid": ("link", "pair", "sources"), "drip": ("link",), "shuffle": ("locks",), "rotate": ("pair", "locked"),
}
# The same lookups the executors make where they consume a link or pair.
_READERS = {
    "link": RaidLinkService.get_link_for_playlist,
    "pair": PlaylistPairService.get_pair_for_playlist,
    "sources": read_sources,
    "locks": TrackLockService.get_locked_positions,
    "locked": TrackLockService.get_locked_uris,
}


# Set only inside fresh_reads: see _read_rows_fresh.
_FRESH_READS = ContextVar("live_review_fresh_reads", default=False)


@event.listens_for(Session, "do_orm_execute")
def _read_rows_fresh(execute_state):
    """Inside fresh_reads, a SELECT overwrites any row this request already loaded (populate_existing)."""
    if _FRESH_READS.get() and execute_state.is_select:
        execute_state.update_execution_options(populate_existing=True)


@contextmanager
def fresh_reads():
    """Every ORM SELECT in the block reads rows as they are now, never as this request cached them.

    Autoflush writes any pending change first, so none is discarded.
    """
    token = _FRESH_READS.set(True)
    try:
        yield
    finally:
        _FRESH_READS.reset(token)


def read_inputs(steps, user_id, target_id):
    """The fresh rows a run's steps consume, each row type read once.

    Each SELECT runs with populate_existing (fresh_reads), so a row this
    request loaded before the lock wait (a Drip's link, read to start the run)
    is compared as it is now, never as cached.
    """
    names = dict.fromkeys(name for step in steps for name in STEP_ROWS[step])
    with fresh_reads():
        return {name: _READERS[name](user_id, target_id) for name in names}


def build_part(step, inputs, *, extras=(), algorithm=None, params=None):
    """One step's fingerprinted inputs.

    Saved settings the step cannot run as saved raise InvalidSettings: a review
    refuses them, and a run reads them as a changed review.
    """
    if step == "raid":
        return {
            "link": link_ref(inputs["link"]),
            "pair": pair_ref(inputs["pair"]),
            "sources": [
                [source.id, source.source_type, source.source_playlist_id, source.search_query, source.raid_count]
                for source in sorted(inputs["sources"], key=lambda source: source.id)
            ],
            "extras": _converted(ordered, extras),
        }
    if step == "drip":
        link = inputs["link"]
        if link is None:
            return {"link": None, "count": None, "gate": None}
        params = _saved_params(params)
        return {
            "link": link_ref(link), "count": _converted(effective_drip_count, params, link),
            "gate": drip_gate(params, link),
        }
    params = _saved_params(params)
    if step == "shuffle":
        if algorithm not in ShuffleRegistry.get_available_algorithms():
            raise InvalidSettings(f"Unknown shuffle algorithm: {algorithm}")
        return {
            "algorithm": algorithm,
            "params": dict(params),
            "locks": sorted([position, uri] for position, uri in inputs["locks"].items()),
        }
    mode, count, cap, protect = _converted(rotation_settings, params)
    # The rotate executor runs only a known mode, and a swap needs a playlist size cap.
    if mode not in tuple(RotationMode) or cap is None:
        raise InvalidSettings("A rotation needs a known mode and a playlist size cap")
    return {
        "pair": pair_ref(inputs["pair"]),
        "mode": mode,
        "count": count,
        "cap": cap,
        "protect": protect,
        "locked": sorted(inputs["locked"]),
    }


def routine_parts(schedule, steps, rows=None):
    """A saved or transient routine's step parts, from fresh rows unless given.

    A routine's saved ``source_playlist_ids`` are the extras its raid receives;
    a reviewed Collect's transient routine carries only the request's extras.
    """
    if rows is None:
        rows = read_inputs(steps, schedule.user_id, schedule.target_playlist_id)
    return {
        step: build_part(
            step, rows, extras=schedule.source_playlist_ids or [],
            algorithm=schedule.algorithm_name, params=schedule.algorithm_params,
        )
        for step in steps
    }


def direct_parts(kind, rows, extras):
    """A Collect, Drip or Raid + Drip request's parts: its explicit extras and a forced Drip."""
    return {
        step: build_part(step, rows, extras=extras or [], params=DIRECT_DRIP_PARAMS)
        for step in DIRECT_STEPS[kind]
    }


# A saved value with no canonical JSON form (a non-finite number, say) raises InvalidSettings below.
def part_digest(step, part):
    return _converted(fingerprint, {"purpose": PART_PURPOSE, "step": step, "part": part})


def direct_scope(kind, target_id, parts):
    """The scope of an explicitly requested Collect, Drip or Raid + Drip."""
    if kind == "raid_and_drip":
        action = {step: part_digest(step, parts[step]) for step in ("raid", "drip")}
    else:
        action = parts[kind]
    return _converted(scope_fingerprint, kind, target_id, action)


def schedule_scope(schedule_id, job_type, target_id, parts):
    return _converted(
        scope_fingerprint,
        "schedule",
        target_id,
        {"schedule_id": schedule_id, "job_type": str(job_type), "target_id": target_id, "steps": parts},
    )
