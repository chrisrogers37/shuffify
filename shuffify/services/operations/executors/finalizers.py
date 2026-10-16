"""Freeze Workshop source cleanup independently of its already-saved target.

The command layer captures/revalidates owned associations and pending rows. These
helpers only authorize complete Spotify resources, prepare one source intent and
apply it through the caller's journal. Local promotion/acceptance belongs to the
command's fenced database receipt, after verified source removal.
"""

import json
from copy import deepcopy
from dataclasses import asdict, dataclass

from shuffify.schemas.requests import TRACK_URI_RE
from shuffify.services.executors.base_executor import JobExecutionError
from shuffify.services.workshop_finalize_service import WorkshopFinalizeResult

from .collect_common import apply, validate


class FinalizerRejected(JobExecutionError):
    """An authored no-write refusal, suitable for a finalizer command's rich DTO."""

    def __init__(self, code, message, http_status=409):
        self.code, self.message, self.http_status = code, message, http_status
        self.retryable = False
        super().__init__(message)


@dataclass(frozen=True)
class FinalizeRow:
    id: int
    user_id: int
    target_playlist_id: str
    track_uri: str
    status: str


@dataclass(frozen=True)
class _FinalizerConfig:
    user_id: int
    target_playlist_id: str
    source_playlist_id: str | None
    linked_source_playlist_id: str | None
    target_track_uris: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "target_track_uris", tuple(self.target_track_uris))

    def document(self):
        return json.loads(json.dumps(asdict(self), allow_nan=False))


@dataclass(frozen=True)
class FinalizeRaidsConfig(_FinalizerConfig):
    pending_rows: tuple[FinalizeRow, ...]

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "pending_rows", tuple(self.pending_rows))


@dataclass(frozen=True)
class FinalizeRestoreConfig(_FinalizerConfig):
    track_uris: tuple[str, ...]

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "track_uris", tuple(self.track_uris))


@dataclass(frozen=True)
class ArchiveAppendConfig(FinalizeRestoreConfig):
    baseline_track_uris: tuple[str, ...]

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "baseline_track_uris", tuple(self.baseline_track_uris))


def _uris(values):
    return all(isinstance(value, str) and TRACK_URI_RE.fullmatch(value) for value in values)


def _validate(runtime, config, expected_type, prepare_key):
    if type(config) is not expected_type:
        raise FinalizerRejected("invalid_configuration", "Finalization requires captured configuration.")
    source = config.source_playlist_id
    if source != config.linked_source_playlist_id or source is None and expected_type is not FinalizeRaidsConfig:
        raise FinalizerRejected("source_changed", "The linked source changed. Review the original source.")
    if source == config.target_playlist_id:
        raise FinalizerRejected("invalid_source", "The source must be different from the saved playlist.")
    validate(runtime, config, prepare_key, (config.target_playlist_id, source))
    if not _uris(config.target_track_uris):
        raise FinalizerRejected("invalid_configuration", "The captured target contains invalid track URIs.")
    if expected_type is FinalizeRaidsConfig:
        rows = config.pending_rows
        if (
            not rows
            or any(
                type(row) is not FinalizeRow
                or type(row.id) is not int
                or row.id < 1
                or type(row.user_id) is not int
                or row.user_id != config.user_id
                or row.target_playlist_id != config.target_playlist_id
                or row.status not in {"pending", "promoted"}
                or not _uris((row.track_uri,))
                for row in rows
            )
            or len({row.id for row in rows}) != len(rows)
        ):
            raise FinalizerRejected(
                "invalid_track_selection", "Some selected tracks are unavailable for this playlist."
            )
    elif not config.track_uris or not _uris(config.track_uris):
        raise FinalizerRejected("invalid_track_selection", "Choose the exact source track occurrences.")
    if expected_type is ArchiveAppendConfig and not _uris(config.baseline_track_uris):
        raise FinalizerRejected("invalid_configuration", "The captured archive contains invalid track URIs.")


def _prepare(runtime, config, label):
    target, source = config.target_playlist_id, config.source_playlist_id
    before = {target: runtime.read_resource(target)}
    if before[target]["track_uris"] != list(config.target_track_uris):
        raise FinalizerRejected("target_changed", "The saved playlist changed. Review it before cleanup.")
    rows = config.pending_rows if type(config) is FinalizeRaidsConfig else ()
    requested = [row.track_uri for row in rows] if rows else list(config.track_uris)
    if label != "archive_append" and not set(requested).issubset(before[target]["track_uris"]):
        raise FinalizerRejected("target_tracks_missing", "The saved playlist does not contain every selected track.")
    if source is not None:
        before[source] = runtime.read_resource(source)
    desired = deepcopy(before)
    actions, local = [], {}
    current = before[source]["track_uris"] if source else []
    name = before[source].get("metadata", {}).get("name") if source else None
    selected = set(requested)
    present = [value for value in current if value in selected]
    choices = {"selected_uris": requested, "source_affected_count": len(present)}
    if label == "archive_append":
        expected = list(config.baseline_track_uris) + requested
        if current == expected:
            affected = 0
        elif current != list(config.baseline_track_uris):
            raise FinalizerRejected("source_changed", "The archive changed. Review its current contents.")
        else:
            affected = len(requested)
            desired[source]["track_uris"] = expected
            actions = [
                {
                    "kind": "add",
                    "resource": source,
                    "track_uris": requested,
                    "position": None,
                    "before": deepcopy(before[source]),
                }
            ]
        choices.update(baseline_track_uris=list(config.baseline_track_uris), source_affected_count=affected)
        extra = {"archived_count": len(requested)}
    else:
        if present:
            desired[source]["track_uris"] = [value for value in current if value not in selected]
            actions = [
                {
                    "kind": "remove",
                    "resource": source,
                    "track_uris": list(dict.fromkeys(requested)),
                    "before": deepcopy(before[source]),
                }
            ]
        if label == "finalize_raids":
            affected = len({row.track_uri for row in rows if row.status == "pending"} | set(present))
            extra = {
                "promoted_count": len(rows),
                "source_status": "complete" if source else "not_required",
                "database_status": "pending" if any(row.status == "pending" for row in rows) else "complete",
            }
            local["pending_promotion"] = {
                "target_playlist_id": target,
                "rows": [asdict(row) for row in rows],
                "pending_ids": [row.id for row in rows if row.status == "pending"],
                "selected_count": len(rows),
                "affected_count": affected,
            }
        else:
            affected = len(present)
            extra = {"restored_count": len(requested)}
    result = WorkshopFinalizeResult(
        source,
        name,
        "complete" if affected else "not_required",
        "Collection changes verified." if label == "finalize_raids" else "Source changes verified.",
        affected_count=affected,
        extra=extra,
    ).to_dict()
    result["http_status"] = 200
    return {
        "version": 1,
        "config": config.document(),
        "before": before,
        "desired": desired,
        "actions": actions,
        "choices": choices,
        "result": result,
        "local_actions": local,
    }


def _prepared(runtime, config, expected_type, label, prepare_key):
    _validate(runtime, config, expected_type, prepare_key)
    prepared = runtime.prepare(prepare_key, lambda: _prepare(runtime, config, label))
    if prepared.get("version") != 1 or prepared.get("config") != config.document():
        raise FinalizerRejected("configuration_changed", "The captured finalizer configuration changed.")
    return prepared


def prepare_finalize_raids(runtime, config, *, prepare_key):
    return _prepared(runtime, config, FinalizeRaidsConfig, "finalize_raids", prepare_key)


def execute_finalize_raids(runtime, config, *, prepare_key):
    return apply(runtime, prepare_finalize_raids(runtime, config, prepare_key=prepare_key), "finalize_raids")


def prepare_archive_append(runtime, config, *, prepare_key):
    return _prepared(runtime, config, ArchiveAppendConfig, "archive_append", prepare_key)


def execute_archive_append(runtime, config, *, prepare_key):
    return apply(runtime, prepare_archive_append(runtime, config, prepare_key=prepare_key), "archive_append")


def prepare_finalize_restore(runtime, config, *, prepare_key):
    return _prepared(runtime, config, FinalizeRestoreConfig, "finalize_restore", prepare_key)


def execute_finalize_restore(runtime, config, *, prepare_key):
    return apply(runtime, prepare_finalize_restore(runtime, config, prepare_key=prepare_key), "finalize_restore")
