"""Create one playlist identity before separately verifying private visibility."""

from copy import deepcopy
from dataclasses import asdict, dataclass

from shuffify.services.executors.base_executor import JobExecutionError
from shuffify.services.operations.journal import NeedsAttention, OperationDeferred
from shuffify.services.operations.planner import content
from shuffify.services.operations.repository import LeaseLost
from shuffify.spotify.exceptions import SpotifyAuthError, SpotifyForbiddenError, SpotifyWriteRejectedError
from shuffify.spotify.observation import current_observer

from .collect_common import validate


@dataclass(frozen=True)
class CreationConfig:
    user_id: int
    target_playlist_id: str
    name: str
    description: str
    purpose: str

    def document(self):
        return asdict(self)


def _validate(runtime, config, prepare_key):
    if type(config) is not CreationConfig:
        raise JobExecutionError("Creation requires captured configuration")
    validate(runtime, config, prepare_key, (config.target_playlist_id,))
    if (
        not isinstance(config.name, str)
        or not config.name.strip()
        or not isinstance(config.description, str)
        or not isinstance(config.purpose, str)
        or not config.purpose.strip()
    ):
        raise JobExecutionError("Creation requires validated name, description, and purpose")


def _metadata(runtime, resource):
    observed = {**runtime.read_metadata_resource(resource), "scope": "metadata"}
    return {**observed, **content(observed)}


def prepare_creation(runtime, config, *, prepare_key):
    """Freeze caller-authored strings and editable target metadata, without domain effects."""
    _validate(runtime, config, prepare_key)

    def prepare():
        return {
            "version": 1,
            "config": config.document(),
            "before": {config.target_playlist_id: _metadata(runtime, config.target_playlist_id)},
            "choices": {
                "name": config.name,
                "description": config.description,
                "public": False,
                "purpose": config.purpose,
            },
        }

    prepared = runtime.prepare(prepare_key, prepare)
    if prepared.get("version") != 1 or prepared.get("config") != config.document():
        raise JobExecutionError("The captured creation configuration changed")
    return prepared


def _known_plan(runtime, config, position):
    plans = runtime.journal.doc["plans"]
    if position >= len(plans):
        return None
    stored = plans[position]
    intent = {"kind": "create", "name": config.name, "description": config.description, "public": False}
    return stored if stored.get("intent") == intent else None


def _facts(config, plan, *, privacy_confirmed=False, error=None):
    created = plan.get("result") if plan else None
    if created:
        name = created.get("name")
        name = name.strip() if isinstance(name, str) and name.strip() else config.name
        return {
            "success": True,
            "outcome": "created",
            "playlist": {"id": created["id"], "name": name},
            "privacy_confirmed": privacy_confirmed,
            "creation_unknown": False,
            "error_code": "privacy_unconfirmed" if error is not None else None,
        }
    unknown = bool(plan and plan["batches"][0]["state"] == "dispatched")
    deferred = isinstance(error, OperationDeferred)
    refused = isinstance(error, (SpotifyAuthError, SpotifyForbiddenError, SpotifyWriteRejectedError))
    return {
        "success": False,
        "outcome": "unknown" if unknown else "rate_limited" if deferred else "refused" if refused else "not_started",
        "privacy_confirmed": False,
        "creation_unknown": unknown,
        "error_code": "creation_unknown"
        if unknown
        else "rate_limited"
        if deferred
        else "authentication_required"
        if isinstance(error, SpotifyAuthError)
        else "creation_refused"
        if refused
        else "creation_failed",
    }


def _privacy_baseline(runtime, created, config):
    observed = _metadata(runtime, created["id"])
    metadata = observed["metadata"]
    # A known identity is not permission to overwrite intervening metadata edits.
    expected = {"name": config.name, "description": config.description}
    expected.update({key: created[key] for key in ("name", "description", "public") if key in created})
    if any(metadata.get(key) != value for key, value in expected.items()) or type(metadata.get("public")) is not bool:
        raise NeedsAttention("created_playlist_changed")
    return observed


def execute_creation(runtime, config, *, prepare_key):
    """Apply only creation/privacy plans; callers own association and terminal policy.

    Typed errors propagate with safe ``result`` facts. A known playlist remains
    known even when its privacy write fails; an unidentified creation is never
    adopted by name or automatically posted again.
    """
    if current_observer() is not runtime.journal:
        raise JobExecutionError("The executor requires the operation's outer journal observer")
    prepared = prepare_creation(runtime, config, prepare_key=prepare_key)
    position = runtime.journal.cursor
    try:
        for resource, frozen in prepared["before"].items():
            runtime.journal.observe_resource(resource, expected_baseline=frozen, scope="metadata")
        runtime.journal.ensure_create(config.name, public=False, description=config.description)
        created = runtime.api.create_user_playlist(config.name, public=False, description=config.description)
        if created["id"] == config.target_playlist_id:
            raise NeedsAttention("created_identity_matches_target")
        runtime.check_pending_control()
        baseline = runtime.prepare(prepare_key + ".privacy", lambda: _privacy_baseline(runtime, created, config))
        runtime.journal.ensure_details(created["id"], expected_baseline=baseline, public=False)
        runtime.api.update_playlist_details(created["id"], public=False)
        runtime.check_pending_control()
        after = {resource: _metadata(runtime, resource) for resource in (*prepared["before"], created["id"])}
        desired = {**deepcopy(baseline), "metadata": {**baseline["metadata"], "public": False}}
        if content(after[created["id"]]) != content(desired) or any(
            content(after[key]) != content(value) for key, value in prepared["before"].items()
        ):
            raise NeedsAttention("creation_verification_failed")
        return {
            "before": deepcopy(prepared["before"]),
            "after": after,
            "choices": deepcopy(prepared["choices"]),
            "result": _facts(config, _known_plan(runtime, config, position), privacy_confirmed=True),
            "local_actions": {},
        }
    except Exception as error:
        control = error
        seen = set()
        while control is not None and id(control) not in seen:
            if isinstance(control, LeaseLost):
                break
            seen.add(id(control))
            control = control.__cause__ or control.__context__
        control = control if isinstance(control, LeaseLost) else error
        facts = _facts(config, _known_plan(runtime, config, position), error=control)
        prior = deepcopy(getattr(control, "result", None) or {})
        control.result = {**prior, **facts}
        if prior.get("error_code"):
            control.result["error_code"] = prior["error_code"]
        if control is not error:
            raise control from None
        raise
