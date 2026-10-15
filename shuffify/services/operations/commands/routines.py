"""Queued routine orchestration: frozen child choices and atomic acceptance, one execution identity."""

from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from shuffify.enums import ActivityType, SnapshotType
from shuffify.models.db import ActivityLog, Schedule
from shuffify.services.live_review_bindings import REVIEW_CHANGED
from shuffify.services.live_review_scope import ReviewChanged, drip_forced, effective_drip_count, rotation_settings
from shuffify.services.operations.bookkeeping import OperationBookkeeping
from shuffify.services.operations.control import propagate_control
from shuffify.services.operations.executors.collect_storage import (
    apply_drip_local,
    apply_raid_local,
    capture_pending_rows,
    capture_pending_uris,
)
from shuffify.services.operations.executors.drip import DripConfig, PendingRow, execute_drip, prepare_drip
from shuffify.services.operations.executors.raid import RaidConfig, RaidSource, execute_raid, prepare_raid
from shuffify.services.operations.executors.resources import observe_resources
from shuffify.services.operations.executors.rotate import RotateConfig, execute_rotate, prepare_rotate
from shuffify.services.operations.executors.shuffle import ShuffleConfig, execute_shuffle, prepare_shuffle
from shuffify.services.operations.runtime import OperationResult
from shuffify.services.state_service import StateService
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyRateLimitError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)
from shuffify.spotify.observation import observe_mutations

from .envelope import validate_command
from .routine_capture import CaptureNotFound, CaptureRejected, recapture, require_review
from .routine_facts import BINDING_KEY, build_result, input_key, plan_key
from .routine_schemas import DripNowCommand, RaidAndDripCommand, RaidNowCommand, ScheduleRunCommand

EXECUTORS = {
    "raid": (prepare_raid, execute_raid),
    "drip": (prepare_drip, execute_drip),
    "rotate": (prepare_rotate, execute_rotate),
    "shuffle": (prepare_shuffle, execute_shuffle),
}
SNAPSHOTS = {
    "raid": SnapshotType.AUTO_PRE_RAID,
    "drip": SnapshotType.AUTO_PRE_DRIP,
    "rotate": SnapshotType.AUTO_PRE_ROTATE,
    "shuffle": SnapshotType.AUTO_PRE_SHUFFLE,
}


class RequiredCommitError(RuntimeError):
    pass


class VerifiedHistoryError(RuntimeError):
    pass


def _terminal_control(error):
    # Terminal DB callbacks intentionally run through a settled abort/failed state.
    # Inspect the exception chain without invoking the observer's forward-only guard.
    with observe_mutations(None):
        propagate_control(error)


def _initial_binding(runtime, command):
    def capture():
        with Session(runtime.repository.engine) as storage:
            current = recapture(storage, runtime.user.id, command)
            if isinstance(command, ScheduleRunCommand) and command.triggered_by == "scheduler":
                enabled = storage.scalar(
                    select(Schedule.is_enabled).where(
                        Schedule.id == command.definition.schedule_id,
                        Schedule.user_id == runtime.user.id,
                    )
                )
                if not enabled:
                    raise CaptureRejected("schedule_disabled", "This scheduled routine is paused.")
            require_review(current)
            return {"command": current.model_dump(mode="json"), "run_started": True}

    return type(command).model_validate(runtime.prepare(BINDING_KEY, capture)["command"])


def _step_inputs(runtime, command, name):
    values = runtime.get_journal().get("runtime", {}).get("prepared", {})
    if plan_key(name) not in values and input_key(name) in values:
        with Session(runtime.repository.engine) as storage:
            recapture(storage, runtime.user.id, command, steps=(name,))

    def capture():
        with Session(runtime.repository.engine) as storage:
            current = recapture(storage, runtime.user.id, command, steps=(name,))
            value = {"command": current.model_dump(mode="json")}
            if name == "raid":
                value["pending_track_uris"] = list(capture_pending_uris(storage, runtime.user.id, command.playlist_id))
            elif name == "drip":
                value["pending_rows"] = [
                    row.__dict__
                    for row in capture_pending_rows(
                        storage,
                        runtime.user.id,
                        command.playlist_id,
                    )
                ]
            return value

    return runtime.prepare(input_key(name), capture)


def _config(runtime, name, values):
    raw = values["command"]
    schema = (
        ScheduleRunCommand
        if raw["definition"]["schedule_id"] is not None
        else {
            "raid": RaidNowCommand,
            "drip": DripNowCommand,
            "raid_and_drip": RaidAndDripCommand,
        }[raw["definition"]["job_type"]]
    )
    command = schema.model_validate(raw)
    config, definition, user_id, target = command.config, command.definition, runtime.user.id, command.playlist_id
    link, pair = config.link, config.pair
    params = definition.algorithm_params or {}
    if name == "raid":
        return RaidConfig(
            user_id,
            target,
            link.raid_playlist_id if link else None,
            pair.archive_playlist_id if pair else None,
            sources=tuple(RaidSource(**source.model_dump()) for source in config.sources),
            extra_source_playlist_ids=tuple(definition.source_playlist_ids or []),
            pending_track_uris=tuple(values["pending_track_uris"]),
        )
    if name == "drip":
        if link is None:
            raise CaptureRejected("link_required", "No raid playlist link found. Create a raid link first.")
        forced = drip_forced(params)
        count = effective_drip_count(params, link) if forced or link.drip_enabled else 1
        return DripConfig(
            user_id,
            target,
            link.raid_playlist_id,
            count,
            link.drip_enabled,
            forced,
            tuple(PendingRow(**row) for row in values["pending_rows"]),
        )
    if name == "rotate":
        if pair is None:
            raise CaptureRejected("pair_required", "No archive playlist is linked to this target.")
        mode, count, cap, protect = rotation_settings(params)
        return RotateConfig(
            user_id,
            target,
            pair.archive_playlist_id,
            mode,
            count,
            cap,
            protect,
            tuple(lock.track_uri for lock in config.locks),
        )
    return ShuffleConfig(
        user_id, target, definition.algorithm_name, params, {lock.position: lock.track_uri for lock in config.locks}
    )


def _snapshots(runtime, name, prepared):
    def commit(storage):
        bookkeeping = OperationBookkeeping(storage, runtime.user.id, runtime.operation.id)
        receipts = []
        destinations = {runtime.operation.payload["command"]["playlist_id"]}
        if name in {"raid", "drip"}:
            collection = prepared["config"].get("collection_playlist_id")
            if collection:
                destinations.add(collection)
        for resource, before in prepared["before"].items():
            if resource not in destinations:
                continue
            receipts.append(
                bookkeeping.optional(
                    lambda resource=resource, before=before: bookkeeping.snapshot(
                        resource,
                        before.get("metadata", {}).get("name") or resource,
                        before["track_uris"],
                        SNAPSHOTS[name],
                        f"Before {name}",
                        auto=True,
                    ),
                    fallback={"status": "failed", "error_code": "snapshot_failed"},
                )
            )
        return receipts

    # A read-only/gated/no-op step does not need mutation snapshots.
    changing = any(
        value["track_uris"] != prepared["before"][resource]["track_uris"]
        for resource, value in prepared["desired"].items()
    )
    return runtime.local_step(f"routine.{name}.snapshots.v1", commit) if changing else []


def _apply(runtime, command, name):
    values = _step_inputs(runtime, command, name)
    config = _config(runtime, name, values)
    prepare, execute = EXECUTORS[name]
    first_prepare = plan_key(name) not in runtime.get_journal().get("runtime", {}).get("prepared", {})
    prepared = prepare(runtime, config, prepare_key=plan_key(name))
    if first_prepare:
        with Session(runtime.repository.engine) as storage:
            recapture(storage, runtime.user.id, command, steps=(name,))
    # Includes read-only target/archive exclusions, before Rotate's archive purge.
    observe_resources(runtime, prepared["before"])
    snapshots = _snapshots(runtime, name, prepared)
    applied = execute(runtime, config, prepare_key=plan_key(name))
    applied["snapshots"] = snapshots
    reported = applied["result"]
    if reported.get("status") in {"failed", "failed_rolled_back", "skipped"} and reported.get("success") is not False:
        reported.update(success=False, error_code="producer_result_inconsistent")
    history = runtime.history
    if history is not None:
        try:
            for resource, after in applied["after"].items():
                before = applied["before"][resource]["track_uris"]
                if before != after["track_uris"]:
                    StateService.record_verified_change(
                        history, resource, before_uris=before, after_uris=after["track_uris"]
                    )
        except Exception as error:
            propagate_control(error)
            runtime.mark_history_unconfirmed()
            raise VerifiedHistoryError("The verified playlist history could not be saved") from error
    return applied


def _commit(runtime, name, storage, applied):
    try:
        if name == "raid":
            receipt = apply_raid_local(
                storage,
                runtime.user.id,
                applied["local_actions"]["pending_stage"]["target_playlist_id"],
                applied["local_actions"],
            )
        elif name == "drip":
            receipt = apply_drip_local(
                storage,
                runtime.user.id,
                applied["local_actions"]["pending_promotion"]["target_playlist_id"],
                applied["local_actions"],
            )
        else:
            target = next(
                resource
                for resource in applied["after"]
                if resource == runtime.operation.payload["command"]["playlist_id"]
            )
            updated = OperationBookkeeping(storage, runtime.user.id, runtime.operation.id).reconcile_locks(
                target,
                applied["after"][target]["track_uris"],
            )
            receipt = {"lock_reconcile": {"status": "complete", "affected_count": updated}}
        return {**applied, "receipt": receipt}
    except Exception as error:
        propagate_control(error)
        raise RequiredCommitError("The verified playlist bookkeeping could not be committed") from error


class RoutineHandler:
    def __init__(self, schema):
        self.schema = schema

    def __call__(self, runtime):
        try:
            command = validate_command(runtime, self.schema)
        except (ValueError, TypeError):
            # A detached/deleted schedule still has its accepted queue identity.
            # Re-parse only to project the refusal; the guard failure never grants effects.
            command = self.schema.model_validate(runtime.operation.payload["command"])
            if (
                not isinstance(command, ScheduleRunCommand)
                or command.definition.schedule_id == runtime.operation.schedule_id
            ):
                raise
            return OperationResult(
                "failed",
                build_result(
                    runtime,
                    command,
                    "failed",
                    error_code="configuration_changed",
                    http_status=409,
                ),
            )
        phase = "coordination_failed"
        try:
            command = _initial_binding(runtime, command)
            phase = "execution_failed"
            for name in command.steps:
                child = runtime.composite_step(
                    name,
                    lambda name=name: _apply(runtime, command, name),
                    lambda storage, applied, name=name: _commit(runtime, name, storage, applied),
                )
                code = child["result"].get("error_code")
                if child["result"].get("success") is False or child["result"].get("skipped_reason") == "drip_disabled":
                    code = code or child["result"]["skipped_reason"]
                    return OperationResult(
                        "failed",
                        build_result(runtime, command, "failed", error_code=code, retryable=code == "drip_disabled"),
                    )
            return OperationResult("succeeded", build_result(runtime, command, "succeeded"))
        except (RequiredCommitError, VerifiedHistoryError) as error:
            code = (
                "history_confirmation_failed"
                if isinstance(error, VerifiedHistoryError)
                else "required_bookkeeping_failed"
            )
            document = runtime.get_journal()
            document.setdefault("runtime", {})["routine_db_failure"] = {
                "step": document.get("boundaries", {}).get("current", {}).get("name"),
                "error_code": code,
            }
            runtime.checkpoint(document)
            return OperationResult(
                "needs_attention",
                build_result(
                    runtime,
                    command,
                    "needs_attention",
                    error_code=code,
                    message="The playlist was verified, but its local records need review.",
                ),
            )
        except Exception as error:
            cause = error.cause if isinstance(error, SpotifyPartialBatchError) else error
            rejected = isinstance(
                cause,
                (
                    SpotifyAuthError,
                    SpotifyForbiddenError,
                    SpotifyNotFoundError,
                    SpotifyRateLimitError,
                    SpotifyWriteRejectedError,
                ),
            )
            code = (
                "review_changed"
                if isinstance(error, ReviewChanged)
                else "write_rejected"
                if rejected
                else "write_unverified"
                if isinstance(cause, SpotifyWriteUnverifiedError)
                else "configuration_changed"
                if isinstance(error, ValidationError)
                else "database_read_failed"
                if isinstance(error, SQLAlchemyError)
                else error.code
                if isinstance(error, CaptureRejected)
                else "configuration_changed"
                if isinstance(error, CaptureNotFound)
                else phase
            )
            status = (
                409
                if code in {"review_changed", "configuration_changed"}
                else 400
                if isinstance(
                    error,
                    (CaptureRejected, CaptureNotFound),
                )
                else 502
            )
            body = build_result(
                runtime,
                command,
                "failed",
                error_code=code,
                http_status=status,
                retryable=isinstance(error, SQLAlchemyError),
                message=REVIEW_CHANGED
                if isinstance(error, ReviewChanged)
                else error.message
                if isinstance(error, CaptureRejected)
                else None,
            )
            error.result = body
            propagate_control(error)
            # The worker records abort direction and compensates only the current unaccepted step.
            return OperationResult("failed", body)

    def finalize(self, runtime, state, result):
        try:
            command = validate_command(runtime, self.schema)
        except (ValueError, TypeError):
            return OperationResult(state, result)
        code = result.get("error_code") or result.get("operation", {}).get("error_code")
        body = build_result(
            runtime,
            command,
            state,
            error_code=code,
            message=result.get("message"),
            http_status=result.get("http_status"),
            retryable=result.get("retryable", False),
        )
        document = runtime.get_journal()
        if BINDING_KEY not in document.get("runtime", {}).get("prepared", {}):
            return OperationResult(state, body)
        receipts = document.get("runtime", {}).get("local_steps", {})
        activity_id = next(
            (
                value.get("activity_id")
                for key, value in receipts.items()
                if key.startswith("routine.finalize.") and value.get("activity_id")
            ),
            None,
        )

        def commit(storage):
            with observe_mutations(None):
                effects = any(
                    entry.get("dispatched") and entry["status"] in {"complete", "restored"}
                    for entry in body["operation"]["resources"]
                ) or any(
                    entry["kind"] in {"pending_stage", "pending_promotion"}
                    and entry["status"] == "complete"
                    and entry.get("affected_count", 0) > 0
                    for entry in body["operation"]["bookkeeping"]
                )
                recorded = activity_id
                if effects or activity_id is not None:
                    bookkeeping = OperationBookkeeping(storage, runtime.user.id, runtime.operation.id)

                    def activity():
                        metadata = {
                            "execution_id": runtime.operation.id,
                            "schedule_id": runtime.operation.schedule_id,
                            "triggered_by": command.triggered_by,
                            "status": body["status"],
                            "aggregate_status": body["operation"]["aggregate_status"],
                            "result_detail": body["result_detail"],
                            "tracks_added": body["tracks_added"],
                            "tracks_total": body["tracks_total"],
                        }
                        previous = storage.get(ActivityLog, activity_id) if activity_id is not None else None
                        if previous is not None:
                            if previous.user_id != runtime.user.id or previous.playlist_id != command.playlist_id:
                                raise ValueError("The previous run activity belongs to another operation")
                            previous.metadata_json = metadata
                            previous.activity_type = (
                                ActivityType.SCHEDULE_RUN_ROLLED_BACK
                                if state == "rolled_back"
                                else ActivityType.SCHEDULE_RUN
                            )
                            previous.description = body["message"]
                            storage.flush()
                            return previous.id
                        return bookkeeping.activity(
                            ActivityType.SCHEDULE_RUN_ROLLED_BACK
                            if state == "rolled_back"
                            else ActivityType.SCHEDULE_RUN,
                            body["message"],
                            playlist_id=command.playlist_id,
                            playlist_name=command.definition.target_playlist_name,
                            metadata=metadata,
                        )["id"]

                    activity_receipt = bookkeeping.optional(
                        lambda: {"status": "complete", "activity_id": activity()},
                        fallback={"status": "failed", "error_code": "activity_log_failed", "activity_id": activity_id},
                    )
                    recorded = activity_receipt["activity_id"]
                if command.definition.schedule_id is not None:
                    schedule = storage.scalar(
                        select(Schedule).where(
                            Schedule.id == command.definition.schedule_id, Schedule.user_id == runtime.user.id
                        )
                    )
                    if schedule is None:
                        raise CaptureNotFound("The run schedule is unavailable")
                    schedule.last_run_at = datetime.now(timezone.utc)
                    schedule.last_status = body["status"]
                    schedule.last_error = body.get("error")
                return {
                    "activity_id": recorded,
                    "status": body["status"],
                    "activity": activity_receipt
                    if effects or activity_id is not None
                    else {"status": "not_required", "activity_id": None},
                }

        try:
            runtime.repository.local_step(runtime.lease, f"routine.finalize.{state}.v1", commit)
            return OperationResult(state, body)
        except Exception as error:
            _terminal_control(error)
            return OperationResult(
                "needs_attention",
                build_result(
                    runtime,
                    command,
                    "needs_attention",
                    error_code="terminal_finalization_failed",
                    message="The playlist result is retained, but its run records need review.",
                ),
            )


schedule_run = RoutineHandler(ScheduleRunCommand)
raid_now = RoutineHandler(RaidNowCommand)
drip_now = RoutineHandler(DripNowCommand)
raid_and_drip = RoutineHandler(RaidAndDripCommand)
