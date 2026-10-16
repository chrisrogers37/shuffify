"""Session-authorized admission of immutable worker commands, without Spotify I/O."""

from flask import jsonify, request, session, url_for

from shuffify.services.api_response import busy_error, error_payload
from shuffify.services.live_operation_service import direct_rejected_result, direct_response_payload
from shuffify.services.live_playlist_service import LivePlaylistService
from shuffify.services.operations.history import HistoryStore, get_history_scope
from shuffify.services.operations.repository import BusyOperation, OperationRepository, RequestKeyConflict
from shuffify.services.state_service import PLAYLIST_STATES_KEY, StateError


class AdmissionRejected(Exception):
    """A route's locally validated command cannot be accepted."""

    def __init__(self, response):
        self.response = response
        super().__init__("The command was not accepted")


def capture_history(user, playlist_id, *, storage=None):
    """Copy only this target's history and revision; never copy authentication."""
    store = HistoryStore(session=storage)
    binding = store.capture(user.id, get_history_scope(session), [playlist_id],
                            legacy=session.get(PLAYLIST_STATES_KEY))
    mapping = store.session_for(user.id, binding["scope"], [playlist_id],
                                legacy=binding["imports"], expected_revisions=binding["revisions"])
    return binding, mapping


def capture_direct_history(user, playlist_id, kind, *, storage=None):
    """Preserve the direct action's authored rejection when its local history is unusable."""
    try:
        return capture_history(user, playlist_id, storage=storage)
    except StateError as error:
        status, code, message, retryable = LivePlaylistService.preflight_failure(error)
        payload, status = direct_response_payload(direct_rejected_result(
            kind, error_code=code, http_status=status, retryable=retryable, message=message,
        ), kind)
        raise AdmissionRejected((jsonify(payload), status)) from error


def enqueue_command(user, kind, intent, build, *, schedule_id=None):
    """Reply with one owned operation, replaying the same key before local capture.

    ``build`` returns a validated Pydantic command and its optional scoped
    history binding. It may read local data, but must never contact Spotify.
    """
    key = request.headers.get("Idempotency-Key")
    if key is not None and (not key.strip() or len(key) > 128):
        return jsonify(error_payload("Use an idempotency key of 1 to 128 characters.", 400)), 400

    def factory(storage):
        command, history = build(storage)
        payload = {"version": 1, "command": command.model_dump(mode="json")}
        if history is not None:
            payload["history"] = history
        return getattr(command, "reserved_targets", [command.playlist_id]), payload

    repository = OperationRepository()
    try:
        accepted = repository.enqueue(kind=kind, user_id=user.id, intent=intent, command_factory=factory,
                                      request_key=key, schedule_id=schedule_id)
    except AdmissionRejected as error:
        return error.response
    except BusyOperation as error:
        payload, status = busy_error("An operation is using this playlist. Try again.", operation_id=error.operation_id)
        return jsonify(payload), status
    except RequestKeyConflict:
        return jsonify(error_payload("This idempotency key belongs to different changes.", 409,
                                     error_code="request_key_conflict", retryable=False)), 409
    location = url_for("api.operation_status", operation_id=accepted.id)
    return jsonify({"success": True, "category": "info", "message": "Operation accepted.",
                    "operation_id": accepted.id, "operation": repository.get_owned(user.id, accepted.id)}), 202, {
        "Location": location,
    }
