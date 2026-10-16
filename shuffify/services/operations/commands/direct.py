"""Direct worker handlers call services with prepared intent and scoped history."""

from copy import deepcopy

from shuffify.services.live_metadata_service import LiveMetadataService
from shuffify.services.live_operation_service import direct_rejected_result, direct_response_payload
from shuffify.services.live_playlist_service import LivePlaylistService
from shuffify.services.operations.runtime import OperationResult, VerifiedStepError
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


def publication_failure(body):
    """Keep known public provider facts while refusing another automatic write."""
    body = deepcopy(body)
    body.pop('playlist_state', None)
    body.update(success=False, status='failed', category='error', retryable=False,
                error_code='verified_step_publication_failed',
                message='Playlist changes were verified, but their local records could not be confirmed. '
                        'Review the result before another operation.')
    body['error'] = body['message']
    operation = body.get('operation')
    if isinstance(operation, dict):
        operation.update(error_code=body['error_code'], retryable=False)
        if operation.get('aggregate_status') == 'complete':
            operation['aggregate_status'] = 'partial'
        for item in operation.get('bookkeeping', []):
            if item.get('kind') == 'session_history':
                item.update(status='unknown', affected_count=None, error_code='history_confirmation_failed')
        if (operation.get('kind') in {'shuffle', 'undo', 'snapshot_restore'}
                and not any(item.get('kind') == 'session_history' for item in operation.get('bookkeeping', []))
                and (operation['kind'] == 'undo'
                     or any(item.get('dispatched') for item in operation.get('resources', [])))):
            operation.setdefault('bookkeeping', []).append({
                'kind': 'session_history', 'status': 'unknown', 'affected_count': None,
                'step': operation['kind'],
                'resource': next((item['resource'] for item in operation.get('resources', [])), None),
                'error_code': 'history_confirmation_failed',
            })
            operation['aggregate_status'] = 'unknown'
    bookkeeping = body.get('bookkeeping')
    if isinstance(bookkeeping, dict) and bookkeeping.get('history') == 'confirmed':
        bookkeeping['history'] = 'unknown'
    return OperationResult('needs_attention', body)


def _published(runtime, execute, kind):
    def apply():
        try:
            result = execute()
            body, _ = direct_response_payload(result, kind)
            if not body['success'] and runtime.verified_step_failure is None:
                runtime.journal.request_abort(body.get('operation', {}).get('error_code') or 'direct_action_failed',
                                              result=body)
            return result
        except Exception as error:
            runtime.fail_if_verified(error)
            raise

    try:
        result = runtime.composite_step('direct.publication.v1', apply, lambda storage, value: value)
    except VerifiedStepError as error:
        applied = error.applied
        body = (applied['public_result'] if 'public_result' in applied
                else direct_response_payload(applied, kind)[0])
        return publication_failure(body)
    return _outcome(runtime, result, kind)


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
    return _published(runtime, lambda: LivePlaylistService(runtime.api).shuffle(
        command.playlist_id, runtime.user, history, algorithm=command.algorithm,
        params=command.params, reviewed=command.reviewed, runtime=runtime,
    ), 'shuffle')


def undo(runtime):
    command = validate_command(runtime, UndoCommand)
    history, failure = _history(runtime, 'undo', command.playlist_id)
    if failure is not None:
        return failure
    selected = UndoSelection(tuple(tuple(uris) for uris in command.selection.states), command.selection.current_index)
    return _published(runtime, lambda: LivePlaylistService(runtime.api).undo(
        command.playlist_id, runtime.user, history, reviewed=command.reviewed,
        runtime=runtime, selected=selected,
    ), 'undo')


def visibility(runtime):
    command = validate_command(runtime, VisibilityCommand)
    return _published(runtime, lambda: LiveMetadataService(runtime.api).set_visibility(
        command.playlist_id, runtime.user, public=command.public,
        reviewed_scope=command.reviewed['reviewed_scope'] if command.reviewed else None, runtime=runtime,
    ), 'privacy')


def snapshot_restore(runtime):
    command = validate_command(runtime, SnapshotRestoreCommand)
    history, failure = _history(runtime, 'snapshot_restore', command.playlist_id)
    if failure is not None:
        return failure
    return _published(runtime, lambda: LivePlaylistService(runtime.api).restore_snapshot(
        command.snapshot, runtime.user, history, reviewed=command.reviewed, runtime=runtime,
    ), 'snapshot_restore')
