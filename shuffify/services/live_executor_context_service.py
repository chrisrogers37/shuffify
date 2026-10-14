"""Read-only review contexts for Workshop and routine executor actions.

Reviews a Collect, Drip, Raid + Drip or saved-routine run before it runs. A
context names every resource the run writes and each Spotify playlist it reads
for exclusions, with fresh authorized names, and binds the scope the run
compares again inside its target lock (``live_review_scope``). Nothing here
writes, resolves a discovery source, records, logs Activity, registers or
changes a routine, or touches session history.
"""

import logging

from sqlalchemy.exc import SQLAlchemyError

from shuffify.models.db import db
from shuffify.services.live_operation_service import STEP_BOOKKEEPING, STEP_TARGET_WRITES, DirectFailure
from shuffify.services.live_review_context import (
    context_rejected,
    failure_rejected,
    playlist_name,
    read_failure,
    resource_entry,
    review_context,
)
from shuffify.services.live_review_scope import (
    DIRECT_STEPS,
    JOB_STEPS,
    InvalidSettings,
    direct_parts,
    direct_scope,
    read_inputs,
    routine_parts,
    schedule_scope,
)
from shuffify.services.playlist_service import PlaylistService
from shuffify.services.scheduler_service import ScheduleNotFoundError, SchedulerService
from shuffify.shuffle_algorithms.registry import ShuffleRegistry

logger = logging.getLogger(__name__)

LABELS = {
    "raid": "Collect for review",
    "drip": "Add collected tracks now",
    "raid_and_drip": "Collect and add tracks now",
    "schedule": "Run routine now",
}
# What each step does to each role: (access, mutation type). Reads are exclusion reads.
_STEP_RESOURCES = {
    "raid": {
        "target": ("read", None), "collection": ("write", "track_membership"),
        "archive": ("read", None), "inbox": ("write", "inbox_state"),
    },
    "drip": {
        "target": ("write", STEP_TARGET_WRITES["drip"]), "collection": ("write", "track_membership"),
        "inbox": ("write", "inbox_state"),
    },
    "shuffle": {"target": ("write", STEP_TARGET_WRITES["shuffle"])},
    "rotate": {"target": ("write", STEP_TARGET_WRITES["rotate"]), "archive": ("write", "track_membership")},
}
_ROLES = ("target", "collection", "archive", "inbox")
_ROLE_NAMES = {"target": "playlist", "collection": "linked collection", "archive": "paired archive"}
_RANK = {None: 0, "track_order": 1, "track_membership": 2, "inbox_state": 2}
_INVALID = "This routine's saved settings cannot run as saved. Edit the routine, then review again."


def required_bookkeeping(steps):
    """The inbox bookkeeping each step reports; every executor run also records itself."""
    return [STEP_BOOKKEEPING[step] for step in steps if step in STEP_BOOKKEEPING] + ["execution_record"]


def _resources(steps, ids, names):
    """Each named role once: a write in any step outranks an exclusion read."""
    effects = {}
    for step in steps:
        for role, effect in _STEP_RESOURCES[step].items():
            known = effects.get(role)
            if known is None or effect[0] == "write" and (
                known[0] == "read" or _RANK[effect[1]] > _RANK[known[1]]
            ):
                effects[role] = effect
    return [
        resource_entry(ids[role], names[role], role, *effects[role])
        for role in _ROLES
        if role in effects and ids.get(role)
    ]


def _rows(steps, user_id, target_id, link_message):
    """The fresh rows ``steps`` read; a Drip step needs a linked collection."""
    try:
        rows = read_inputs(steps, user_id, target_id)
    except SQLAlchemyError:
        logger.warning("Executor review settings could not be read", exc_info=True)
        db.session.rollback()
        raise DirectFailure(
            502, "database_read_failed",
            "Saved settings could not be read. Nothing was changed; load the review again.", retryable=True,
        ) from None
    if "drip" in steps and rows.get("link") is None:
        raise DirectFailure(400, "link_required", link_message)
    return rows


def _configured(build):
    try:
        return build()
    except InvalidSettings:
        raise DirectFailure(409, "invalid_configuration", _INVALID) from None


def _require_authorization(user):
    # Executors authorize with the stored refresh token, never the session token.
    if not user.encrypted_refresh_token:
        raise DirectFailure(
            409, "authorization_unavailable",
            "Sign in again so Shuffify can run this for you. Nothing was changed.",
        )


def _source_label(source):
    """A saved name or query text for display; never an authorization fact."""
    return source.source_name or source.search_query or source.source_playlist_id or "Discovery source"


class LiveExecutorContextService:
    """Resolve one executor action review without writing, running or recording."""

    def __init__(self, api):
        self.playlists = PlaylistService(api)

    def direct(self, kind, target_id, user, extras):
        """(payload, status) reviewing a Collect, Drip or Raid + Drip of ``target_id``."""
        try:
            return {"success": True, "context": self._direct(kind, target_id, user, extras)}, 200
        except DirectFailure as failure:
            return failure_rejected(kind, failure)

    def schedule(self, schedule_id, user):
        """(payload, status) reviewing one run of an owned saved routine, now."""
        try:
            schedule = SchedulerService.get_schedule(schedule_id, user.id)
        except ScheduleNotFoundError:
            return context_rejected("schedule_not_found", 404, "Schedule not found.", kind="schedule")
        except Exception:
            logger.warning("Routine review lookup unavailable for %s", schedule_id, exc_info=True)
            db.session.rollback()
            return context_rejected(
                "schedule_unavailable", 503,
                "This routine could not be read. Nothing was changed; load the review again.",
                retryable=True, kind="schedule",
            )
        try:
            return {"success": True, "context": self._schedule(schedule, user)}, 200
        except DirectFailure as failure:
            return failure_rejected("schedule", failure)

    def _direct(self, kind, target_id, user, extras):
        steps = DIRECT_STEPS[kind]
        _require_authorization(user)
        rows = _rows(steps, user.id, target_id, "Link a collection playlist before adding collected tracks.")
        link, sources = rows.get("link"), rows.get("sources", [])
        if "raid" in steps and not sources and not extras:
            raise DirectFailure(400, "no_sources", "Watch a discovery source before collecting. Nothing was changed.")
        parts = _configured(lambda: direct_parts(kind, rows, extras))
        # Saved values that no longer fingerprint are a configuration problem, found before any Spotify read.
        scope = _configured(lambda: direct_scope(kind, target_id, parts))
        ids, names = self._playlists(user, target_id, rows)
        collection = {"id": ids["collection"], "name": names["collection"]} if link else None
        current, action = {}, {}
        if "raid" in steps:
            current.update(source_count=len(sources), extra_source_ids=list(extras or []), collection=collection)
            action.update(
                sources=[{"id": source.id, "label": _source_label(source)} for source in sources],
                extras=list(extras or []),
            )
        if "drip" in steps:
            # A direct Drip runs past the saved Drip setting; the review says so.
            current.update(collection=collection, drip_enabled=bool(link.drip_enabled))
            action.update(count=parts["drip"]["count"])
        return review_context(
            kind, LABELS[kind], target_id, names["target"],
            resources=_resources(steps, ids, names), steps=steps, bookkeeping=required_bookkeeping(steps),
            scope=scope, current=current, action=action,
        )

    def _schedule(self, schedule, user):
        _require_authorization(user)
        steps = JOB_STEPS.get(schedule.job_type)
        if steps is None:
            raise DirectFailure(409, "invalid_configuration", _INVALID)
        target_id = schedule.target_playlist_id
        rows = _rows(steps, user.id, target_id, "Link a collection playlist before running this routine.")
        if "rotate" in steps and rows.get("pair") is None:
            raise DirectFailure(409, "invalid_configuration", "Pair an archive playlist before running this rotation.")
        # The step parts own every saved-settings check (algorithm, parameters, rotation).
        parts = _configured(lambda: routine_parts(schedule, steps, rows))
        scope = _configured(lambda: schedule_scope(schedule.id, schedule.job_type, target_id, parts))
        ids, names = self._playlists(user, target_id, rows)
        job_type = str(schedule.job_type)
        algorithm = schedule.algorithm_name if "shuffle" in steps else None
        rotate = parts.get("rotate")
        action = {
            "job_type": job_type,
            "algorithm": {"id": algorithm, "name": ShuffleRegistry.get_algorithm(algorithm)().name}
            if algorithm else None,
            "params": dict(schedule.algorithm_params or {}) if algorithm else {},
            "rotation": {key: rotate[key] for key in ("mode", "count", "cap", "protect")} if rotate else None,
            "drip": {key: parts["drip"][key] for key in ("count", "gate")} if "drip" in parts else None,
        }
        # Running now leaves a paused routine paused and its timing unchanged.
        current = {
            "is_enabled": bool(schedule.is_enabled),
            "source_count": len(rows["sources"]) if "raid" in steps else None,
        }
        return review_context(
            "schedule", LABELS["schedule"], target_id, names["target"],
            schedule={"id": schedule.id, "job_type": job_type},
            resources=_resources(steps, ids, names), steps=steps, bookkeeping=required_bookkeeping(steps),
            scope=scope, current=current, action=action,
        )

    def _playlists(self, user, target_id, rows):
        """(ids, fresh names) of the playlists the rows name; neither collection nor archive may be the target."""
        link, pair = rows.get("link"), rows.get("pair")
        ids = {
            "target": target_id, "inbox": target_id,
            "collection": link.raid_playlist_id if link else None,
            "archive": pair.archive_playlist_id if pair else None,
        }
        if target_id in (ids["collection"], ids["archive"]):
            raise DirectFailure(
                409, "invalid_configuration",
                "A linked collection or paired archive must be a different playlist. Nothing was changed.",
            )
        return ids, self._names(user, ids)

    def _names(self, user, ids):
        """Fresh authorized names; an unreadable or non-editable playlist blocks the review."""
        names, seen = {}, {}
        for role in ("target", "collection", "archive"):
            playlist_id = ids.get(role)
            if not playlist_id:
                continue
            if playlist_id not in seen:
                seen[playlist_id] = self._name(playlist_id, user, role)
            names[role] = seen[playlist_id]
        names["inbox"] = names["target"]
        return names

    def _name(self, playlist_id, user, role):
        try:
            metadata = self.playlists.validate_user_can_edit(playlist_id, user.spotify_id, skip_cache=True)
        except Exception as error:
            noun = _ROLE_NAMES[role]
            failure = read_failure(error, noun)
            if role != "target" and failure.status in (403, 404):
                failure.message = f"The {noun} is not available to edit. Nothing was changed."
            raise failure from None
        return playlist_name(metadata.get("name") if isinstance(metadata, dict) else None)
