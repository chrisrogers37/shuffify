"""Durable Workshop target commit; HTTP admission performs no Spotify work."""

from dataclasses import asdict

from shuffify.services.operations.history import HistoryError
from shuffify.services.operations.runtime import OperationResult, VerifiedStepError
from shuffify.services.workshop_commit_service import WorkshopCommitService, WorkshopTargetResult

from .direct import publication_failure
from .envelope import validate_command
from .schemas import WorkshopCommitCommand


def workshop_commit(runtime):
    """Replay one frozen target action with authoritative scoped history."""
    command = validate_command(runtime, WorkshopCommitCommand)
    runtime.check_pending_control()
    history = runtime.history
    if history is None:
        raise HistoryError("The command's saved history binding is unavailable.")
    service = WorkshopCommitService(runtime.api)
    prepared = runtime.prepare("direct.action.v1", lambda: service.prepare_commit(
        command.playlist_id, runtime.user, command.baseline_track_uris, command.track_uris,
    ))

    def apply():
        try:
            result = service.apply_prepared(prepared, runtime.user, history, execution=runtime)
            runtime.check_pending_control()
            if result.http_status != 200 and runtime.verified_step_failure is None:
                # Persist the failure direction before a process interruption can
                # resume the forward plan. The worker alone owns compensation.
                runtime.journal.request_abort(result.error_code or "workshop_commit_failed", result=result.to_dict())
            return asdict(result)
        except Exception as error:
            runtime.fail_if_verified(error)
            raise

    try:
        raw = runtime.composite_step("direct.publication.v1", lambda: runtime.step("direct.apply.v1", apply),
                                     lambda storage, value: value)
    except VerifiedStepError as error:
        body = (error.applied["public_result"] if "public_result" in error.applied
                else WorkshopTargetResult(**error.applied).to_dict())
        return publication_failure(body)
    result = WorkshopTargetResult(**raw)
    runtime.check_pending_control()
    return OperationResult("succeeded" if result.http_status == 200 else "failed", result.to_dict())
