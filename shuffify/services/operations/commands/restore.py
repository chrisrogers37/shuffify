"""Explicit reviewed restoration of an owned operation's known unaccepted effects."""

from copy import deepcopy
from itertools import zip_longest
from typing import ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution
from shuffify.services.operations.bookkeeping import OperationBookkeeping
from shuffify.services.operations.control import propagate_control
from shuffify.services.operations.executors.resources import expected_baseline
from shuffify.services.operations.restoration import RestoreCapture, RestoreUnavailable, capture_restore
from shuffify.services.operations.restore_attempts import require_no_attempt
from shuffify.services.operations.restore_review import RestoreReview, assert_reviewed, read_all
from shuffify.services.operations.runtime import OperationResult, VerifiedStepError
from shuffify.services.state_service import StateService
from shuffify.spotify.observation import observe_mutations

from .envelope import validate_command

PLAN_KEY = "restore.plan.v1"


class OperationRestoreCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kind: ClassVar[str] = "operation_restore"
    capture: RestoreCapture
    reviewed: RestoreReview

    @property
    def playlist_id(self):
        return self.capture.playlist_id

    @property
    def reserved_targets(self):
        return self.capture.reserved_targets


def capture_command(storage, user_id, operation_id, reviewed):
    with storage.no_autoflush:
        parent = storage.scalar(
            select(JobExecution)
            .where(JobExecution.id == operation_id, JobExecution.user_id == user_id)
            .with_for_update()
        )
        captured = capture_restore(parent, user_id)
        require_no_attempt(storage, user_id, operation_id)
    return OperationRestoreCommand(capture=captured, reviewed=assert_reviewed(captured, reviewed))


def recapture(storage, user_id, command, *, lock=False, child_id=None):
    statement = select(JobExecution).where(
        JobExecution.id == command.capture.operation_id, JobExecution.user_id == user_id
    )
    if lock:
        statement = statement.with_for_update()
    with storage.no_autoflush:
        current = capture_restore(storage.scalar(statement), user_id)
        require_no_attempt(storage, user_id, command.capture.operation_id, exclude_operation_id=child_id)
    if current != command.capture:
        raise RestoreUnavailable("restore_scope_changed", "The operation's restorable scope changed. Review it again.")
    assert_reviewed(current, command.reviewed)
    return current


class RestoreLocalError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _control(error):
    with observe_mutations(None):
        propagate_control(error)


def _prepare(runtime, command):
    with Session(runtime.repository.engine) as storage:
        recapture(storage, runtime.user.id, command, child_id=runtime.operation.id)
    before = read_all(command.capture, runtime.read_resource, runtime.read_metadata_resource)
    assert_reviewed(command.capture, command.reviewed, before)
    desired = deepcopy(before)
    actions, resources = [], []
    positions = 0
    for resource in command.capture.resources:
        playlist = resource.playlist_id
        current = deepcopy(before[playlist])
        changed = False
        if resource.track_uris is not None:
            order = list(resource.track_uris)
            positions += sum(left != right for left, right in zip_longest(current["track_uris"], order))
            actions.append({"kind": "replace", "resource": playlist, "before": deepcopy(current), "track_uris": order})
            changed |= current["track_uris"] != order
            current["track_uris"] = order
        if resource.metadata:
            actions.append(
                {
                    "kind": "details",
                    "resource": playlist,
                    "before": deepcopy(current),
                    "metadata": deepcopy(resource.metadata),
                }
            )
            changed |= any(current["metadata"].get(key) != value for key, value in resource.metadata.items())
            current["metadata"].update(resource.metadata)
        desired[playlist] = current
        fact = {
            "playlist_id": playlist,
            "status": "complete" if changed else "not_required",
            "track_count": len(current["track_uris"]) if resource.track_uris is not None else None,
            "metadata": {key: current["metadata"][key] for key in resource.metadata},
        }
        if resource.track_uris is not None:
            fact["current_track_uris"] = deepcopy(current["track_uris"])
        resources.append(fact)
    return {
        "before": before,
        "desired": desired,
        "actions": actions,
        "result": {
            "success": True,
            "status": "complete",
            "parent_operation_id": command.capture.operation_id,
            "parent_resolution": "pending",
            "restored_resource_count": sum(item["status"] == "complete" for item in resources),
            "changed_position_count": positions,
            "resources": resources,
            "retryable": False,
            "http_status": 200,
            "message": "The reviewed operation changes were restored.",
        },
    }


def _apply(runtime, command):
    with Session(runtime.repository.engine) as storage:
        recapture(storage, runtime.user.id, command, child_id=runtime.operation.id)
    prepared = runtime.prepare(PLAN_KEY, lambda: _prepare(runtime, command))
    for item in command.capture.observations:
        runtime.journal.observe_resource(
            item.playlist_id, expected_baseline=prepared["before"][item.playlist_id], scope=item.scope
        )
    for action in prepared["actions"]:
        baseline = expected_baseline(runtime, action["resource"], action["before"])
        if action["kind"] == "replace":
            runtime.journal.ensure_replace(action["resource"], action["track_uris"], expected_baseline=baseline)
            runtime.api.update_playlist_tracks(action["resource"], action["track_uris"])
        else:
            runtime.journal.ensure_details(action["resource"], expected_baseline=baseline, **action["metadata"])
            runtime.api.update_playlist_details(action["resource"], **action["metadata"])
    after = read_all(command.capture, runtime.read_resource, runtime.read_metadata_resource)
    for item in command.capture.observations:
        expected = expected_baseline(runtime, item.playlist_id, prepared["desired"][item.playlist_id])
        if not runtime.journal.observation_is_current(
            item.playlist_id, after[item.playlist_id], expected,
            expected.get("snapshot_id") if item.scope == "tracks" else None,
        ):
            from shuffify.services.operations.journal import NeedsAttention

            raise NeedsAttention("restore_verification_failed")
    runtime.protect_verified("restore", prepared["result"])
    return {"before": prepared["before"], "after": after, "result": deepcopy(prepared["result"])}


def _commit(runtime, command, storage, applied):
    recapture(storage, runtime.user.id, command, lock=True, child_id=runtime.operation.id)
    book = OperationBookkeeping(storage, runtime.user.id, runtime.operation.id)
    locks = {
        resource.playlist_id: book.reconcile_locks(
            resource.playlist_id, applied["after"][resource.playlist_id]["track_uris"]
        )
        for resource in command.capture.resources
        if resource.track_uris is not None
    }
    activity = None
    if applied["result"]["restored_resource_count"]:
        activity = book.optional(
            lambda: book.activity(
                "operation_restore",
                f"Restored operation {command.capture.operation_id}",
                playlist_id=command.playlist_id,
                metadata={"operation_id": runtime.operation.id, "parent_operation_id": command.capture.operation_id},
            ),
            fallback={"status": "failed", "error_code": "activity_record_failed"},
        )
    parent = storage.get(JobExecution, command.capture.operation_id)
    document = deepcopy(parent.journal)
    document.setdefault("runtime", {})["restoration"] = {
        "operation_id": runtime.operation.id,
        "scope_fingerprint": command.capture.scope_fingerprint,
    }
    document["recovery"] = {"available": False, "action": None, "reason": "operation_already_restored"}
    parent.journal = document
    storage.flush()
    return {
        **applied,
        "receipt": {"locks": locks, "activity": activity},
        "result": {**applied["result"], "parent_resolution": "complete"},
    }


class RestoreHandler:
    def __call__(self, runtime):
        command = validate_command(runtime, OperationRestoreCommand)
        assert_reviewed(command.capture, command.reviewed)
        if runtime.operation.schedule_id is not None or runtime.operation.scheduled_for is not None:
            raise ValueError("A restoration requires a manual operation")
        try:
            # A history refusal belongs to this empty, undispatched boundary.
            runtime.journal.begin_step("restore")
            history = runtime.history
            if history is None:
                raise RestoreUnavailable("history_unavailable", "The restoration's saved history is unavailable.")
            for resource in command.reserved_targets:
                runtime.assert_history_revision(resource)

            def prepare_history(verified):
                try:
                    for resource in command.capture.resources:
                        if resource.track_uris is not None:
                            StateService.record_verified_change(
                                history,
                                resource.playlist_id,
                                before_uris=verified["before"][resource.playlist_id]["track_uris"],
                                after_uris=verified["after"][resource.playlist_id]["track_uris"],
                            )
                except Exception as error:
                    _control(error)
                    try:
                        runtime.mark_history_unconfirmed()
                    except Exception as marker:
                        _control(marker)
                    raise RestoreLocalError("history_confirmation_failed") from error

            accepted = runtime.composite_step(
                "restore",
                lambda: _apply(runtime, command),
                lambda storage, applied: _commit(runtime, command, storage, applied),
                after_verify=prepare_history,
            )
            return OperationResult("succeeded", {**accepted["result"], "bookkeeping": accepted["receipt"]})
        except VerifiedStepError as error:
            result = deepcopy(error.applied.get("result", error.applied.get("public_result", {})))
            result.update(
                success=False,
                status="unknown",
                parent_resolution="unknown",
                error_code="restore_publication_pending",
                message="Restoration was verified, but its local records need review.",
                http_status=502,
            )
            return OperationResult("needs_attention", result)
        except Exception as error:
            result = {
                "success": False,
                "status": "failed",
                "parent_operation_id": command.capture.operation_id,
                "parent_resolution": "not_started",
                "restored_resource_count": 0,
                "changed_position_count": 0,
                "resources": [],
                "error_code": error.code if isinstance(error, RestoreUnavailable) else "restore_failed",
                "message": error.message
                if isinstance(error, RestoreUnavailable)
                else "The restoration did not complete.",
                "http_status": error.http_status if isinstance(error, RestoreUnavailable) else 502,
                "retryable": False,
            }
            error.result = result
            _control(error)
            return OperationResult("failed", result)

    def finalize(self, runtime, state, result):
        try:
            command = validate_command(runtime, OperationRestoreCommand)
        except (ValueError, TypeError):
            return OperationResult(state, result)
        result["parent_operation_id"] = command.capture.operation_id
        accepted = runtime.journal.accepted_step("restore")
        if accepted is not None:
            result["parent_resolution"] = "complete"
        if state == "rolled_back":
            result.update(restored_resource_count=0, changed_position_count=0, parent_resolution="not_started")
            prepared = runtime.get_journal().get("runtime", {}).get("prepared", {}).get(PLAN_KEY)
            if isinstance(prepared, dict):
                result["resources"] = []
                for resource in command.capture.resources:
                    before = prepared["before"][resource.playlist_id]
                    fact = {
                        "playlist_id": resource.playlist_id,
                        "status": "rolled_back",
                        "track_count": len(before["track_uris"]) if resource.track_uris is not None else None,
                        "metadata": {key: before["metadata"].get(key) for key in resource.metadata},
                    }
                    if resource.track_uris is not None:
                        fact["current_track_uris"] = deepcopy(before["track_uris"])
                    result["resources"].append(fact)
        elif state == "needs_attention" and result.get("error_code") in {
            "restore_publication_pending",
            "verified_step_publication_failed",
        }:
            if accepted is None:
                result["parent_resolution"] = "unknown"
        elif state == "needs_attention" and result.get("error_code") not in {
            "restore_publication_pending",
            "verified_step_publication_failed",
        }:
            result.update(restored_resource_count=None, changed_position_count=None, success=False)
            for resource in result.get("resources", []):
                resource.update(status="unknown", track_count=None)
                resource.pop("current_track_uris", None)
                resource.pop("metadata", None)
        return OperationResult(state, result)


operation_restore = RestoreHandler()
