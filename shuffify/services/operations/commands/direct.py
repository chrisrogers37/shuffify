"""Direct worker handlers call services with prepared intent and scoped history."""

from shuffify.services.live_metadata_service import LiveMetadataService
from shuffify.services.live_operation_service import direct_rejected_result, direct_response_payload
from shuffify.services.live_playlist_service import LivePlaylistService
from shuffify.services.operations.runtime import OperationResult
from shuffify.services.state_service import StateError, UndoSelection

from .envelope import validate_command
from .schemas import ShuffleCommand, SnapshotRestoreCommand, UndoCommand, VisibilityCommand


def _outcome(runtime, result, kind):
    runtime.check_pending_control()
    body, _ = direct_response_payload(result, kind)
    state = 'succeeded' if body['success'] and result.get('status') == 'success' else 'failed'
    if result.get('operation', {}).get('error_code') == 'history_confirmation_failed':
        state = 'needs_attention'
    return OperationResult(state, body)


def _history(runtime, kind, playlist_id):
    try:
        history = runtime.history
        if history is None:
            raise StateError('The command has no saved history binding')
        if kind == 'undo':
            runtime.assert_history_revision(playlist_id)
        return history, None
    except StateError as error:
        status, code, message, retryable = LivePlaylistService.preflight_failure(error)
        return None, _outcome(runtime, direct_rejected_result(
            kind, error_code=code, http_status=status, message=message, retryable=retryable,
        ), kind)


def shuffle(runtime):
    command = validate_command(runtime, ShuffleCommand)
    history, failure = _history(runtime, 'shuffle', command.playlist_id)
    if failure is not None:
        return failure
    result = LivePlaylistService(runtime.api).shuffle(
        command.playlist_id, runtime.user, history, algorithm=command.algorithm,
        params=command.params, reviewed=command.reviewed, runtime=runtime,
    )
    return _outcome(runtime, result, 'shuffle')


def undo(runtime):
    command = validate_command(runtime, UndoCommand)
    history, failure = _history(runtime, 'undo', command.playlist_id)
    if failure is not None:
        return failure
    selected = UndoSelection(tuple(tuple(uris) for uris in command.selection.states), command.selection.current_index)
    result = LivePlaylistService(runtime.api).undo(
        command.playlist_id, runtime.user, history, reviewed=command.reviewed,
        runtime=runtime, selected=selected,
    )
    return _outcome(runtime, result, 'undo')


def visibility(runtime):
    command = validate_command(runtime, VisibilityCommand)
    result = LiveMetadataService(runtime.api).set_visibility(
        command.playlist_id, runtime.user, public=command.public,
        reviewed_scope=command.reviewed['reviewed_scope'] if command.reviewed else None, runtime=runtime,
    )
    return _outcome(runtime, result, 'privacy')


def snapshot_restore(runtime):
    command = validate_command(runtime, SnapshotRestoreCommand)
    history, failure = _history(runtime, 'snapshot_restore', command.playlist_id)
    if failure is not None:
        return failure
    result = LivePlaylistService(runtime.api).restore_snapshot(
        command.snapshot, runtime.user, history, reviewed=command.reviewed, runtime=runtime,
    )
    return _outcome(runtime, result, 'snapshot_restore')
