"""Freeze an actual archive-to-target move, then apply only its observed intent."""

from copy import deepcopy
from dataclasses import asdict, dataclass

from shuffify.schemas.requests import TRACK_URI_RE
from shuffify.services.executors.base_executor import JobExecutionError

from .collect_common import apply, validate


@dataclass(frozen=True)
class UnarchiveConfig:
    user_id: int
    target_playlist_id: str
    archive_playlist_id: str
    track_uris: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "track_uris", tuple(self.track_uris))

    def document(self):
        value = asdict(self)
        value["track_uris"] = list(self.track_uris)
        return value


def _validate(runtime, config, prepare_key):
    if type(config) is not UnarchiveConfig:
        raise JobExecutionError("Unarchive requires captured configuration")
    validate(runtime, config, prepare_key, (config.target_playlist_id, config.archive_playlist_id))
    if any(not isinstance(value, str) or not TRACK_URI_RE.fullmatch(value) for value in config.track_uris):
        raise JobExecutionError("Unarchive requires valid ordered track URIs")


def _prepare(runtime, config):
    target, archive = config.target_playlist_id, config.archive_playlist_id
    before = {resource: runtime.read_resource(resource) for resource in (target, archive)}
    desired = deepcopy(before)
    requested = list(config.track_uris)
    selected = set(requested)
    removed = [value for value in before[archive]["track_uris"] if value in selected]
    desired[target]["track_uris"].extend(requested)
    desired[archive]["track_uris"] = [value for value in before[archive]["track_uris"] if value not in selected]
    actions = []
    if requested:
        actions.append(
            {
                "kind": "add",
                "resource": target,
                "track_uris": requested,
                "position": None,
                "before": deepcopy(before[target]),
            }
        )
    if removed:
        actions.append(
            {
                "kind": "remove",
                "resource": archive,
                "track_uris": list(dict.fromkeys(requested)),
                "before": deepcopy(before[archive]),
            }
        )
    local = {
        "lock_reconcile": {"playlist_id": target, "track_uris": deepcopy(desired[target]["track_uris"])}
        if requested
        else None,
        "history": {
            "playlist_id": target,
            "before_uris": deepcopy(before[target]["track_uris"]),
            "after_uris": deepcopy(desired[target]["track_uris"]),
        }
        if requested
        else None,
        "activity": {"activity_type": "unarchive_tracks", "track_count": len(requested), "archive_playlist_id": archive}
        if requested
        else None,
    }
    return {
        "version": 1,
        "config": config.document(),
        "before": before,
        "desired": desired,
        "actions": actions,
        "choices": {"requested_uris": requested, "removed_uris": removed},
        "result": {
            "success": True,
            "status": "complete" if requested else "not_required",
            "unarchived_count": len(requested),
            "tracks_added": len(requested),
            "tracks_total": len(desired[target]["track_uris"]),
            "removed_count": len(removed),
            "message": f"Unarchived {len(requested)} tracks",
            "error_code": None,
            "retryable": False,
        },
        "local_actions": local,
    }


def prepare_unarchive(runtime, config, *, prepare_key):
    """Authorize complete inputs and freeze duplicate-sensitive orders without effects."""
    _validate(runtime, config, prepare_key)
    prepared = runtime.prepare(prepare_key, lambda: _prepare(runtime, config))
    if prepared.get("version") != 1 or prepared.get("config") != config.document():
        raise JobExecutionError("The captured unarchive configuration changed")
    return prepared


def execute_unarchive(runtime, config, *, prepare_key):
    """Append to target before archive cleanup; caller owns all fenced local receipts."""
    return apply(runtime, prepare_unarchive(runtime, config, prepare_key=prepare_key), "unarchive")
