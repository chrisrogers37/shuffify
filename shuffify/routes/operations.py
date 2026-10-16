"""Owner-scoped operation status, independent of Spotify availability."""

from shuffify.routes import api_bp, json_error, json_success, no_store, require_session_and_db


@api_bp.get("/operations/<int:operation_id>")
@require_session_and_db
def operation_status(operation_id, user=None):
    """Return bounded progress and the stored result without private command or journal data."""
    from shuffify.services.operations.repository import OperationRepository

    operation = OperationRepository().get_owned(user.id, operation_id)
    if operation is None:
        return json_error("Operation not found.", 404)
    return json_success("Operation loaded.", operation=operation)


def _restore_error(error):
    from flask import jsonify

    from shuffify.services.api_response import error_payload

    extra = {"operation_id": error.operation_id} if error.operation_id is not None else {}
    return jsonify(
        error_payload(error.message, error.http_status, error_code=error.code, retryable=False, **extra)
    ), error.http_status


def _restore_parent(operation_id, user_id):
    from shuffify.services.operations.repository import OperationRepository
    from shuffify.services.operations.restoration import RestoreUnavailable

    parent = OperationRepository().get(operation_id)
    if parent is None or parent.user_id != user_id:
        raise RestoreUnavailable("operation_not_found", "Operation not found.", 404)
    return parent


@api_bp.get("/operations/<int:operation_id>/restore-context")
@no_store
@require_session_and_db
def operation_restore_context(operation_id, user=None):
    """Read fresh authorized comparison facts after the original owner's local check."""
    from shuffify.routes import require_auth
    from shuffify.services.operations.control import propagate_control
    from shuffify.services.operations.restoration import RestoreUnavailable, capture_restore
    from shuffify.services.operations.restore_review import build_context, read_metadata, read_tracks

    try:
        parent = _restore_parent(operation_id, user.id)
        captured = capture_restore(parent, user.id)
        from sqlalchemy.orm import Session

        from shuffify.services.operations.repository import OperationRepository
        from shuffify.services.operations.restore_attempts import require_no_attempt

        with Session(OperationRepository().engine) as storage:
            require_no_attempt(storage, user.id, operation_id)
        api = require_auth()
        if api is None:
            return json_error("Please log in first.", 401)
        context = build_context(
            captured,
            lambda resource: read_tracks(api, user, resource),
            lambda resource: read_metadata(api, user, resource),
        )
        return json_success("Restoration review loaded.", context=context)
    except RestoreUnavailable as error:
        return _restore_error(error)
    except Exception as error:
        propagate_control(error)
        from flask import jsonify

        from shuffify.services.api_response import error_payload
        from shuffify.services.playlist_service import PlaylistAccessError
        from shuffify.services.workshop_commit_service import IncompletePlaylistRead, UnsupportedPlaylistItems
        from shuffify.spotify.exceptions import SpotifyAuthError, SpotifyRateLimitError

        status, code, message, retryable, extra = (
            502,
            "restore_read_failed",
            "The current playlists could not be read. Try loading the review again.",
            True,
            {},
        )
        if isinstance(error, SpotifyRateLimitError):
            status, code, message = 429, "rate_limited", "Spotify is busy. Load the review again after its retry delay."
            if type(error.retry_after) is int and error.retry_after > 0:
                extra["retry_after"] = error.retry_after
        elif isinstance(error, SpotifyAuthError):
            status, code, message, retryable = 401, "authentication_required", "Please log in again.", False
        elif isinstance(error, PlaylistAccessError):
            status, code, message, retryable = (
                403,
                "restore_access_changed",
                "A reviewed playlist is unavailable or is no longer editable.",
                False,
            )
        elif isinstance(error, UnsupportedPlaylistItems):
            status, code, message, retryable = (
                422,
                "unsupported_playlist_items",
                "A reviewed playlist contains items that cannot be restored safely.",
                False,
            )
        elif isinstance(error, IncompletePlaylistRead):
            status, code, message = (
                409,
                "restore_read_changed",
                "The playlists changed while loading. Load the review again.",
            )

        response = jsonify(error_payload(message, status, error_code=code, retryable=retryable, **extra))
        if "retry_after" in extra:
            response.headers["Retry-After"] = str(extra["retry_after"])
        return response, status


@api_bp.post("/operations/<int:operation_id>/restore")
@no_store
@require_session_and_db
def operation_restore_admit(operation_id, user=None):
    """Queue one immutable server-derived restoration; no provider work at admission."""
    from flask import request, session
    from pydantic import ValidationError

    from shuffify.routes.queue import AdmissionRejected, enqueue_command
    from shuffify.services.operations.commands.restore import capture_command
    from shuffify.services.operations.history import HistoryStore, get_history_scope
    from shuffify.services.operations.restoration import RestoreUnavailable
    from shuffify.services.operations.restore_review import RestoreRequest
    from shuffify.services.state_service import PLAYLIST_STATES_KEY

    try:
        _restore_parent(operation_id, user.id)
    except RestoreUnavailable as error:
        return _restore_error(error)

    try:
        value = RestoreRequest.model_validate(request.get_json(silent=True))
    except ValidationError:
        return _restore_error(
            RestoreUnavailable(
                "invalid_restore_review", "Load a complete restoration review before confirming it.", 400
            )
        )

    def build(storage):
        try:
            command = capture_command(storage, user.id, operation_id, value.reviewed)
        except RestoreUnavailable as error:
            raise AdmissionRejected(_restore_error(error)) from error
        binding = HistoryStore(session=storage).capture(
            user.id, get_history_scope(session), command.reserved_targets, legacy=session.get(PLAYLIST_STATES_KEY)
        )
        return command, binding

    return enqueue_command(
        user,
        "operation_restore",
        {"operation_id": operation_id, "reviewed": value.reviewed.model_dump(mode="json")},
        build,
    )
