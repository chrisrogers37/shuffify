"""Explicit-session, read-only capture and semantic revalidation of routine inputs."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import or_, select

from shuffify.models.db import PlaylistPair, RaidPlaylistLink, Schedule, TrackLock, UpstreamSource, User
from shuffify.services.live_review_bindings import fingerprint
from shuffify.services.live_review_scope import (
    InvalidSettings,
    ReviewChanged,
    direct_parts,
    direct_scope,
    link_ref,
    pair_ref,
    routine_parts,
    schedule_scope,
)

from .routine_schemas import DripNowCommand, RaidAndDripCommand, RaidNowCommand, ScheduleRunCommand

DIRECT_MODELS = {"raid": RaidNowCommand, "drip": DripNowCommand, "raid_and_drip": RaidAndDripCommand}


class CaptureNotFound(LookupError):
    """An authenticated caller cannot capture the missing or foreign owned row."""


class CaptureRejected(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class CaptureChanged(CaptureRejected):
    def __init__(self):
        super().__init__("configuration_changed", "The captured executor configuration changed before this step ran.")


def _owner_id(user_id):
    if type(user_id) is not int or user_id < 1:
        raise CaptureNotFound("User not found")


def _read(storage, model, *conditions):
    # Core mappings read the database without refreshing or mutating ORM objects
    # already present in the caller's identity map. The caller owns transaction
    # boundaries; capture neither commits nor flushes pending local changes.
    return storage.execute(select(model.__table__).where(*conditions).order_by(model.id)).mappings().all()


def _fields(row, names):
    return {name: deepcopy(row[name]) for name in names}


def _config(storage, user_id, target, now):
    links = _read(storage, RaidPlaylistLink, RaidPlaylistLink.user_id == user_id,
                  RaidPlaylistLink.target_playlist_id == target)
    pairs = _read(storage, PlaylistPair, PlaylistPair.user_id == user_id,
                  PlaylistPair.production_playlist_id == target)
    sources = _read(storage, UpstreamSource, UpstreamSource.user_id == user_id,
                    UpstreamSource.target_playlist_id == target)
    locks = _read(storage, TrackLock, TrackLock.user_id == user_id, TrackLock.spotify_playlist_id == target,
                  or_(TrackLock.expires_at.is_(None), TrackLock.expires_at > now))
    config = {
        "link": _fields(links[0], ("id", "raid_playlist_id", "raid_playlist_name", "drip_count", "drip_enabled"))
        if links else None,
        "pair": _fields(pairs[0], ("id", "archive_playlist_id", "archive_playlist_name")) if pairs else None,
        "sources": [_fields(row, ("id", "source_type", "source_playlist_id", "source_name", "search_query",
                                   "raid_count", "source_url")) for row in sources],
        "locks": [_fields(row, ("position", "track_uri")) for row in locks],
    }
    return config, links[0]["target_playlist_name"] if links else None


def _now(value):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Capture time must be timezone aware")
    return value


def capture_schedule(storage, user_id, schedule_id, *, reviewed=None, triggered_by="manual", now=None):
    """Capture one owned saved definition, including paused manual runs and raw JSON."""
    _owner_id(user_id)
    if type(schedule_id) is not int or schedule_id < 1:
        raise CaptureNotFound("Schedule not found")
    with storage.no_autoflush:
        rows = _read(storage, Schedule, Schedule.id == schedule_id, Schedule.user_id == user_id)
        if not rows:
            raise CaptureNotFound("Schedule not found")
        row = rows[0]
        definition = _fields(row, ("job_type", "target_playlist_id", "target_playlist_name", "algorithm_name",
                                    "algorithm_params", "source_playlist_ids"))
        definition["schedule_id"] = row["id"]
        config, _ = _config(storage, user_id, row["target_playlist_id"], _now(now))
    return ScheduleRunCommand(playlist_id=row["target_playlist_id"], definition=definition, config=config,
                              reviewed=reviewed, triggered_by=triggered_by)


def _direct(storage, user_id, playlist_id, kind, source_playlist_ids, reviewed, now, *, admission):
    _owner_id(user_id)
    if kind not in DIRECT_MODELS:
        raise CaptureRejected("invalid_action", "Choose a supported explicit executor action.")
    if not isinstance(playlist_id, str) or not playlist_id:
        raise CaptureRejected("invalid_target", "Choose a valid target playlist.")
    with storage.no_autoflush:
        if storage.scalar(select(User.id).where(User.id == user_id)) is None:
            raise CaptureNotFound("User not found")
        config, linked_name = _config(storage, user_id, playlist_id, _now(now))
    if admission and reviewed is None:
        if "raid" in kind and not (config["sources"] if source_playlist_ids is None else source_playlist_ids):
            raise CaptureRejected("no_sources", "No sources configured. Watch a playlist first.")
        # An unreviewed composite can first Collect into its local inbox with
        # no collection, then report its Drip failure. Do not reject that valid
        # earlier child at admission merely because the later child lacks a link.
        if kind == "drip" and config["link"] is None:
            raise CaptureRejected("link_required", "No raid playlist link found. Create a raid link first.")
    definition = {"schedule_id": None, "job_type": kind, "target_playlist_id": playlist_id,
                  "target_playlist_name": linked_name if "drip" in kind and linked_name else playlist_id,
                  "algorithm_name": None,
                  "algorithm_params": {"force": True} if "drip" in kind else None,
                  "source_playlist_ids": deepcopy(source_playlist_ids) if "raid" in kind else None}
    return DIRECT_MODELS[kind](playlist_id=playlist_id, definition=definition, config=config, reviewed=reviewed)


def capture_direct(storage, user_id, playlist_id, kind, *, source_playlist_ids=None, reviewed=None, now=None):
    """Capture explicit Collect/Drip intent; source IDs never grant write access."""
    return _direct(storage, user_id, playlist_id, kind, source_playlist_ids, reviewed, now, admission=True)


def _inputs(command):
    config = command.config
    locks = {lock.position: lock.track_uri for lock in config.locks}
    return {"link": config.link, "pair": config.pair, "sources": config.sources,
            "locks": locks, "locked": {lock.track_uri for lock in config.locks}}


def _steps(command, steps):
    selected = command.steps if steps is None else tuple(steps)
    if not selected or any(step not in command.steps for step in selected):
        raise ValueError("A configuration comparison requires declared executor steps")
    return selected


def _part(command, step):
    rows = _inputs(command)
    if isinstance(command, ScheduleRunCommand):
        definition = command.definition.model_dump()
        definition["id"] = definition.pop("schedule_id")
        return routine_parts(SimpleNamespace(**definition), (step,), rows)[step]
    return direct_parts(step, rows, command.definition.source_playlist_ids)[step]


def command_parts(command, steps=None):
    """Use precisely the existing review builders; invalid saved settings still raise."""
    return {step: _part(command, step) for step in _steps(command, steps)}


def command_scope(command):
    parts = command_parts(command)
    if isinstance(command, ScheduleRunCommand):
        definition = command.definition
        return schedule_scope(definition.schedule_id, definition.job_type, command.playlist_id, parts)
    return direct_scope(command.review_kind, command.playlist_id, parts)


def require_review(command):
    """Compare captured parts with the original reviewed scope before preparation."""
    if command.reviewed is None:
        return None
    try:
        parts = command_parts(command)
        actual = command_scope(command)
    except InvalidSettings:
        raise ReviewChanged() from None
    if actual != command.reviewed["reviewed_scope"]:
        raise ReviewChanged()
    return parts


def _raw_part(command, step):
    """Only an invalid step falls back to its raw, execution-relevant JSON."""
    definition, inputs = command.definition, _inputs(command)
    params = definition.algorithm_params or {}
    if step == "shuffle":
        return {"algorithm": definition.algorithm_name, "params": params,
                "locks": sorted([position, uri] for position, uri in inputs["locks"].items())}
    if step == "raid":
        part = direct_parts("raid", inputs, [])["raid"]
        return {**part, "extras": definition.source_playlist_ids}
    link = inputs["link"]
    if step == "rotate":
        settings = {key: params.get(key, default) for key, default in (
            ("rotation_mode", "swap"), ("rotation_count", 5), ("target_size", None), ("protect_count", 0),
        )} if isinstance(params, dict) else params
        return {"pair": pair_ref(inputs["pair"]), "settings": settings, "locked": sorted(inputs["locked"])}
    if not isinstance(params, dict):
        return {"link": link_ref(link), "params": params,
                "saved_count": link.drip_count if link else None, "saved_gate": link.drip_enabled if link else None}
    return {"link": link_ref(link), "count": params.get("drip_count", link.drip_count if link else None),
            "gate": "forced" if params.get("force") else "open" if link and link.drip_enabled else "closed"}


def configuration_digest(command, steps=None):
    parts = {}
    for step in _steps(command, steps):
        try:
            parts[step] = {"valid": True, "part": _part(command, step)}
        except InvalidSettings:
            parts[step] = {"valid": False, "raw": _raw_part(command, step)}
    return fingerprint({"purpose": "shuffify.operation.routine-config.v1", "kind": command.kind,
                        "playlist_id": command.playlist_id, "schedule_id": command.definition.schedule_id,
                        "job_type": command.definition.job_type, "parts": parts})


def assert_revalidated(captured, current, steps=None):
    """Reject changed step semantics or destinations; completed unrelated inputs may change."""
    selected = _steps(captured, steps)
    if (type(captured) is not type(current)
            or captured.definition.job_type != current.definition.job_type
            or captured.definition.schedule_id != current.definition.schedule_id
            or captured.targets_for(selected) != current.targets_for(selected)
            or configuration_digest(captured, selected) != configuration_digest(current, selected)):
        raise CaptureChanged()
    return current


def recapture(storage, user_id, captured, *, steps=None, now=None):
    """Fresh owned DB reads under worker locks, before the first/dependent preparation."""
    if isinstance(captured, ScheduleRunCommand):
        current = capture_schedule(storage, user_id, captured.definition.schedule_id, reviewed=captured.reviewed,
                                   triggered_by=captured.triggered_by, now=now)
    else:
        current = _direct(storage, user_id, captured.playlist_id, captured.review_kind,
                          captured.definition.source_playlist_ids, captured.reviewed, now, admission=False)
    return assert_revalidated(captured, current, steps)
