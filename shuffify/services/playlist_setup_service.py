"""Shared playlist setup: sources, collection and archive, saved explicitly.

Reading, reviewing and creating a Spotify playlist never change the shared
configuration. Save applies reviewed changes under the target's playlist lock
in one transaction, committed once. It never calls APScheduler or changes a
routine's enabled state, timing or parameters: a job reads its row when it
runs, so the database change is the whole effect, and re-registering a job
would restart its interval. Creating a playlist never links, pairs or
schedules; the client offers it as a staged change that still needs Save.

The configuration revision covers definitions only. Names, timestamps and
``last_*`` run tracking are excluded, so a routine run between review and Save
is not a Conflict. ``state_digest`` is an ID-free digest that a client compares
with a review's ``expected_state_digest`` after losing a Save's response.
"""

import copy
import logging
from dataclasses import dataclass
from functools import partial

from shuffify.enums import ActivityType
from shuffify.models.db import PlaylistPair, RaidPlaylistLink, Schedule, db
from shuffify.schemas.playlist_setup_requests import DRIP_COUNT_RANGE, RAID_COUNT_RANGE, is_playlist_id
from shuffify.services import signed_digest
from shuffify.services.activity_log_service import ActivityLogService
from shuffify.services.base import ensure_private, safe_commit
from shuffify.services.live_review_bindings import fingerprint
from shuffify.services.live_review_scope import (
    JOB_STEPS,
    STEP_ROWS,
    drip_forced,
    drip_override,
    fresh_reads,
    ordered,
    read_inputs,
)
from shuffify.services.playlist_lock import playlist_configuration_lock, playlist_lock
from shuffify.services.playlist_pair_service import PlaylistPairService
from shuffify.services.playlist_service import (
    PlaylistAccessError,
    PlaylistError,
    PlaylistNotFoundError,
    PlaylistService,
)
from shuffify.services.raid_link_service import RaidLinkService
from shuffify.services.schedule_utils import operation_label, timing_text
from shuffify.services.upstream_source_service import MAX_SOURCES_PER_TARGET, UpstreamSourceService
from shuffify.spotify.api import playlist_item_count
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyRateLimitError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)

logger = logging.getLogger(__name__)

ACK_PURPOSE = "shuffify.setup.ack.v1"
CONFIGURATION_PURPOSE = "shuffify.setup.configuration.v1"
CHANGES_PURPOSE = "shuffify.setup.changes.v1"
SECTION_PURPOSE = "shuffify.setup.section.v1"
STATE_PURPOSE = "shuffify.setup.state.v1"
ACK_MAX_AGE_S = 30 * 60
LOCK_WAIT_S = 2.0
SECTIONS = ("sources", "collection", "archive", "routines")
LIMITS = {
    "max_sources": MAX_SOURCES_PER_TARGET, "raid_count": list(RAID_COUNT_RANGE), "drip_count": list(DRIP_COUNT_RANGE),
}
# What a row starts with when a change creates it, as the legacy creates do.
NEW_SOURCE_RAID_COUNT = 5
NEW_SETTINGS = {"collection": {"drip_enabled": False, "drip_count": 3}, "archive": {"auto_archive_on_remove": True}}

# The order changes apply in: removals, unlinks, counts, links, then the
# collection and archive settings (a set_drip applies after a link in the same
# list), and additions last (removals free their slots first).
PHASES = {
    "remove_source": 0,
    "remove_routine_source": 0,
    "unlink_collection": 1,
    "unpair_archive": 1,
    "set_source_count": 2,
    "link_collection": 3,
    "replace_collection": 3,
    "pair_archive": 3,
    "replace_archive": 3,
    "set_drip": 4,
    "set_auto_archive": 4,
    "add_playlist_source": 5,
    "add_search_source": 5,
}

BUSY = "A routine or save is running on this playlist. Try again when it finishes."
CONFIGURATION_CHANGED = "These settings changed since your review. Review the changes again."
ACK_MESSAGES = {
    "expired": "This review expired. Review the changes again.",
    "mismatch": "These are not the changes you reviewed. Review them again.",
    "malformed": "This review could not be read. Review the changes again.",
}
INVALID_CHANGES = "Some changes need attention. Nothing was saved."
SAVE_FAILED = "Could not confirm settings save."
TARGET_NOT_FOUND = "That playlist doesn't exist or was deleted."
TARGET_NOT_EDITABLE = "You can only set up your own or collaborative playlists."
SOURCE_LIMIT = f"A playlist can have at most {MAX_SOURCES_PER_TARGET} sources"
NOT_EDITABLE = "You can only use your own or collaborative playlists"
PLAYLIST_NOT_FOUND = "Playlist not found"
SAME_AS_TARGET = "This is the playlist being set up"
DISTINCT = "The collection and archive must be different playlists"
NO_ROUTINES = "No existing routines are affected. This does not schedule anything."
AUTO_ARCHIVE_NOTE = "Auto-archive is used only by Workshop drafts; no routine reads it."
CREATION_UNKNOWN = (
    "We could not confirm whether the playlist was created. "
    "Check your Spotify playlists before creating another."
)
MISSING = {"collection": "No collection is linked", "archive": "No archive is paired"}
EXISTS = {
    "collection": "A collection is already linked. Replace it instead.",
    "archive": "An archive is already paired. Replace it instead.",
}
ALREADY = {
    "collection": ("already_linked", "This playlist already has a collection. Replace it in its settings instead."),
    "archive": ("already_paired", "This playlist already has an archive. Replace it in its settings instead."),
}
DESCRIPTIONS = {
    "collection": "Collected discoveries, created in Shuffify setup",
    "archive": "Archived tracks, created in Shuffify setup",
}
# The row each setup section is, as live_review_scope.STEP_ROWS names what a step reads.
ROWS = {"sources": "sources", "collection": "link", "archive": "pair"}
# drip_override's answer here for a routine that saves no Drip count: it adds the collection's.
_SHARED_COUNT = object()
# Per step that reads the asset's row: what linking, replacing or unlinking the asset does to a routine.
ASSET_EFFECTS = {
    "collection": {
        "raid": {
            "link": "will add discoveries to {new}",
            "replace": "will add discoveries to {new} instead of {old}",
            "unlink": "will stop adding discoveries to {old}",
        },
        "drip": {
            "link": "will add tracks from {new}",
            "replace": "will add tracks from {new} instead of {old}",
            "unlink": "will skip adding: no collection",
        },
    },
    "archive": {
        "rotate": {
            "link": "will rotate tracks into {new}",
            "replace": "will rotate tracks into {new} instead of {old}",
            "unlink": "will have no archive to rotate into",
        },
        "raid": {
            "link": "will skip discoveries already in {new}",
            "replace": "will skip discoveries already in {new} instead of {old}",
            "unlink": "will stop skipping discoveries already in {old}",
        },
    },
}
ASSET_OPS = {
    "link_collection": ("collection", "link"),
    "replace_collection": ("collection", "replace"),
    "unlink_collection": ("collection", "unlink"),
    "pair_archive": ("archive", "link"),
    "replace_archive": ("archive", "replace"),
    "unpair_archive": ("archive", "unlink"),
}
# Per asset: the service writes that create, update and delete its row, and the keywords they take
# for its playlist ID, its playlist's name and the target's name.
ASSET_ROWS = {
    "collection": {
        "create": RaidLinkService.create_link,
        "update": RaidLinkService.update_link,
        "delete": RaidLinkService.delete_link,
        "keys": ("raid_playlist_id", "raid_playlist_name", "target_playlist_name"),
    },
    "archive": {
        "create": PlaylistPairService.create_pair,
        "update": PlaylistPairService.update_pair,
        "delete": PlaylistPairService.delete_pair,
        "keys": ("archive_playlist_id", "archive_playlist_name", "production_playlist_name"),
    },
}


class PlaylistSetupError(Exception):
    """A setup Save that could not be committed."""


# --- Reading -------------------------------------------------------------


@dataclass(frozen=True)
class SetupState:
    """One fresh read of a user's shared configuration for a target and every routine on it."""

    user_id: int
    target_id: str
    sources: tuple
    link: RaidPlaylistLink | None
    pair: PlaylistPair | None
    schedules: tuple


def read_state(user_id, target_id):
    """The rows for (user, target), read fresh: no Spotify call and no write.

    The link, pair and sources (ordered by id) are the rows a Collect run reads
    (live_review_scope.read_inputs). Routines are every job type on the target,
    as ``SchedulerService.get_schedules_for_playlist`` selects them, ordered by id.
    """
    rows = read_inputs(("raid",), user_id, target_id)
    with fresh_reads():
        schedules = Schedule.query.filter_by(user_id=user_id, target_playlist_id=target_id).order_by(Schedule.id).all()
    return SetupState(user_id, target_id, tuple(rows["sources"]), rows["link"], rows["pair"], tuple(schedules))


def _config(state):
    """The state's plain values: what the digests cover and a review simulates."""
    link, pair = state.link, state.pair
    return {
        "sources": [
            {
                "id": source.id,
                "source_type": source.source_type,
                "source_playlist_id": source.source_playlist_id,
                "search_query": source.search_query,
                "raid_count": source.raid_count,
                "name": source.source_name,
            }
            for source in state.sources
        ],
        "collection": None if link is None else {
            "id": link.id,
            "playlist_id": link.raid_playlist_id,
            "name": link.raid_playlist_name,
            "drip_enabled": link.drip_enabled,
            "drip_count": link.drip_count,
        },
        "archive": None if pair is None else {
            "id": pair.id,
            "playlist_id": pair.archive_playlist_id,
            "name": pair.archive_playlist_name,
            "auto_archive_on_remove": pair.auto_archive_on_remove,
        },
        "routines": [
            {
                "id": schedule.id,
                "job_type": schedule.job_type,
                "schedule_type": schedule.schedule_type,
                "schedule_value": schedule.schedule_value,
                "timezone": schedule.timezone,
                "is_enabled": schedule.is_enabled,
                "source_playlist_ids": copy.deepcopy(schedule.source_playlist_ids),
                "algorithm_name": schedule.algorithm_name,
                "algorithm_params": copy.deepcopy(schedule.algorithm_params),
            }
            for schedule in state.schedules
        ],
    }


def _revision_sections(config):
    """What the revision covers, per section; rows are in read_state's id order."""
    collection, archive = config["collection"], config["archive"]
    return {
        "sources": [
            [s["id"], s["source_type"], s["source_playlist_id"], s["search_query"], s["raid_count"]]
            for s in config["sources"]
        ],
        "collection": None if collection is None else [
            collection["id"], collection["playlist_id"], collection["drip_enabled"], collection["drip_count"]
        ],
        "archive": None if archive is None else [
            archive["id"], archive["playlist_id"], archive["auto_archive_on_remove"]
        ],
        "routines": [
            [
                r["id"], r["job_type"], r["schedule_type"], r["schedule_value"], r["timezone"], r["is_enabled"],
                r["source_playlist_ids"], r["algorithm_name"], r["algorithm_params"],
            ]
            for r in config["routines"]
        ],
    }


def _revision(user_id, target_id, sections):
    return fingerprint({
        "purpose": CONFIGURATION_PURPOSE,
        "user_id": user_id,
        "target_id": target_id,
        "sections": sections,
    })


def _section_digests(sections):
    return {
        name: fingerprint({"purpose": SECTION_PURPOSE, "section": name, "value": sections[name]})
        for name in SECTIONS
    }


def _state_digest(user_id, target_id, config):
    collection, archive = config["collection"], config["archive"]
    return fingerprint({
        "purpose": STATE_PURPOSE,
        "user_id": user_id,
        "target_id": target_id,
        "sources": ordered(
            [s["source_type"], s["source_playlist_id"], s["search_query"], s["raid_count"]] for s in config["sources"]
        ),
        "collection": None if collection is None else [
            collection["playlist_id"], collection["drip_enabled"], collection["drip_count"]
        ],
        "archive": None if archive is None else [archive["playlist_id"], archive["auto_archive_on_remove"]],
        "routines": [
            [
                r["id"], r["job_type"], r["is_enabled"], r["schedule_type"],
                r["schedule_value"], r["timezone"], ordered(_ids(r)),
            ]
            for r in config["routines"]
        ],
    })


def configuration_revision(state):
    """Digest of the target's shared configuration and routine definitions (no names, timestamps or run tracking)."""
    return _revision(state.user_id, state.target_id, _revision_sections(_config(state)))


def section_digests(state):
    """Per-section digests of the revision's content, so a Conflict can name what changed."""
    return _section_digests(_revision_sections(_config(state)))


def state_digest(state):
    """ID-free digest of the configuration, comparable with a review's expected_state_digest."""
    return _state_digest(state.user_id, state.target_id, _config(state))


# --- Routines and wording ---------------------------------------------------


def _steps(routine):
    return JOB_STEPS.get(routine["job_type"], ())


def _ids(routine):
    ids = routine["source_playlist_ids"]
    return list(ids) if isinstance(ids, list) else []


def _params(routine):
    params = routine["algorithm_params"]
    return params if isinstance(params, dict) else {}


def _reads(routine, section):
    """Whether a step of the routine reads the section's row when it runs (live_review_scope.STEP_ROWS)."""
    return any(ROWS[section] in STEP_ROWS[step] for step in _steps(routine))


def _own_drip_count(routine):
    """A drip routine's own Drip count, or _SHARED_COUNT when it saves none and adds the collection's.

    Any saved drip_count is its own (drip_override). It is shown as its runs add
    it (effective_drip_count), or as saved when no run can use it.
    """
    own = drip_override(_params(routine), _SHARED_COUNT) if "drip" in _steps(routine) else _SHARED_COUNT
    try:
        return max(1, int(own))
    except (TypeError, ValueError):
        return own


def _labels(routines):
    labels = [f"{operation_label(r['job_type'])} (#{r['id']})" for r in routines]
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} and {labels[-1]}"


def _name(value, fallback):
    """A stored or Spotify display name, or the fallback when it has none."""
    return value[:255] if isinstance(value, str) and value.strip() else fallback


def _asset(row):
    return {"playlist_id": row["playlist_id"], "name": _name(row["name"], row["playlist_id"])}


def _source_name(source):
    return _name(source["name"], source["search_query"] or source["source_playlist_id"] or "")


# --- Context ------------------------------------------------------------------


def _routine_entry(routine):
    own = _own_drip_count(routine)
    return {
        "id": routine["id"],
        "job_type": routine["job_type"],
        "operation": operation_label(routine["job_type"]),
        "timing_text": timing_text(routine["schedule_type"], routine["schedule_value"], routine.get("timezone")),
        "is_enabled": bool(routine["is_enabled"]),
        "uses": {
            "sources": _reads(routine, "sources"),
            "collection": _reads(routine, "collection"),
            "drip": "drip" in _steps(routine),
            "archive": _reads(routine, "archive"),
        },
        "drip_count_override": own if type(own) is int else None,
    }


def _routine_only_sources(config):
    """Playlist IDs a raid-family routine lists with no saved source: raided today as ephemeral sources."""
    saved = {s["source_playlist_id"] for s in config["sources"] if s["source_playlist_id"]}
    found = {}
    for routine in config["routines"]:
        if not _reads(routine, "sources"):
            continue
        for playlist_id in _ids(routine):
            if isinstance(playlist_id, str) and playlist_id and playlist_id not in saved:
                routine_ids = found.setdefault(playlist_id, [])
                if routine["id"] not in routine_ids:
                    routine_ids.append(routine["id"])
    return [{"playlist_id": playlist_id, "routine_ids": ids} for playlist_id, ids in sorted(found.items())]


def _context(state, target_name):
    """The setup context of a fresh state (``get_context``)."""
    config = _config(state)
    collection, archive = config["collection"], config["archive"]
    sections = _revision_sections(config)
    return {
        "version": 1,
        "target": {"id": state.target_id, "name": target_name},
        "sources": [
            {
                "id": s["id"],
                "type": s["source_type"],
                "playlist_id": s["source_playlist_id"],
                "search_query": s["search_query"],
                "name": _source_name(s),
                "raid_count": s["raid_count"],
            }
            for s in config["sources"]
        ],
        "routine_only_sources": _routine_only_sources(config),
        "collection": None if collection is None else {
            **_asset(collection),
            "drip_enabled": collection["drip_enabled"],
            "drip_count": collection["drip_count"],
        },
        "archive": None if archive is None else {
            **_asset(archive),
            "auto_archive_on_remove": archive["auto_archive_on_remove"],
        },
        "routines": [_routine_entry(r) for r in config["routines"]],
        "limits": copy.deepcopy(LIMITS),
        "configuration_revision": _revision(state.user_id, state.target_id, sections),
        "section_digests": _section_digests(sections),
        "state_digest": _state_digest(state.user_id, state.target_id, config),
    }


# --- Spotify reads and refusals ---------------------------------------------


def refusal(error_code, status, message, headers=None, **extra):
    """A refused setup request: ``(payload, status)``, or ``(payload, status, headers)`` when headers are given."""
    payload = {"success": False, "error_code": error_code, "message": message, **extra}
    return (payload, status, headers) if headers else (payload, status)


def _causes(error):
    chain = []
    while error is not None and error not in chain:
        chain.append(error)
        error = error.__cause__ or error.__context__
    return chain


def _rate_limited(error, message, **extra):
    retry_after = error.retry_after if type(error.retry_after) is int and error.retry_after > 0 else None
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return refusal("rate_limited", 429, message, headers, retryable=True, retry_after=retry_after, **extra)


def _read_refusal(error):
    """A Spotify read that failed for a reason other than the playlist itself; nothing was changed."""
    chain = _causes(error)
    if any(isinstance(cause, SpotifyAuthError) for cause in chain):
        return refusal("authentication_required", 401, "Please log in again. Nothing was changed.")
    limited = next((cause for cause in chain if isinstance(cause, SpotifyRateLimitError)), None)
    if limited is not None:
        return _rate_limited(limited, "Spotify is busy. Nothing was changed; try again shortly.")
    logger.warning("Playlist setup could not read Spotify: %s", error)
    return refusal("spotify_unavailable", 502, "Spotify could not be reached. Nothing was changed.", retryable=True)


def _target_refusal(error):
    if isinstance(error, PlaylistNotFoundError):
        return refusal("playlist_not_found", 404, TARGET_NOT_FOUND)
    if isinstance(error, PlaylistAccessError):
        return refusal("access_denied", 403, TARGET_NOT_EDITABLE)
    return _read_refusal(error)


def _editable_name(playlists, user, playlist_id, *, missing, not_editable):
    """The playlist's fresh name when the user can edit it, from one lean read.

    Raises PlaylistNotFoundError(missing) when Spotify does not have it and
    PlaylistAccessError(not_editable) when the user cannot edit it or Spotify
    refuses it. Any other failure propagates.
    """
    try:
        metadata = playlists.validate_user_can_edit(playlist_id, user.spotify_id, lean=True)
    except PlaylistAccessError as error:
        if isinstance(error.__cause__, SpotifyNotFoundError):
            raise PlaylistNotFoundError(missing) from error
        raise PlaylistAccessError(not_editable) from error
    return _name(metadata.get("name") if isinstance(metadata, dict) else None, playlist_id)


def _target_name(api, user, target_id):
    """The target's fresh name. Raises PlaylistNotFoundError or PlaylistAccessError unless the user can edit it."""
    if not is_playlist_id(target_id):
        raise PlaylistNotFoundError(TARGET_NOT_FOUND)
    return _editable_name(
        PlaylistService(api), user, target_id, missing=TARGET_NOT_FOUND, not_editable=TARGET_NOT_EDITABLE
    )


# --- Review: normalize, validate and simulate ---------------------------------


def _normalized(changes):
    """(request index, change) in apply order; changes in one phase keep their request order."""
    return sorted(enumerate(changes), key=lambda item: PHASES[item[1].op])


def _change_hash(target_id, changes):
    """Digest of the changes' validated fields in apply order (display names are not part of it)."""
    return fingerprint({
        "purpose": CHANGES_PURPOSE,
        "target_id": target_id,
        "changes": [change.model_dump(mode="json") for _, change in changes],
    })


def _ack_payload(user, target_id, revision, change_hash):
    return {"user_id": user.id, "target_id": target_id, "configuration_revision": revision, "change_hash": change_hash}


class _Review:
    """One change list checked and simulated against fresh state. Spotify is read; nothing is written.

    ``entries`` are the normalized changes with resolved names and summaries,
    ``writes`` the service writes Save runs in order (each in its staging
    form), ``work`` the configuration the changes produce and ``errors`` every
    ``{index, field, message}`` failure.
    """

    def __init__(self, api, user, target_id, target_name, state):
        self.playlists = PlaylistService(api)
        self.user = user
        self.target_id = target_id
        self.target_name = target_name
        self.before = _config(state)
        self.work = copy.deepcopy(self.before)
        self.entries = []
        self.writes = []
        self.errors = []
        self.set_by = {}
        self._editable = {}
        self._metadata = {}

    def run(self, changes):
        for index, change in changes:
            if change.op in ASSET_OPS:
                self._asset(index, change, *ASSET_OPS[change.op])
            else:
                getattr(self, f"_{change.op}")(index, change)
        self._check_distinct()
        self.errors.sort(key=lambda error: (error["index"], error["field"]))
        return self

    # Helpers

    def _error(self, index, field, message):
        self.errors.append({"index": index, "field": field, "message": message})

    def _entry(self, index, change, summary, **fields):
        self.entries.append({"index": index, "op": change.op, "subject": change.subject, "summary": summary, **fields})

    def _stage(self, write, *args, **kwargs):
        """Queue a service write, in its staging form, for Save to run in order."""
        self.writes.append(partial(write, *args, commit=False, **kwargs))

    def _saved_source(self, source_id):
        return next((s for s in self.work["sources"] if s["id"] == source_id), None)

    def _is_source(self, playlist_id):
        return any(s["source_playlist_id"] == playlist_id for s in self.work["sources"])

    def _raid_routines(self):
        return [routine for routine in self.work["routines"] if _reads(routine, "sources")]

    def _prune(self, playlist_id):
        """Drop the ID from every raid-family routine, unless a saved source still has it."""
        if self._is_source(playlist_id):
            return
        for routine in self._raid_routines():
            ids = _ids(routine)
            if playlist_id in ids:
                routine["source_playlist_ids"] = [i for i in ids if i != playlist_id]

    def _append(self, playlist_id):
        for routine in self._raid_routines():
            ids = _ids(routine)
            if playlist_id not in ids:
                routine["source_playlist_ids"] = ids + [playlist_id]

    def _candidate(self, playlist_id):
        """(fresh name, None) when the user can edit the playlist, else (None, the field message); read once."""
        if playlist_id not in self._editable:
            try:
                name = _editable_name(
                    self.playlists, self.user, playlist_id, missing=PLAYLIST_NOT_FOUND, not_editable=NOT_EDITABLE
                )
            except (PlaylistNotFoundError, PlaylistAccessError) as error:
                self._editable[playlist_id] = (None, str(error))
            else:
                self._editable[playlist_id] = (name, None)
        return self._editable[playlist_id]

    def _source_metadata(self, playlist_id):
        """Spotify's metadata for a playlist to add, or None when it is not found or not readable."""
        if playlist_id not in self._metadata:
            try:
                self._metadata[playlist_id] = self.playlists.get_playlist_metadata(playlist_id)
            except PlaylistNotFoundError:
                self._metadata[playlist_id] = None
            except PlaylistError as error:
                if not any(isinstance(cause, SpotifyForbiddenError) for cause in _causes(error)):
                    raise
                self._metadata[playlist_id] = None
        return self._metadata[playlist_id]

    # Sources

    def _remove_source(self, index, change):
        source = self._saved_source(change.source_id)
        if source is None:
            return self._error(index, "source_id", "Source not found")
        self.work["sources"].remove(source)
        self._stage(UpstreamSourceService.delete_source, change.source_id, self.user.spotify_id, self.target_id)
        if source["source_playlist_id"]:
            self._prune(source["source_playlist_id"])
        name = _source_name(source)
        self._entry(index, change, f"Remove source: {name}", source_id=change.source_id, name=name)

    def _remove_routine_source(self, index, change):
        playlist_id = change.playlist_id
        if self._is_source(playlist_id):
            return self._error(index, "playlist_id", "This is a saved source. Remove it from the sources instead.")
        routine_ids = [routine["id"] for routine in self._raid_routines() if playlist_id in _ids(routine)]
        if not routine_ids:
            return self._error(index, "playlist_id", "Not used by any routine")
        self._prune(playlist_id)
        self._entry(
            index, change, f"Stop routines collecting from {playlist_id}",
            playlist_id=playlist_id, routine_ids=routine_ids,
        )

    def _set_source_count(self, index, change):
        source = self._saved_source(change.source_id)
        if source is None:
            return self._error(index, "source_id", "Source not found")
        previous = source["raid_count"]
        source["raid_count"] = change.raid_count
        self._stage(
            UpstreamSourceService.update_raid_count, self.user.id, self.target_id, change.source_id, change.raid_count
        )
        name = _source_name(source)
        self._entry(
            index, change, f"{name}: collect {change.raid_count} per run (was {previous})",
            source_id=change.source_id, name=name, raid_count=change.raid_count, previous_raid_count=previous,
        )

    def _add_playlist_source(self, index, change):
        playlist_id = change.playlist_id
        if playlist_id == self.target_id:
            return self._error(index, "playlist_id", SAME_AS_TARGET)
        if self._is_source(playlist_id):
            return self._error(index, "playlist_id", "Already a source")
        if len(self.work["sources"]) >= MAX_SOURCES_PER_TARGET:
            return self._error(index, "playlist_id", SOURCE_LIMIT)
        metadata = self._source_metadata(playlist_id)
        if metadata is None:
            return self._error(index, "playlist_id", PLAYLIST_NOT_FOUND)
        source_type = "own" if metadata.get("owner_id") == self.user.spotify_id else "external"
        name = _name(metadata.get("name"), playlist_id)
        self.work["sources"].append({
            "id": None, "source_type": source_type, "source_playlist_id": playlist_id,
            "search_query": None, "raid_count": NEW_SOURCE_RAID_COUNT, "name": name,
        })
        self._append(playlist_id)
        self._stage(
            UpstreamSourceService.add_source, self.user.spotify_id, self.target_id, playlist_id,
            source_type=source_type, source_name=name,
        )
        self._entry(
            index, change, f"Add source: {name} (playlist)",
            playlist_id=playlist_id, name=name, source_type=source_type,
        )

    def _add_search_source(self, index, change):
        query = change.search_query
        if any(s["search_query"] == query for s in self.work["sources"]):
            return self._error(index, "search_query", "Already a source")
        if len(self.work["sources"]) >= MAX_SOURCES_PER_TARGET:
            return self._error(index, "search_query", SOURCE_LIMIT)
        name = _name(change.source_name, query[:255])
        self.work["sources"].append({
            "id": None, "source_type": "search_query", "source_playlist_id": None,
            "search_query": query, "raid_count": NEW_SOURCE_RAID_COUNT, "name": name,
        })
        self._stage(
            UpstreamSourceService.add_search_source, self.user.spotify_id, self.target_id, query, source_name=name
        )
        self._entry(index, change, f"Add source: “{query}” (search)", search_query=query, name=name)

    # Collection and archive

    def _asset(self, index, change, role, kind):
        """Link, replace or unlink the collection or archive (ASSET_OPS)."""
        current, row = self.work[role], ASSET_ROWS[role]
        if (current is None) != (kind == "link"):
            return self._error(index, "op", EXISTS[role] if kind == "link" else MISSING[role])
        noun = role.capitalize()
        previous = None if current is None else _asset(current)
        if kind == "unlink":
            self.work[role] = None
            self._stage(row["delete"], self.user.id, self.target_id)
            return self._entry(index, change, f"{noun}: unlink {previous['name']}", previous=previous)
        playlist_id = change.playlist_id
        if playlist_id == self.target_id:
            return self._error(index, "playlist_id", SAME_AS_TARGET)
        name, problem = self._candidate(playlist_id)
        if problem:
            return self._error(index, "playlist_id", problem)
        self.set_by[role] = index
        id_key, name_key, target_key = row["keys"]
        if kind == "replace":
            # In place, keeping the row's settings: a delete and insert of one key in a flush fails.
            current["playlist_id"], current["name"] = playlist_id, name
            self._stage(row["update"], self.user.id, self.target_id, **{id_key: playlist_id, name_key: name})
            summary = f"{noun}: replace {previous['name']} with {name}"
            return self._entry(index, change, summary, playlist_id=playlist_id, name=name, previous=previous)
        self.work[role] = {"id": None, "playlist_id": playlist_id, "name": name, **NEW_SETTINGS[role]}
        self._stage(
            row["create"], self.user.id, self.target_id,
            **{id_key: playlist_id, name_key: name, target_key: self.target_name}, **NEW_SETTINGS[role],
        )
        self._entry(index, change, f"{noun}: use {name}", playlist_id=playlist_id, name=name)

    def _set_drip(self, index, change):
        link = self.work["collection"]
        if link is None:
            return self._error(index, "op", "Link a collection first")
        parts = []
        if change.drip_enabled is not None:
            link["drip_enabled"] = change.drip_enabled
            parts.append(
                "add collected tracks automatically" if change.drip_enabled
                else "stop adding collected tracks automatically"
            )
        if change.drip_count is not None:
            link["drip_count"] = change.drip_count
            parts.append(f"add {change.drip_count} per run")
        self._stage(
            RaidLinkService.update_link, self.user.id, self.target_id,
            drip_enabled=change.drip_enabled, drip_count=change.drip_count,
        )
        self._entry(
            index, change, f"Collection: {', '.join(parts)}",
            drip_enabled=change.drip_enabled, drip_count=change.drip_count,
        )

    def _set_auto_archive(self, index, change):
        pair = self.work["archive"]
        if pair is None:
            return self._error(index, "op", "Pair an archive first")
        pair["auto_archive_on_remove"] = change.value
        self._stage(PlaylistPairService.update_pair, self.user.id, self.target_id, auto_archive_on_remove=change.value)
        verb = "auto-archive" if change.value else "stop auto-archiving"
        self._entry(index, change, f"Archive: {verb} removed tracks", value=change.value)

    def _check_distinct(self):
        """Refuse a change that makes the collection and archive one playlist; a legacy shared pair is kept."""
        collection, archive = self.work["collection"], self.work["archive"]
        if collection and archive and collection["playlist_id"] == archive["playlist_id"]:
            for role in ("collection", "archive"):
                if role in self.set_by:
                    self._error(self.set_by[role], "playlist_id", DISTINCT)


# --- Impact -----------------------------------------------------------------------


def _effects(routine, change, entry, effects, reasons, *, adding_on):
    """Add what one change does to one routine to effects, or why it does not to reasons.

    adding_on: whether the collection adds collected tracks automatically once every change applies."""
    steps, op = _steps(routine), change.op
    collects = _reads(routine, "sources")
    if op in ("add_playlist_source", "add_search_source"):
        if collects:
            effects.append(f"will use {entry['name']} on its next run")
    elif op == "remove_source":
        if collects:
            effects.append(f"will stop collecting from {entry['name']}")
    elif op == "set_source_count":
        if collects:
            effects.append(f"will collect {change.raid_count} per run from {entry['name']}")
    elif op == "remove_routine_source":
        if routine["id"] in entry["routine_ids"]:
            effects.append(f"will stop collecting from {change.playlist_id}")
    elif op in ASSET_OPS:
        role, kind = ASSET_OPS[op]
        new, old = entry.get("name"), (entry.get("previous") or {}).get("name")
        for step in steps:
            if ROWS[role] in STEP_ROWS[step]:
                effects.append(ASSET_EFFECTS[role][step][kind].format(new=new, old=old))
    elif op == "set_drip" and "drip" in steps:
        if change.drip_enabled is not None:
            if drip_forced(_params(routine)):
                reasons.append("always adds collected tracks, whatever the Drip setting")
            else:
                effects.append("saved runs add collected tracks" if change.drip_enabled else "saved runs skip adding")
        if change.drip_count is not None:
            own = _own_drip_count(routine)
            if own is not _SHARED_COUNT:
                reasons.append(f"keeps its own count of {own} per run")
            elif adding_on or drip_forced(_params(routine)):
                effects.append(f"saved runs add {change.drip_count} per run")
            else:
                # The count waits while saved runs skip adding: it is not claimed as what they add.
                effects.append(f"will add {change.drip_count} per run when adding is on")


def _stranded(review):
    """Raid-family routines that can collect from a source now and could not after the changes."""
    before = {routine["id"]: routine for routine in review.before["routines"]}
    return [
        routine for routine in review.work["routines"]
        if _reads(routine, "sources")
        and (review.before["sources"] or _ids(before[routine["id"]]))
        and not (review.work["sources"] or _ids(routine))
    ]


def _stranded_note(routines):
    sentences = []
    for enabled, state in ((True, "enabled"), (False, "paused")):
        group = [routine for routine in routines if bool(routine["is_enabled"]) == enabled]
        if group:
            verb = "stays" if len(group) == 1 else "stay"
            sentences.append(f"{_labels(group)} {verb} {state} but cannot collect discoveries until a source is added.")
    if any(len(_steps(routine)) > 1 for routine in routines):
        sentences.append("A composite routine still runs its other step.")
    return " ".join(sentences)


def _impact(review, changes):
    """(affected routines, unaffected routines, notes). Every routine is considered; each appears at most once."""
    entries = {entry["index"]: entry for entry in review.entries}
    adding_on = bool((review.work["collection"] or {}).get("drip_enabled"))
    affected, unaffected = [], []
    for routine in review.before["routines"]:
        effects, reasons = [], []
        for index, change in changes:
            _effects(routine, change, entries[index], effects, reasons, adding_on=adding_on)
        if effects:
            affected.append({
                "id": routine["id"],
                "operation": operation_label(routine["job_type"]),
                "timing_text": timing_text(
                    routine["schedule_type"], routine["schedule_value"], routine.get("timezone")
                ),
                "is_enabled": bool(routine["is_enabled"]),
                "effects": effects + reasons,
            })
        elif reasons:
            unaffected.append({
                "id": routine["id"], "operation": operation_label(routine["job_type"]), "reason": "; ".join(reasons),
            })
    ops = {change.op for _, change in changes}
    notes = []
    if ops & {"remove_source", "remove_routine_source"}:
        stranded = _stranded(review)
        if stranded:
            notes.append(_stranded_note(stranded))
    if "unlink_collection" in ops:
        drips = [routine for routine in review.before["routines"] if "drip" in _steps(routine)]
        if drips:
            notes.append(f"Saved runs of {_labels(drips)} skip adding tracks until a collection is linked.")
    if "set_auto_archive" in ops:
        notes.append(AUTO_ARCHIVE_NOTE)
    if not affected:
        notes.append(NO_ROUTINES)
    return affected, unaffected, notes


def _conflict(state, target_name, request):
    """409: the configuration changed since review. Carries the fresh context and, when known, what changed."""
    context = _context(state, target_name)
    changed = None
    if request.section_digests is not None:
        reviewed = request.section_digests.model_dump()
        changed = [name for name in SECTIONS if reviewed[name] != context["section_digests"][name]]
    return refusal("configuration_changed", 409, CONFIGURATION_CHANGED, context=context, changed_sections=changed)


def _invalid(errors):
    return refusal("invalid_changes", 400, INVALID_CHANGES, errors=errors)


# --- Save -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Saved:
    target_name: str
    revision: str
    affected_ids: list


def _apply(state, review):
    """Run the reviewed writes in order, then sync the routines' source lists; the caller commits once."""
    for write in review.writes:
        write()
    lists = {routine["id"]: routine["source_playlist_ids"] for routine in review.work["routines"]}
    for schedule in state.schedules:
        wanted = lists[schedule.id]
        if wanted != schedule.source_playlist_ids:
            # Assign a new list: the JSON column does not see in-place mutation.
            schedule.source_playlist_ids = copy.deepcopy(wanted)
    db.session.flush()


@dataclass(frozen=True)
class _Reviewed:
    """A request that passed the shared opening of Impact and Save."""

    target_name: str
    state: SetupState
    revision: str
    changes: list
    review: _Review
    acknowledged: dict


def _review_request(api, user, target_id, request, *, verify_acknowledgment=False):
    """Impact's and Save's opening: a refusal reply, or the reviewed request.

    It checks the target, reads fresh state, refuses a stale revision with a
    Conflict, puts the changes in apply order, verifies Save's acknowledgment
    before the review (a null one is malformed), and reviews; a Spotify failure
    becomes its refusal. Save therefore validates exactly as the preview does.
    """
    try:
        target_name = _target_name(api, user, target_id)
        state = read_state(user.id, target_id)
        revision = configuration_revision(state)
        if revision != request.configuration_revision:
            return _conflict(state, target_name, request)
        changes = _normalized(request.changes)
        acknowledged = _ack_payload(user, target_id, revision, _change_hash(target_id, changes))
        if verify_acknowledgment:
            reason = signed_digest.verify(ACK_PURPOSE, acknowledged, request.acknowledgment, max_age_s=ACK_MAX_AGE_S)
            if reason is not None:
                return refusal("acknowledgment_invalid", 409, ACK_MESSAGES[reason], reason=reason)
        review = _Review(api, user, target_id, target_name, state).run(changes)
    except (PlaylistError, SpotifyError) as error:
        return _target_refusal(error)
    if review.errors:
        return _invalid(review.errors)
    return _Reviewed(target_name, state, revision, changes, review, acknowledged)


def _save_locked(api, user, target_id, request):
    reviewed = _review_request(api, user, target_id, request, verify_acknowledgment=True)
    if not isinstance(reviewed, _Reviewed):
        return reviewed
    review = reviewed.review
    affected, _, _ = _impact(review, reviewed.changes)
    affected_ids = [routine["id"] for routine in affected]
    summaries = [entry["summary"] for entry in review.entries]
    try:
        _apply(reviewed.state, review)
        revision_after = configuration_revision(read_state(user.id, target_id))
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.PLAYLIST_SETUP_SAVE,
            description=f"Saved playlist settings: {'; '.join(summaries)}",
            playlist_id=target_id,
            playlist_name=reviewed.target_name,
            metadata={
                "changes": summaries,
                "affected_schedule_ids": affected_ids,
                "revision_before": reviewed.revision,
                "revision_after": revision_after,
            },
            commit=False,
        )
        safe_commit(f"save playlist setup for {target_id} (user {user.id})", PlaylistSetupError)
    except Exception:
        db.session.rollback()
        logger.exception("Playlist setup Save for %s did not commit", target_id)
        return refusal("save_failed", 500, SAVE_FAILED)
    return _Saved(reviewed.target_name, revision_after, affected_ids)


# --- Spotify creation ----------------------------------------------------------


def _existing_asset(user_id, target_id, purpose):
    """The collection or archive already saved for the target, read fresh as read_state reads it, or None."""
    saved = _config(read_state(user_id, target_id))[purpose]
    if saved is None:
        return None
    asset = _asset(saved)
    return {"id": asset["playlist_id"], "name": asset["name"]}


def _creation_unknown(status, error_code):
    return refusal(error_code, status, CREATION_UNKNOWN, outcome="unknown")


def _owned_entry(playlist, spotify_id):
    owner = playlist.get("owner") if isinstance(playlist.get("owner"), dict) else {}
    images = playlist.get("images")
    image = images[0] if isinstance(images, list) and images and isinstance(images[0], dict) else {}
    return {
        "id": playlist["id"],
        "name": _name(playlist.get("name"), playlist["id"]),
        "track_count": playlist_item_count(playlist, default=None),
        "image_url": image.get("url"),
        "owned": owner.get("id") == spotify_id,
    }


class PlaylistSetupService:
    """Shared prerequisite setup for one target playlist.

    Route-facing methods return ``(payload, status)`` or ``(payload, status,
    headers)``. ``get_context`` and the module's ``read_state`` and
    ``configuration_revision`` are also for other services: routine activation
    reads prerequisites and the revision through them, inside its own lock.
    """

    @staticmethod
    def get_context(user, target_id, api):
        """The setup context. Raises PlaylistNotFoundError or PlaylistAccessError for a target the user cannot edit."""
        context, _ = PlaylistSetupService.get_context_and_state(user, target_id, api)
        return context

    @staticmethod
    def get_context_and_state(user, target_id, api):
        """Return setup context and its exact source rows from one validated read."""
        target_name = _target_name(api, user, target_id)
        state = read_state(user.id, target_id)
        return _context(state, target_name), state

    @staticmethod
    def context_reply(api, user, target_id):
        try:
            context = PlaylistSetupService.get_context(user, target_id, api)
        except (PlaylistError, SpotifyError) as error:
            return _target_refusal(error)
        return {"success": True, "context": context}, 200

    @staticmethod
    def impact(api, user, target_id, request):
        """Review changes without writing: normalized changes, affected routines, notes and an acknowledgment."""
        reviewed = _review_request(api, user, target_id, request)
        if not isinstance(reviewed, _Reviewed):
            return reviewed
        affected, unaffected, notes = _impact(reviewed.review, reviewed.changes)
        return {
            "success": True,
            "configuration_revision": reviewed.revision,
            "changes": reviewed.review.entries,
            "affected_routines": affected,
            "unaffected_routines": unaffected,
            "notes": notes,
            "expected_state_digest": _state_digest(user.id, target_id, reviewed.review.work),
            "acknowledgment": signed_digest.issue(ACK_PURPOSE, reviewed.acknowledged),
        }, 200

    @staticmethod
    def save(api, user, target_id, request):
        """Apply acknowledged changes under the target lock in one transaction; never schedules anything."""
        with playlist_lock(target_id, timeout_s=LOCK_WAIT_S) as execution_acquired:
            if not execution_acquired:
                return refusal("playlist_busy", 409, BUSY, retryable=True)
            with playlist_configuration_lock(target_id, timeout_s=LOCK_WAIT_S) as configuration_acquired:
                if not configuration_acquired:
                    return refusal("playlist_busy", 409, BUSY, retryable=True)
                outcome = _save_locked(api, user, target_id, request)
        if not isinstance(outcome, _Saved):
            return outcome
        try:
            context = _context(read_state(user.id, target_id), outcome.target_name)
        except Exception:
            db.session.rollback()
            logger.warning("Playlist setup for %s saved; its fresh context could not be read", target_id, exc_info=True)
            context = None
        return {
            "success": True,
            "saved": True,
            "configuration_revision": outcome.revision,
            "affected_schedule_ids": outcome.affected_ids,
            "context": context,
        }, 200

    @staticmethod
    def create_playlist(api, user, target_id, request):
        """Create one private playlist in Spotify; it is never linked, paired or scheduled here."""
        try:
            target_name = _target_name(api, user, target_id)
        except (PlaylistError, SpotifyError) as error:
            return _target_refusal(error)
        existing = _existing_asset(user.id, target_id, request.purpose)
        if existing is not None:
            error_code, message = ALREADY[request.purpose]
            return refusal(error_code, 409, message, playlist=existing)
        try:
            created = api.create_user_playlist(request.name, public=False, description=DESCRIPTIONS[request.purpose])
        except SpotifyWriteUnverifiedError:
            logger.warning("Playlist creation for %s has an unknown outcome", target_id, exc_info=True)
            return _creation_unknown(502, "creation_unknown")
        except (SpotifyForbiddenError, SpotifyWriteRejectedError) as error:
            return refusal(
                "creation_refused", 422, "Spotify refused to create the playlist.",
                outcome="refused", reason=str(error)[:300] or "Spotify refused the request.",
            )
        except SpotifyRateLimitError as error:
            return _rate_limited(error, "Spotify is busy. Try again shortly.", outcome="rate_limited")
        except Exception:
            logger.exception("Playlist creation for %s failed", target_id)
            return _creation_unknown(500, "creation_failed")
        playlist_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(playlist_id, str) or not playlist_id:
            logger.warning("Spotify answered a playlist creation for %s without an ID", target_id)
            return _creation_unknown(502, "creation_unknown")
        name = _name(created.get("name"), request.name)
        privacy_confirmed = ensure_private(api, playlist_id)
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.PLAYLIST_SETUP_CREATE,
            description=f"Created {request.purpose} playlist '{name}' in Spotify",
            playlist_id=target_id,
            playlist_name=target_name,
            metadata={"purpose": request.purpose, "playlist_id": playlist_id, "privacy_confirmed": privacy_confirmed},
        )
        return {
            "success": True,
            "outcome": "created",
            "playlist": {"id": playlist_id, "name": name},
            "privacy_confirmed": privacy_confirmed,
        }, 201

    @staticmethod
    def owned_playlists(api, user, *, refresh=False):
        """The user's owned and collaborative playlists, hidden ones included; refresh bypasses the cache."""
        try:
            playlists = api.get_user_playlists(skip_cache=refresh)
        except (PlaylistError, SpotifyError) as error:
            return _read_refusal(error)
        entries = [
            _owned_entry(playlist, user.spotify_id)
            for playlist in playlists or []
            if isinstance(playlist, dict) and isinstance(playlist.get("id"), str) and playlist["id"]
        ]
        return {"success": True, "playlists": entries}, 200
