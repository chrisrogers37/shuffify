"""Shared playlist setup JSON endpoints.

Reading the context, reviewing changes and listing playlists write nothing.
Save applies acknowledged changes to the shared configuration; creating a
Spotify playlist is a separate write that never links, pairs or schedules.
Neither starts, pauses or changes a routine.
"""

from flask import abort, request
from pydantic import ValidationError

from shuffify.error_handlers import format_validation_error
from shuffify.routes import (
    api_bp,
    json_reply,
    no_store,
    require_auth_and_db,
    require_session_and_db,
)
from shuffify.routes.asset_queue import admit_asset
from shuffify.schemas import CreateSetupPlaylistRequest, SetupImpactRequest, SetupSaveRequest
from shuffify.services.live_operation_service import preflight_error_code
from shuffify.services.page_context_service import RETURN_TARGETS
from shuffify.services.playlist_setup_service import PlaylistSetupService, refusal


def _preflight(message, status_code):
    """The auth and database gate's refusals, in the setup envelope."""
    return json_reply(refusal(preflight_error_code(status_code), status_code, message))


def _body(schema):
    """(request model, None), or (None, a 400 reply) when the body is not a valid JSON object for schema."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, refusal("invalid_request", 400, "Request body must be a JSON object.")
    try:
        return schema.model_validate(data), None
    except ValidationError as error:
        return None, refusal("invalid_request", 400, f"Validation error: {format_validation_error(error)}")


@api_bp.route("/playlist/<playlist_id>/setup/context")
@no_store
@require_auth_and_db(on_preflight_error=_preflight)
def playlist_setup_context(playlist_id, api=None, user=None):
    """The shared setup context and its configuration revision. Writes nothing."""
    return_target = request.args.get("return_target", request.args.get("return", "playlist"))
    if return_target not in RETURN_TARGETS:
        abort(404)
    payload, status, *headers = PlaylistSetupService.context_reply(api, user, playlist_id)
    if status < 400:
        payload = {**payload, "return_target": return_target}
    return json_reply((payload, status, *headers))


@api_bp.route("/playlist/<playlist_id>/setup/impact", methods=["POST"])
@no_store
@require_auth_and_db(on_preflight_error=_preflight)
def playlist_setup_impact(playlist_id, api=None, user=None):
    """Review typed changes: affected routines, notes and a signed acknowledgment. Writes nothing."""
    body, invalid = _body(SetupImpactRequest)
    if invalid:
        return json_reply(invalid)
    return json_reply(PlaylistSetupService.impact(api, user, playlist_id, body))


@api_bp.route("/playlist/<playlist_id>/setup/settings", methods=["PUT"])
@require_auth_and_db(on_preflight_error=_preflight)
def playlist_setup_settings(playlist_id, api=None, user=None):
    """Save acknowledged shared settings. Never starts, pauses or schedules a routine."""
    body, invalid = _body(SetupSaveRequest)
    if invalid:
        return json_reply(invalid)
    return json_reply(PlaylistSetupService.save(api, user, playlist_id, body))


@api_bp.route("/playlist/<playlist_id>/setup/spotify-playlists", methods=["POST"])
@require_session_and_db(on_preflight_error=_preflight)
def playlist_setup_create_playlist(playlist_id, api=None, user=None):
    """Accept a private creation; only a later settings Save can link it."""
    body, invalid = _body(CreateSetupPlaylistRequest)
    if invalid:
        return json_reply(invalid)
    return admit_asset(user, playlist_id, "setup_playlist_create", body.model_dump())


@api_bp.route("/playlist/<playlist_id>/setup/playlists")
@no_store
@require_auth_and_db(on_preflight_error=_preflight)
def playlist_setup_playlists(playlist_id, api=None, user=None):
    """The user's owned and collaborative playlists, hidden ones included; ``?refresh=1`` skips the cache."""
    return json_reply(PlaylistSetupService.owned_playlists(api, user, refresh=request.args.get("refresh") == "1"))
