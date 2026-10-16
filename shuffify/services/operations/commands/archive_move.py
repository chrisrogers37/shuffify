"""Owned exact archive moves, captured locally before worker authorization."""

from copy import deepcopy
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from shuffify.models.db import PlaylistPair, User
from shuffify.schemas.workshop_finalize_requests import PlaylistID, TrackURI
from shuffify.services.operations.bookkeeping import OperationBookkeeping
from shuffify.services.operations.control import propagate_control
from shuffify.services.operations.executors.unarchive import UnarchiveConfig, execute_unarchive, prepare_unarchive
from shuffify.services.operations.history import HistoryError
from shuffify.services.operations.runtime import OperationResult, VerifiedStepError
from shuffify.services.state_service import StateService
from shuffify.spotify.observation import observe_mutations

from .envelope import validate_command


class ArchiveMoveRejected(ValueError):
    def __init__(self, code, message, http_status=409, retryable=False):
        self.code, self.message = code, message
        self.http_status, self.retryable = http_status, retryable
        super().__init__(message)


class UnarchiveCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    kind: ClassVar[str] = "unarchive"
    pair_id: Annotated[StrictInt, Field(gt=0)]
    playlist_id: PlaylistID
    archive_playlist_id: PlaylistID
    track_uris: Annotated[list[TrackURI], Field(min_length=1)]

    @property
    def reserved_targets(self):
        return sorted([self.playlist_id, self.archive_playlist_id])

    @model_validator(mode="after")
    def distinct(self):
        if self.playlist_id == self.archive_playlist_id:
            raise ValueError("The archive must be distinct from its target")
        return self


def capture_unarchive(storage, user_id, playlist_id, track_uris):
    try:
        with storage.no_autoflush:
            if (
                type(user_id) is not int
                or user_id < 1
                or storage.scalar(select(User.id).where(User.id == user_id)) is None
            ):
                raise ArchiveMoveRejected("user_not_found", "The authenticated user is unavailable.", 404)
            pair = (
                storage.execute(
                    select(PlaylistPair.__table__).where(
                        PlaylistPair.user_id == user_id,
                        PlaylistPair.production_playlist_id == playlist_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if pair is None:
                raise ArchiveMoveRejected("pair_not_found", "No archive pair found", 404)
            return UnarchiveCommand(
                pair_id=pair["id"],
                playlist_id=playlist_id,
                archive_playlist_id=pair["archive_playlist_id"],
                track_uris=track_uris,
            )
    except SQLAlchemyError as error:
        propagate_control(error)
        raise ArchiveMoveRejected("database_read_failed", "Could not read the archive pair.", 502, True) from None
    except ValidationError:
        raise ArchiveMoveRejected("configuration_changed", "Review the archive pair before moving tracks.") from None


def recapture(storage, user_id, command):
    current = capture_unarchive(storage, user_id, command.playlist_id, command.track_uris)
    if current != command:
        raise ArchiveMoveRejected("configuration_changed", "The archive pair changed. Review it before moving tracks.")
    return current


class VerifiedLocalError(RuntimeError):
    pass


def _local_control(error):
    with observe_mutations(None):
        propagate_control(error)


def _history_failure(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, VerifiedLocalError) and str(error) == "history_confirmation_failed":
            return True
        error = error.__cause__ or error.__context__
    return False


class UnarchiveHandler:
    def __call__(self, runtime):
        command = validate_command(runtime, UnarchiveCommand)
        if runtime.operation.schedule_id is not None or runtime.operation.scheduled_for is not None:
            raise ValueError("An archive move requires a manual operation")
        applied = None
        try:
            history = runtime.history
            if history is None:
                raise HistoryError("The saved history binding is unavailable")

            def bind():
                with Session(runtime.repository.engine) as storage:
                    current = recapture(storage, runtime.user.id, command)
                    return {"command": current.model_dump(mode="json")}

            command = UnarchiveCommand.model_validate(runtime.prepare("unarchive.binding.v1", bind)["command"])
            config = UnarchiveConfig(
                runtime.user.id, command.playlist_id, command.archive_playlist_id, tuple(command.track_uris)
            )

            def apply():
                nonlocal applied
                prepare_unarchive(runtime, config, prepare_key="unarchive.plan.v1")
                with Session(runtime.repository.engine) as storage:
                    recapture(storage, runtime.user.id, command)
                applied = execute_unarchive(runtime, config, prepare_key="unarchive.plan.v1")
                return applied

            def prepare_history(verified):
                try:
                    for resource, after in verified["after"].items():
                        StateService.record_verified_change(
                            history,
                            resource,
                            before_uris=verified["before"][resource]["track_uris"],
                            after_uris=after["track_uris"],
                        )
                except Exception as error:
                    _local_control(error)
                    try:
                        runtime.mark_history_unconfirmed()
                    except Exception as marker_error:
                        _local_control(marker_error)
                        raise VerifiedLocalError("history_confirmation_failed") from marker_error
                    raise VerifiedLocalError("history_confirmation_failed") from error

            def commit(storage, value):
                try:
                    recapture(storage, runtime.user.id, command)
                    book = OperationBookkeeping(storage, runtime.user.id, runtime.operation.id)
                    locks = {
                        resource: book.reconcile_locks(resource, after["track_uris"])
                        for resource, after in value["after"].items()
                    }
                    activity = book.optional(
                        lambda: book.activity(
                            "unarchive_tracks",
                            f"Unarchived {len(command.track_uris)} tracks",
                            playlist_id=command.playlist_id,
                            metadata={
                                "track_count": len(command.track_uris),
                                "operation_id": runtime.operation.id,
                                "archive_playlist_id": command.archive_playlist_id,
                            },
                        ),
                        fallback={"status": "failed", "error_code": "activity_record_failed"},
                    )
                    return {**value, "receipt": {"locks": locks, "activity": activity}}
                except Exception as error:
                    _local_control(error)
                    raise VerifiedLocalError("required_bookkeeping_failed") from error

            accepted = runtime.composite_step("unarchive", apply, commit, after_verify=prepare_history)
            return OperationResult("succeeded", {**accepted["result"], "bookkeeping": accepted["receipt"]})
        except (VerifiedLocalError, VerifiedStepError) as error:
            verified = error.applied if isinstance(error, VerifiedStepError) else applied
            result = deepcopy(verified["result"])
            result.update(
                success=False,
                error_code="history_confirmation_failed" if _history_failure(error) else "required_bookkeeping_failed",
                status="unknown",
                retryable=False,
                http_status=502,
                message="The archive move was verified, but its local records need review.",
            )
            return OperationResult("needs_attention", result)
        except Exception as error:
            attempted = any(
                batch.get("attempts", 0) for plan in runtime.get_journal().get("plans", []) for batch in plan["batches"]
            )
            code = (
                error.code
                if isinstance(error, ArchiveMoveRejected)
                else "history_unavailable"
                if isinstance(error, HistoryError)
                else "archive_move_failed"
            )
            result = {
                "success": False,
                "error_code": code,
                "status": "unknown" if attempted else "failed",
                "retryable": error.retryable
                if isinstance(error, ArchiveMoveRejected)
                else not attempted and not isinstance(error, HistoryError),
                "unarchived_count": None if attempted else 0,
                "tracks_added": None if attempted else 0,
                "tracks_total": None,
                "removed_count": None,
                "http_status": error.http_status if isinstance(error, ArchiveMoveRejected) else 502,
                "message": error.message
                if isinstance(error, ArchiveMoveRejected)
                else "The archive move did not complete.",
            }
            error.result = result
            propagate_control(error)
            return OperationResult("failed", result)

    def finalize(self, runtime, state, result):
        try:
            command = validate_command(runtime, UnarchiveCommand)
        except (ValueError, TypeError):
            return OperationResult(state, result)
        document = runtime.get_journal()
        if (result.get("error_code") == "verified_step_publication_failed"
                and document.get("status") != "needs_attention"):
            frozen = document.get("runtime", {}).get("prepared", {}).get("unarchive.plan.v1", {})
            result = {**deepcopy(frozen.get("result", {})), **result}
            result.update(status="unknown", http_status=502)
        result.setdefault("playlist_id", command.playlist_id)
        result.setdefault("archive_playlist_id", command.archive_playlist_id)
        for key in ("unarchived_count", "tracks_added", "tracks_total", "removed_count"):
            result.setdefault(key, None if state == "needs_attention" else 0)
        if state == "rolled_back":
            result.update(
                status="failed",
                success=False,
                unarchived_count=0,
                tracks_added=0,
                removed_count=0,
                tracks_total=None,
                retryable=True,
                message="The archive move was verified as restored.",
            )
        elif state == "needs_attention" and result.get("error_code") not in {
            "required_bookkeeping_failed",
            "history_confirmation_failed",
            "verified_step_publication_failed",
        }:
            result.update(
                status="unknown",
                success=False,
                unarchived_count=None,
                tracks_added=None,
                removed_count=None,
                tracks_total=None,
                retryable=False,
            )
        return OperationResult(state, result)


unarchive = UnarchiveHandler()
