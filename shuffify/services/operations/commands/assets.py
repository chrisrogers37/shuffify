"""Queued private playlist creation and fenced owned asset association."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from shuffify.services.operations.control import propagate_control
from shuffify.services.operations.executors.creation import CreationConfig, execute_creation
from shuffify.services.operations.journal import OperationDeferred
from shuffify.services.operations.runtime import OperationResult, VerifiedStepError
from shuffify.services.playlist_lock import playlist_configuration_lock
from shuffify.services.playlist_service import PlaylistAccessError, PlaylistError, PlaylistNotFoundError
from shuffify.services.playlist_setup_service import TARGET_NOT_EDITABLE, TARGET_NOT_FOUND
from shuffify.spotify.exceptions import SpotifyAuthError, SpotifyError, SpotifyNotFoundError, SpotifyRateLimitError
from shuffify.spotify.observation import observe_mutations

from .asset_capture import AssetCaptureRejected, recapture_asset
from .asset_schemas import AssetCommand
from .asset_storage import commit_asset
from .envelope import validate_command

BINDING_KEY = "asset.binding.v1"
PLAN_KEY = "asset.plan.v1"
CONFIG_LOCK_WAIT_S = 2.0


class AssetCommitError(RuntimeError):
    pass


def _control(error):
    with observe_mutations(None):
        propagate_control(error)


def _known_creation(runtime):
    """Expose only a returned creation identity, never infer an asset by name."""
    for plan in runtime.get_journal().get("plans", []):
        if plan.get("kind") != "create":
            continue
        known = plan.get("result")
        if isinstance(known, dict) and isinstance(known.get("id"), str) and known["id"]:
            return {
                "outcome": "created",
                "playlist": {"id": known["id"], "name": known.get("name")},
                "creation_unknown": False,
                "privacy_confirmed": False,
            }
        if any(batch.get("state") == "dispatched" for batch in plan["batches"]):
            return {"outcome": "unknown", "creation_unknown": True, "privacy_confirmed": False}
    return {"outcome": "not_started", "creation_unknown": False, "privacy_confirmed": False}


def _preflight_failure(error):
    chain, current = [], error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, PlaylistNotFoundError) for item in chain) or (
        isinstance(error, PlaylistAccessError) and any(isinstance(item, SpotifyNotFoundError) for item in chain)
    ):
        return {"error_code": "playlist_not_found", "message": TARGET_NOT_FOUND, "http_status": 404, "retryable": False}
    if isinstance(error, PlaylistAccessError):
        return {"error_code": "access_denied", "message": TARGET_NOT_EDITABLE, "http_status": 403, "retryable": False}
    if any(isinstance(item, SpotifyAuthError) for item in chain):
        return {
            "error_code": "authentication_required",
            "message": "Please log in again. Nothing was changed.",
            "http_status": 401,
            "retryable": False,
        }
    limited = next((item for item in chain if isinstance(item, SpotifyRateLimitError)), None)
    if limited is not None:
        delay = limited.retry_after if type(limited.retry_after) is int and limited.retry_after > 0 else None
        return {
            "error_code": "rate_limited",
            "message": "Spotify is busy. Nothing was changed; try again shortly.",
            "http_status": 429,
            "retryable": True,
            "retry_after": delay,
        }
    if any(isinstance(item, SQLAlchemyError) for item in chain):
        return {
            "error_code": "database_read_failed",
            "message": "Playlist settings could not be checked. Try again.",
            "http_status": 503,
            "retryable": True,
        }
    if any(isinstance(item, (PlaylistError, SpotifyError)) for item in chain):
        return {
            "error_code": "spotify_unavailable",
            "message": "Spotify could not be reached. Nothing was changed.",
            "http_status": 502,
            "retryable": True,
        }
    return None


class AssetHandler:
    def __call__(self, runtime):
        command = validate_command(runtime, AssetCommand)
        if runtime.operation.schedule_id is not None or runtime.operation.scheduled_for is not None:
            raise ValueError("Asset creation requires a manual operation")
        with playlist_configuration_lock(command.playlist_id, timeout_s=CONFIG_LOCK_WAIT_S) as acquired:
            if not acquired:
                raise OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=1))
            return self._run(runtime, command)

    def _run(self, runtime, command):
        try:

            def binding():
                with Session(runtime.repository.engine) as storage:
                    recapture_asset(storage, runtime.user.id, command)
                return {"command": command.model_dump(mode="json")}

            command = AssetCommand.model_validate(runtime.prepare(BINDING_KEY, binding)["command"])

            def apply():
                with Session(runtime.repository.engine) as storage:
                    recapture_asset(storage, runtime.user.id, command)
                if command.create_new:
                    return execute_creation(
                        runtime,
                        CreationConfig(
                            runtime.user.id, command.playlist_id, command.name, command.description, command.purpose
                        ),
                        prepare_key=PLAN_KEY,
                    )
                if not command.already_linked:

                    def observe():
                        return {
                            resource: {**runtime.read_metadata_resource(resource), "scope": "metadata"}
                            for resource in command.reserved_targets
                        }

                    before = runtime.prepare(PLAN_KEY, observe)
                    for resource, value in before.items():
                        runtime.journal.observe_resource(resource, expected_baseline=value, scope="metadata")
                else:
                    before = {}
                return {
                    "before": before,
                    "after": before,
                    "local_actions": {},
                    "result": {
                        "success": True,
                        "outcome": "existing",
                        "creation_unknown": False,
                        "playlist": {"id": command.asset_playlist_id, "name": command.name},
                    },
                }

            def commit(storage, applied):
                try:
                    return commit_asset(storage, runtime.user.id, runtime.operation.id, command, applied)
                except Exception as error:
                    _control(error)
                    raise AssetCommitError("The verified asset association could not be committed") from error

            accepted = runtime.composite_step("asset", apply, commit)
            return OperationResult("succeeded", accepted["result"])
        except VerifiedStepError as error:
            result = deepcopy(error.applied["result"])
            result.update(
                success=False,
                error_code="database_update_pending",
                association_status="unknown",
                message="The playlist was verified, but its local association needs review.",
                retryable=False,
                http_status=502,
            )
            return OperationResult("needs_attention", result)
        except Exception as error:
            _control(error)
            result = _known_creation(runtime)
            attempted = result["outcome"] in {"created", "unknown"}
            facts = getattr(error, "result", None)
            if not attempted and isinstance(facts, dict) and facts.get("outcome") == "refused":
                auth = facts.get("error_code") == "authentication_required"
                return OperationResult(
                    "failed",
                    {
                        **result,
                        "success": False,
                        "outcome": "refused",
                        "error_code": "authentication_required" if auth else "creation_refused",
                        "http_status": 401 if auth else 422,
                        "retryable": False,
                        "association_status": "not_started",
                        "message": "Please log in again. Nothing was changed."
                        if auth
                        else "Spotify refused to create the playlist.",
                    },
                )
            if not runtime.get_journal().get("plans") and not isinstance(error, AssetCaptureRejected):
                preflight = _preflight_failure(error)
                if preflight is not None:
                    return OperationResult(
                        "failed", {**result, **preflight, "success": False, "association_status": "not_started"}
                    )
            result.update(
                success=False,
                error_code=error.code
                if isinstance(error, AssetCaptureRejected)
                else "creation_unknown"
                if result["creation_unknown"]
                else "privacy_unconfirmed"
                if attempted
                else "creation_failed",
                message=error.message
                if isinstance(error, AssetCaptureRejected)
                else "Review the created playlist and its privacy before linking it."
                if attempted
                else "The playlist could not be created.",
                association_status="not_started",
                retryable=False,
                http_status=error.http_status if isinstance(error, AssetCaptureRejected) else 502,
            )
            return OperationResult("needs_attention" if attempted else "failed", result)

    def finalize(self, runtime, state, result):
        try:
            command = validate_command(runtime, AssetCommand)
        except (ValueError, TypeError):
            return OperationResult(state, result)
        if command.create_new:
            for key, value in _known_creation(runtime).items():
                result.setdefault(key, value)
        known = _known_creation(runtime) if command.create_new else {}
        if known.get("creation_unknown"):
            result.update(
                outcome="unknown",
                creation_unknown=True,
                privacy_confirmed=False,
                error_code="creation_unknown",
                http_status=502,
                message="We could not confirm whether the playlist was created. "
                "Check your Spotify playlists before creating another.",
            )
        if state == "needs_attention":
            result.setdefault("http_status", 502)
            result.update(success=False, retryable=False)
            if result.get("error_code") != "database_update_pending":
                result["privacy_confirmed"] = False
            result.setdefault("association_status", "unknown")
        return OperationResult(state, result)


asset = AssetHandler()
HANDLERS = {kind: asset for kind in ("pair_create", "raid_link_create", "setup_playlist_create")}
