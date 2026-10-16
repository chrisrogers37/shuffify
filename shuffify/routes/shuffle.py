"""Shuffle and undo form actions with verified direct track and history outcomes."""

from flask import jsonify, request
from pydantic import ValidationError

from shuffify.routes import api_bp, require_session_and_db
from shuffify.routes.queue import AdmissionRejected, capture_direct_history, enqueue_command
from shuffify.schemas import parse_shuffle_request
from shuffify.services.live_operation_service import (
    DIRECT_ENDPOINT_KINDS,
    DirectFailure,
    direct_rejected_result,
    preflight_error_code,
)
from shuffify.services.live_playlist_service import LivePlaylistService
from shuffify.services.live_review_bindings import (
    ReviewBindingError,
    invalid_binding_result,
    request_binding,
)
from shuffify.services.operations.commands.schemas import ShuffleCommand, UndoCommand
from shuffify.services.state_service import StateError, StateService


def _respond(result):
    body = {key: value for key, value in result.items() if key != "http_status"}
    complete = result["operation"]["aggregate_status"] == "complete" and result["status"] == "success"
    body["success"] = complete
    if not complete:
        body["category"] = "error"
    return jsonify(body), result["http_status"]


def _preflight(message, status_code):
    return _respond(
        direct_rejected_result(
            DIRECT_ENDPOINT_KINDS[request.endpoint],
            error_code=preflight_error_code(status_code),
            http_status=status_code,
            retryable=status_code in (401, 503),
            message=message,
        )
    )


@api_bp.route("/shuffle/<playlist_id>", methods=["POST"])
@require_session_and_db(on_preflight_error=_preflight)
def shuffle(playlist_id, user=None):
    # Optional reviewed binding: absent leaves the request unbound; a malformed one never reads Spotify.
    try:
        reviewed = request_binding(request, "shuffle")
    except ReviewBindingError:
        return _respond(invalid_binding_result("shuffle"))
    try:
        parsed = parse_shuffle_request(request.form.to_dict())
    except (ValidationError, ValueError):
        return _respond(
            direct_rejected_result(
                "shuffle",
                error_code="invalid_request",
                http_status=400,
                message="Check the shuffle method and its options.",
            )
        )
    command = ShuffleCommand(playlist_id=playlist_id, algorithm=parsed.algorithm,
                             params=parsed.get_algorithm_params(), reviewed=reviewed)

    def build(storage):
        binding, _mapping = capture_direct_history(user, playlist_id, "shuffle", storage=storage)
        return command, binding

    return enqueue_command(user, "shuffle", command.model_dump(mode="json"), build)


@api_bp.route("/undo/<playlist_id>", methods=["POST"])
@require_session_and_db(on_preflight_error=_preflight)
def undo(playlist_id, user=None):
    try:
        reviewed = request_binding(request, "undo")
    except ReviewBindingError:
        return _respond(invalid_binding_result("undo"))

    def build(storage):
        binding, mapping = capture_direct_history(user, playlist_id, "undo", storage=storage)
        try:
            selection = StateService.peek_undo(mapping, playlist_id)
            LivePlaylistService.validate_order(selection.desired_uris)
        except (StateError, DirectFailure) as error:
            raise AdmissionRejected(_history_error(error, "undo")) from error
        return UndoCommand(playlist_id=playlist_id, selection=selection.state_dict(), reviewed=reviewed), binding

    return enqueue_command(user, "undo", {"playlist_id": playlist_id, "reviewed": reviewed}, build)


def _history_error(error, kind):
    status, code, message, retryable = LivePlaylistService.preflight_failure(error)
    return _respond(direct_rejected_result(kind, error_code=code, http_status=status,
                                            retryable=retryable, message=message))
