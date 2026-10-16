"""Read-only routine review and exact-ID activation routes."""

from flask import abort, request
from pydantic import ValidationError

from shuffify.error_handlers import format_validation_error
from shuffify.routes import (
    api_bp,
    context_preflight,
    json_reply,
    no_store,
    require_auth_and_db,
)
from shuffify.schemas.routine_requests import (
    RoutineActivateRequest,
    RoutineReviewRequest,
    RoutineStateRequest,
)
from shuffify.services.routine_activation_service import RoutineActivationService


def _navigation():
    """Validate routine deep-link identity and origin before loading playlist context."""
    requested = request.args.get("schedule_id")
    if requested is not None and (
        not requested.isascii() or not requested.isdecimal() or len(requested) > 18 or int(requested) < 1
    ):
        abort(404)
    origin = request.args.get("origin", request.args.get("from", "playlist"))
    if origin not in ("playlist", "routines"):
        abort(404)
    return int(requested) if requested is not None else None, origin


def _body(schema):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, ({"success": False, "error_code": "invalid_request", "message": "Send a JSON object.",
                       "field_errors": [{"path": "body", "message": "Send a JSON object."}]}, 400)
    try:
        return schema.model_validate(data).model_dump(exclude_unset=True), None
    except ValidationError as error:
        fields = [{"path": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
                  for item in error.errors()]
        return None, ({"success": False, "error_code": "invalid_request",
                       "message": format_validation_error(error), "field_errors": fields}, 400)


@api_bp.route("/playlist/<playlist_id>/routines/context", methods=["GET"])
@no_store
@require_auth_and_db(on_preflight_error=context_preflight("routine_context"))
def routine_context(playlist_id, api=None, user=None):
    requested, origin = _navigation()
    payload, status, *headers = RoutineActivationService.context(api, user, playlist_id)
    if status < 400:
        if requested is not None and not any(row["schedule_id"] == requested for row in payload["routines"]):
            abort(404)
        payload = {**payload, "requested_schedule_id": requested, "origin": origin}
    return json_reply((payload, status, *headers))


@api_bp.route("/playlist/<playlist_id>/routines/review", methods=["POST"])
@no_store
@require_auth_and_db(on_preflight_error=context_preflight("routine_review"))
def routine_review(playlist_id, api=None, user=None):
    body, error = _body(RoutineReviewRequest)
    if error:
        return json_reply(error)
    return json_reply(RoutineActivationService.review(api, user, playlist_id, body))


@api_bp.route("/playlist/<playlist_id>/routines/activate", methods=["POST"])
@no_store
@require_auth_and_db
def routine_activate(playlist_id, api=None, user=None):
    body, error = _body(RoutineActivateRequest)
    if error:
        return json_reply(error)
    return json_reply(RoutineActivationService.activate(api, user, playlist_id, body))


@api_bp.route("/routines/<int:schedule_id>/state", methods=["PUT"])
@no_store
@require_auth_and_db
def routine_state(schedule_id, api=None, user=None):
    body, error = _body(RoutineStateRequest)
    if error:
        return json_reply(error)
    return json_reply(RoutineActivationService.set_state(api, user, schedule_id, body))
