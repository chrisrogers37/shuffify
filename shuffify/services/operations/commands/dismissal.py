"""Accept exact inbox dismissal before optional journaled collection cleanup."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from shuffify.enums import ActivityType
from shuffify.services.operations.bookkeeping import OperationBookkeeping
from shuffify.services.operations.control import propagate_control
from shuffify.services.operations.executors.collect_common import apply as apply_cleanup
from shuffify.services.operations.journal import OperationDeferred
from shuffify.services.operations.runtime import OperationResult, VerifiedStepError
from shuffify.services.playlist_lock import playlist_configuration_lock
from shuffify.spotify.observation import observe_mutations

from .dismiss_capture import DismissRejected, dismiss_captured, recapture
from .dismiss_schemas import PendingDismissCommand
from .envelope import validate_command

BINDING_KEY, PLAN_KEY = "dismiss.binding.v1", "dismiss.cleanup.v1"
CONFIG_LOCK_WAIT_S = 2.0


def _control(error):
    with observe_mutations(None):
        propagate_control(error)


def _accepted(runtime, name):
    return next(
        (
            item["result"]
            for item in runtime.get_journal().get("boundaries", {}).get("accepted", [])
            if item["name"] == name
        ),
        None,
    )


def _result(runtime, command, *, source_status="not_started", removed_count=None, error_code=None):
    accepted = _accepted(runtime, "dismiss")
    return {
        "success": error_code is None,
        "dismissed_count": accepted["receipt"]["affected_count"] if accepted else None,
        "dismissed_ids": accepted["receipt"]["confirmed_ids"] if accepted else None,
        "changed_ids": accepted["receipt"]["changed_ids"] if accepted else None,
        "database_status": "complete" if accepted else "unknown",
        "source_status": source_status,
        "source_removed_count": removed_count,
        "source_playlist_id": command.source_binding.playlist_id if command.source_binding else None,
        "playlist_id": command.playlist_id,
        "message": f"Dismissed {accepted['receipt']['affected_count']} raided tracks."
        if accepted
        else "The inbox dismissal could not be confirmed.",
        "error_code": error_code,
        "retryable": False,
        "http_status": 200 if error_code is None else 502,
    }


def _cleanup(runtime, command):
    source = command.source_binding.playlist_id

    def prepare():
        before = runtime.read_resource(source)
        selected = {row.track_uri for row in command.pending_rows}
        desired = deepcopy(before)
        desired["track_uris"] = [uri for uri in before["track_uris"] if uri not in selected]
        count = len(before["track_uris"]) - len(desired["track_uris"])
        return {
            "before": {source: before},
            "desired": {source: desired},
            "actions": [
                {
                    "kind": "remove",
                    "resource": source,
                    "track_uris": list(dict.fromkeys(row.track_uri for row in command.pending_rows)),
                    "before": before,
                }
            ]
            if count
            else [],
            "choices": {},
            "local_actions": {},
            "result": {"source_status": "complete", "source_removed_count": count},
        }

    with Session(runtime.repository.engine) as storage:
        recapture(storage, runtime.user.id, command, allow_dismissed=True)
    prepared = runtime.prepare(PLAN_KEY, prepare)
    with Session(runtime.repository.engine) as storage:
        recapture(storage, runtime.user.id, command, allow_dismissed=True)
    return apply_cleanup(runtime, prepared, "Inbox cleanup")


class DismissHandler:
    def __call__(self, runtime):
        command = validate_command(runtime, PendingDismissCommand)
        if runtime.operation.schedule_id is not None or runtime.operation.scheduled_for is not None:
            raise ValueError("Inbox dismissal requires a manual operation")
        with playlist_configuration_lock(command.playlist_id, timeout_s=CONFIG_LOCK_WAIT_S) as acquired:
            if not acquired:
                raise OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=1))
            return self._run(runtime, command)

    def _run(self, runtime, command):
        try:

            def bind():
                with Session(runtime.repository.engine) as storage:
                    recapture(storage, runtime.user.id, command)
                return {"command": command.model_dump(mode="json")}

            command = PendingDismissCommand.model_validate(runtime.prepare(BINDING_KEY, bind)["command"])

            def dismiss(storage, _applied):
                receipt = dismiss_captured(storage, runtime.user.id, command)
                book = OperationBookkeeping(storage, runtime.user.id, runtime.operation.id)
                activity = book.optional(
                    lambda: book.activity(
                        ActivityType.RAID_DISMISS,
                        f"Dismissed {receipt['affected_count']} raided tracks",
                        playlist_id=command.playlist_id,
                        metadata={"operation_id": runtime.operation.id, "track_count": receipt["affected_count"]},
                    ),
                    fallback={"status": "failed", "error_code": "activity_record_failed"},
                )
                return {"receipt": receipt, "activity": activity}

            runtime.composite_step("dismiss", lambda: {}, dismiss)
            if not command.pending_rows or command.source_binding is None:
                return OperationResult(
                    "succeeded", _result(runtime, command, source_status="not_required", removed_count=0)
                )
            cleaned = runtime.composite_step(
                "dismiss_cleanup", lambda: _cleanup(runtime, command), lambda _storage, value: value
            )
            return OperationResult(
                "succeeded",
                _result(
                    runtime,
                    command,
                    source_status="complete",
                    removed_count=cleaned["result"]["source_removed_count"],
                ),
            )
        except VerifiedStepError as error:
            values = error.applied.get("result", {})
            result = _result(
                runtime,
                command,
                source_status=values.get("source_status", "not_started"),
                removed_count=values.get("source_removed_count"),
                error_code="dismissal_publication_pending",
            )
            return OperationResult("needs_attention", result)
        except Exception as error:
            _control(error)
            result = _result(
                runtime,
                command,
                error_code=error.code if isinstance(error, DismissRejected) else "collection_cleanup_failed",
            )
            retained = result["database_status"] == "complete"
            result.update(
                success=False,
                message=(
                    "The inbox dismissal is confirmed. Collection cleanup did not finish."
                    if retained
                    else error.message
                    if isinstance(error, DismissRejected)
                    else "The inbox dismissal could not be confirmed."
                ),
                http_status=error.http_status if isinstance(error, DismissRejected) else 502,
            )
            if not retained and isinstance(error, DismissRejected):
                result["database_status"] = "not_started"
            return OperationResult("failed", result)

    def finalize(self, runtime, state, result):
        try:
            command = validate_command(runtime, PendingDismissCommand)
        except (ValueError, TypeError):
            return OperationResult(state, result)
        accepted = _accepted(runtime, "dismiss")
        result.setdefault("playlist_id", command.playlist_id)
        result.setdefault("source_playlist_id", command.source_binding.playlist_id if command.source_binding else None)
        result.setdefault("database_status", "unknown")
        result.setdefault("dismissed_count", None)
        result.setdefault("dismissed_ids", None)
        result.setdefault("changed_ids", None)
        result.setdefault("http_status", 200 if state == "succeeded" else 502)
        if accepted:
            result.update(
                dismissed_count=accepted["receipt"]["affected_count"],
                database_status="complete",
                dismissed_ids=accepted["receipt"]["confirmed_ids"],
                changed_ids=accepted["receipt"]["changed_ids"],
            )
        if state == "rolled_back":
            result.update(source_status="restored", source_removed_count=0, success=False)
        elif state == "needs_attention" and result.get("error_code") != "dismissal_publication_pending":
            result.update(source_status="unknown", source_removed_count=None, success=False)
        elif state == "failed":
            result.update(source_status="failed" if accepted else "not_started", source_removed_count=0, success=False)
        if state != "succeeded" and result.get("http_status") == 200:
            result["http_status"] = 502
        return OperationResult(state, result)


pending_dismiss = DismissHandler()
