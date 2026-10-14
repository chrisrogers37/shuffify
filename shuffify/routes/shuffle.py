"""Shuffle and undo form actions with verified direct track and history outcomes."""

from flask import jsonify, request, session
from pydantic import ValidationError

from shuffify.routes import main, require_auth_and_db
from shuffify.schemas import parse_shuffle_request
from shuffify.services.live_operation_service import (
    DIRECT_ENDPOINT_KINDS,
    direct_rejected_result,
    preflight_error_code,
)
from shuffify.services.live_playlist_service import LivePlaylistService
from shuffify.services.live_review_bindings import (
    ReviewBindingError,
    invalid_binding_result,
    request_binding,
)


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


@main.route("/shuffle/<playlist_id>", methods=["POST"])
@require_auth_and_db(on_preflight_error=_preflight)
def shuffle(playlist_id, api=None, user=None):
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
    return _respond(
        LivePlaylistService(api).shuffle(
            playlist_id,
            user,
            session,
            algorithm=parsed.algorithm,
            params=parsed.get_algorithm_params(),
            reviewed=reviewed,
        )
    )


@main.route("/undo/<playlist_id>", methods=["POST"])
@require_auth_and_db(on_preflight_error=_preflight)
def undo(playlist_id, api=None, user=None):
    try:
        reviewed = request_binding(request, "undo")
    except ReviewBindingError:
        return _respond(invalid_binding_result("undo"))
    return _respond(LivePlaylistService(api).undo(playlist_id, user, session, reviewed=reviewed))
