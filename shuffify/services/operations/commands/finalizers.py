"""Queued source finalization after a separately confirmed Workshop target save."""

from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from shuffify.enums import ActivityType
from shuffify.models.db import PendingRaidTrack
from shuffify.services.operations.bookkeeping import OperationBookkeeping
from shuffify.services.operations.control import propagate_control
from shuffify.services.operations.executors.collect_storage import promote_pending
from shuffify.services.operations.executors.finalizers import (
    ArchiveAppendConfig,
    FinalizeRaidsConfig,
    FinalizeRestoreConfig,
    FinalizeRow,
    FinalizerRejected,
    execute_archive_append,
    execute_finalize_raids,
    execute_finalize_restore,
)
from shuffify.services.operations.runtime import OperationResult, VerifiedStepError
from shuffify.services.playlist_service import PlaylistAccessError
from shuffify.services.workshop_commit_service import IncompletePlaylistRead, UnsupportedPlaylistItems
from shuffify.services.workshop_finalize_service import WorkshopFinalizeResult
from shuffify.spotify.exceptions import (
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyRateLimitError,
    SpotifyWriteRejectedError,
)
from shuffify.spotify.observation import observe_mutations

from .envelope import validate_command
from .finalizer_capture import FinalizerCaptureRejected, recapture
from .finalizer_schemas import ArchiveAppendCommand, FinalizeRaidsCommand, FinalizeRestoreCommand

BINDING_KEY = "finalizer.binding.v1"
PLAN_KEY = "finalizer.plan.v1"


class RequiredCommitError(RuntimeError):
    """A verified source effect has no accepted local database receipt."""


def _binding(runtime, command):
    def capture():
        with Session(runtime.repository.engine) as storage:
            current = recapture(storage, runtime.user.id, command)
            return {"command": current.model_dump(mode="json")}

    return type(command).model_validate(runtime.prepare(BINDING_KEY, capture)["command"])


def _config(runtime, command):
    values = dict(
        user_id=runtime.user.id,
        target_playlist_id=command.playlist_id,
        source_playlist_id=command.source_playlist_id,
        linked_source_playlist_id=command.linked_source_playlist_id,
        target_track_uris=tuple(command.target_track_uris),
    )
    if isinstance(command, FinalizeRaidsCommand):
        return FinalizeRaidsConfig(
            **values,
            pending_rows=tuple(
                FinalizeRow(row.id, runtime.user.id, command.playlist_id, row.track_uri, row.status)
                for row in command.pending_rows
            ),
        )
    values["track_uris"] = tuple(command.track_uris)
    if isinstance(command, ArchiveAppendCommand):
        return ArchiveAppendConfig(**values, baseline_track_uris=tuple(command.baseline_track_uris))
    return FinalizeRestoreConfig(**values)


def _commit(runtime, command, storage, applied):
    try:
        # Recheck exact owned identities in the same fenced transaction as promotion.
        if isinstance(command, FinalizeRaidsCommand):
            storage.execute(
                select(PendingRaidTrack.id)
                .where(
                    PendingRaidTrack.id.in_(command.track_ids),
                    PendingRaidTrack.user_id == runtime.user.id,
                    PendingRaidTrack.target_playlist_id == command.playlist_id,
                )
                .order_by(PendingRaidTrack.id)
                .with_for_update()
            ).all()
        recapture(storage, runtime.user.id, command)
        result, receipt = deepcopy(applied["result"]), {}
        if isinstance(command, FinalizeRaidsCommand):
            rows = applied["local_actions"]["pending_promotion"]["rows"]
            receipt["promoted_ids"] = promote_pending(
                storage, runtime.user.id, command.playlist_id, [row for row in rows if row["status"] == "pending"]
            )
            result["database_status"] = "complete"
        activity = None
        if result["status"] == "complete" and (result["affected_count"] or 0) > 0:
            book = OperationBookkeeping(storage, runtime.user.id, runtime.operation.id)
            raid = isinstance(command, FinalizeRaidsCommand)
            restore = isinstance(command, FinalizeRestoreCommand)
            activity = book.optional(
                lambda: book.activity(
                    ActivityType.RAID_PROMOTE
                    if raid
                    else ActivityType.UNARCHIVE_TRACKS
                    if restore
                    else ActivityType.ARCHIVE_TRACKS,
                    f"Finalized {result['affected_count']} raided tracks"
                    if raid
                    else f"Verified {result['affected_count']} archive {'restorations' if restore else 'additions'}",
                    playlist_id=command.playlist_id,
                    metadata={"operation_id": runtime.operation.id, "track_count": result["affected_count"]},
                ),
                fallback={"status": "failed", "error_code": "activity_record_failed"},
            )
        result["bookkeeping"] = {"activity": activity}
        return {**applied, "result": result, "receipt": receipt}
    except Exception as error:
        with observe_mutations(None):
            propagate_control(error)
        raise RequiredCommitError("The verified source's local records could not be committed") from error


def _commit_failed(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, RequiredCommitError):
            return True
        error = error.__cause__ or error.__context__
    return False


class FinalizerHandler:
    def __init__(self, schema, label, execute, count_key):
        self.schema, self.label, self.execute, self.count_key = schema, label, execute, count_key

    def __call__(self, runtime):
        command = validate_command(runtime, self.schema)
        if runtime.operation.schedule_id is not None or runtime.operation.scheduled_for is not None:
            raise ValueError("A source finalizer requires a manual operation")
        applied = None
        try:
            command = _binding(runtime, command)

            def apply():
                nonlocal applied
                # The binding remains frozen across our own pending→promoted receipt.
                with Session(runtime.repository.engine) as storage:
                    recapture(storage, runtime.user.id, command)
                applied = self.execute(runtime, _config(runtime, command), prepare_key=PLAN_KEY)
                return applied

            accepted = runtime.composite_step(
                self.label, apply, lambda storage, value: _commit(runtime, command, storage, value)
            )
            return OperationResult("succeeded", accepted["result"])
        except (RequiredCommitError, VerifiedStepError) as error:
            failed = _commit_failed(error)
            if isinstance(error, VerifiedStepError):
                applied = error.applied
            code = "database_update_failed" if failed else "database_update_pending"
            if isinstance(command, FinalizeRaidsCommand):
                result = WorkshopFinalizeResult(
                    command.source_playlist_id,
                    self._name(runtime, applied),
                    "unknown",
                    "Source removal is verified. The inbox update still needs review.",
                    http_status=502,
                    affected_count=None,
                    error_code=code,
                    retryable=failed,
                    extra={
                        "promoted_count": 0 if failed else None,
                        "source_status": "complete" if command.source_playlist_id else "not_required",
                        "database_status": "failed" if failed else "unknown",
                        "aggregate_status": "partial",
                    },
                ).to_dict()
                result["http_status"] = 502
            else:
                result = deepcopy(applied["result"])
                result.update(
                    success=False,
                    status="unknown",
                    aggregate_status="partial",
                    error_code=code,
                    retryable=False,
                    http_status=502,
                    message="The source was verified, but its local records need review.",
                )
            return OperationResult("needs_attention", result)
        except Exception as error:
            result = self._failure(runtime, command, applied, error)
            error.result = result
            propagate_control(error)
            return OperationResult("failed", result)

    @staticmethod
    def _name(runtime, applied):
        prepared = runtime.get_journal().get("runtime", {}).get("prepared", {}).get(PLAN_KEY, {})
        return (applied or prepared).get("result", {}).get("resource_name")

    def _failure(self, runtime, command, applied, error):
        cause = error.cause if isinstance(error, SpotifyPartialBatchError) else error
        attempted = any(
            batch.get("attempts", 0) for plan in runtime.get_journal().get("plans", []) for batch in plan["batches"]
        )
        if not attempted and not isinstance(cause, (FinalizerCaptureRejected, FinalizerRejected)):
            if isinstance(cause, (PlaylistAccessError, SpotifyForbiddenError, SpotifyNotFoundError)):
                cause = FinalizerCaptureRejected(
                    "playlist_access_denied", "This playlist is no longer available to edit."
                )
            elif isinstance(cause, SpotifyAuthError):
                cause = FinalizerCaptureRejected("authentication_required", "Log in again to check this playlist.", 401)
            elif isinstance(cause, UnsupportedPlaylistItems):
                cause = FinalizerCaptureRejected(
                    "unsupported_playlist_items", "This playlist contains unsupported items."
                )
            else:
                cause = FinalizerCaptureRejected(
                    "upstream_read_failed", "Could not read the complete Spotify playlist.", 502, True
                )
        authored = isinstance(cause, (FinalizerCaptureRejected, FinalizerRejected))
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
            cause.code
            if authored
            else "source_write_rejected"
            if rejected
            else "unsupported_playlist_items"
            if isinstance(cause, UnsupportedPlaylistItems)
            else "access_denied"
            if isinstance(cause, PlaylistAccessError)
            else "incomplete_read"
            if isinstance(cause, IncompletePlaylistRead)
            else "cleanup_unverified"
        )
        known = authored or rejected or isinstance(cause, (PlaylistAccessError, UnsupportedPlaylistItems))
        result = WorkshopFinalizeResult(
            command.source_playlist_id,
            self._name(runtime, applied),
            "failed" if known else "unknown",
            cause.message
            if authored
            else "Spotify did not accept this source change."
            if rejected
            else "Check the source playlist to verify this step.",
            http_status=cause.http_status if authored else 502,
            affected_count=0 if known else None,
            error_code=code,
            retryable=cause.retryable if authored else rejected,
            extra={self.count_key: 0 if known else None},
        ).to_dict()
        result["http_status"] = cause.http_status if authored else 502
        return result

    def finalize(self, runtime, state, result):
        try:
            command = validate_command(runtime, self.schema)
        except (ValueError, TypeError):
            return OperationResult(state, result)
        document = runtime.get_journal()
        if (result.get("error_code") == "verified_step_publication_failed"
                and document.get("status") != "needs_attention"):
            # This classification follows a complete fresh recovery proof. The
            # frozen result supplies source facts, never an assumed DB receipt.
            frozen = document.get("runtime", {}).get("prepared", {}).get(PLAN_KEY, {})
            result = {**deepcopy(frozen.get("result", {})), **result}
            result.update(status="unknown", aggregate_status="partial", http_status=502)
            if isinstance(command, FinalizeRaidsCommand):
                accepted = next((item for item in document.get("boundaries", {}).get("accepted", [])
                                 if item["name"] == self.label), None)
                confirmed = accepted.get("result", {}).get("result", {}) if accepted else {}
                result.update(
                    source_status="complete" if command.source_playlist_id else "not_required",
                    database_status=confirmed.get("database_status", "unknown"),
                    promoted_count=confirmed.get("promoted_count"),
                    affected_count=confirmed.get("affected_count"),
                )
        # Recovery can settle before entering the domain handler. Reconstruct
        # identity from captured intent without treating predicted counts as facts.
        result.setdefault("resource", command.source_playlist_id)
        result.setdefault("resource_name", self._name(runtime, None))
        result.setdefault("http_status", 200 if state == "succeeded" else 502)
        result.setdefault(
            "status", "unknown" if state == "needs_attention" else "complete" if state == "succeeded" else "failed"
        )
        result.setdefault(
            "aggregate_status",
            "unknown" if state == "needs_attention" else "complete" if state == "succeeded" else "partial",
        )
        result.setdefault("affected_count", None if state == "needs_attention" else 0)
        result.setdefault(self.count_key, None if state == "needs_attention" else 0)
        if isinstance(command, FinalizeRaidsCommand):
            accepted = any(
                item["name"] == self.label for item in runtime.get_journal().get("boundaries", {}).get("accepted", [])
            )
            result.setdefault("database_status", "complete" if accepted else "pending")
            result.setdefault("source_status", "unknown" if command.source_playlist_id else "not_required")
            batches = [batch for plan in document.get("plans", []) for batch in plan["batches"]]
            # A reconciled first-write refusal has no provider or local effects.
            # Keep ambiguous, acknowledged and accepted work on its recovery path.
            if (
                state == "failed" and result.get("status") == "failed"
                and result.get("error_code") == "source_write_rejected"
                and result.get("success") is False
                and result.get("affected_count") == result.get(self.count_key) == 0
                and command.source_playlist_id is not None
                and all(plan["resource"] == command.source_playlist_id and plan["kind"] == "remove"
                        for plan in document.get("plans", []))
                and document.get("status") == "failed" and document.get("phase") == "forward"
                and not document.get("boundaries", {}).get("accepted")
                and batches and any(batch["state"] == "rejected" for batch in batches)
                and all(
                    batch["state"] == "prepared" and batch.get("attempts") == 0
                    or batch["state"] == "rejected" and isinstance(batch.get("last_http_status"), int)
                    and 400 <= batch["last_http_status"] < 500
                    for batch in batches
                )
            ):
                result.update(source_status="not_started", database_status="not_started")
        # The worker can discover an external edit while publishing a terminal candidate.
        # Its generic attention result cannot retain a complete source DTO/count.
        if state == "needs_attention" and result.get("error_code") not in {
            "database_update_failed",
            "database_update_pending",
            "verified_step_publication_failed",
        }:
            result.update(
                status="unknown",
                aggregate_status="unknown",
                affected_count=None,
                success=False,
                retryable=False,
                **{self.count_key: None},
            )
            if "source_status" in result:
                result["source_status"] = "unknown"
        elif state == "rolled_back":
            result.update(
                status="failed",
                source_status="restored" if command.source_playlist_id else "not_required",
                aggregate_status="partial",
                affected_count=0,
                success=False,
                retryable=True,
                **{self.count_key: 0},
                message="The source changes were verified as restored. The saved target is unchanged.",
            )
            if isinstance(command, FinalizeRaidsCommand):
                result["database_status"] = "complete" if accepted else "not_started"
        return OperationResult(state, result)


finalize_raids = FinalizerHandler(FinalizeRaidsCommand, "finalize_raids", execute_finalize_raids, "promoted_count")
archive_append = FinalizerHandler(ArchiveAppendCommand, "archive_append", execute_archive_append, "archived_count")
finalize_restore = FinalizerHandler(
    FinalizeRestoreCommand, "finalize_restore", execute_finalize_restore, "restored_count"
)
HANDLERS = {
    "workshop_finalize_raids": finalize_raids,
    "workshop_archive_append": archive_append,
    "workshop_finalize_restore": finalize_restore,
}
