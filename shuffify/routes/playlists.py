"""
Playlist API routes: fetch, refresh, and query playlists.
"""

import logging

from flask import jsonify, request, session

from shuffify.routes import (
    api_bp,
    context_preflight,
    json_error,
    json_reply,
    no_store,
    require_auth_and_db,
)
from shuffify.schemas import PlaylistQueryParams
from shuffify.services import PlaylistError, PlaylistService
from shuffify.services.live_action_context_service import (
    INSPECTION_KINDS,
    InspectRequestError,
    LiveActionContextService,
    context_kind,
    inspection_payload,
    inspection_rejected,
    parse_inspect_request,
)
from shuffify.services.live_metadata_service import LiveMetadataService
from shuffify.services.live_operation_service import (
    direct_rejected_result,
    direct_response_payload,
    preflight_error_code,
)
from shuffify.services.live_playlist_service import LivePlaylistService
from shuffify.services.live_review_bindings import (
    ReviewBindingError,
    invalid_binding_result,
    request_binding,
)
from shuffify.services.live_review_context import context_unavailable
from shuffify.services.page_context_service import playlist_row
from shuffify.services.playlist_preference_service import (
    PlaylistPreferenceService,
)

logger = logging.getLogger(__name__)


@api_bp.route("/playlist/<playlist_id>")
@require_auth_and_db
def get_playlist(playlist_id, api=None, user=None):
    """Get playlist data with optional audio features."""
    query_params = PlaylistQueryParams(
        features=request.args.get("features", "false")
    )

    playlist_service = PlaylistService(api)
    playlist = playlist_service.get_playlist(
        playlist_id, query_params.features
    )
    return jsonify(playlist.to_dict())


@api_bp.route("/playlist/<playlist_id>/stats")
@require_auth_and_db
def get_playlist_stats(
    playlist_id, api=None, user=None
):
    """Get playlist audio feature statistics."""
    playlist_service = PlaylistService(api)
    stats = playlist_service.get_playlist_stats(playlist_id)
    return jsonify(stats)


@api_bp.route("/user-playlists")
@require_auth_and_db
def api_user_playlists(api=None, user=None):
    """Return the user's editable playlists as JSON; ``?refresh=1`` skips the cache."""
    try:
        playlist_service = PlaylistService(api)
        playlists = playlist_service.get_user_playlists(
            skip_cache=request.args.get("refresh") == "1"
        )

        # Apply user preferences (favorites first, hidden excluded)
        try:
            preferences = (
                PlaylistPreferenceService
                .get_user_preferences(user.id)
            )
            if preferences:
                favs, visible, _hidden = (
                    PlaylistPreferenceService
                    .apply_preferences(
                        playlists, preferences
                    )
                )
                playlists = favs + visible
        except Exception:
            pass  # Fall back to default order

        result = [playlist_row(playlist) for playlist in playlists]

        logger.debug(f"API returned {len(result)} playlists")
        return jsonify({"success": True, "playlists": result})
    except PlaylistError as e:
        logger.error(
            f"Failed to fetch playlists for API: {e}"
        )
        return json_error(
            "Failed to fetch playlists.", 500
        )


def _visibility_response(result):
    payload, status_code = direct_response_payload(result, "privacy")
    return jsonify(payload), status_code


def _visibility_preflight(message, status_code):
    return _visibility_response(direct_rejected_result(
        "privacy", error_code=preflight_error_code(status_code),
        http_status=status_code, retryable=status_code in (401, 503), message=message,
    ))


@api_bp.route(
    "/playlist/<playlist_id>/toggle-visibility",
    methods=["POST"],
)
@require_auth_and_db(on_preflight_error=_visibility_preflight)
def toggle_visibility(
    playlist_id, api=None, user=None
):
    """Apply explicit visibility intent and return fresh metadata verification."""
    data = request.get_json(silent=True)
    try:
        binding = request_binding(request, "privacy")
    except ReviewBindingError:
        return _visibility_response(invalid_binding_result("privacy"))
    public = data.get("public") if isinstance(data, dict) else None
    try:
        result = LiveMetadataService(api).set_visibility(
            playlist_id, user, public=public,
            reviewed_scope=binding["reviewed_scope"] if binding else None,
        )
    except Exception:
        logger.exception("Visibility outcome unavailable for %s", playlist_id)
        result = direct_rejected_result(
            "privacy", error_code="outcome_unavailable", http_status=502, unknown=True,
            message="The visibility change could not be verified. Check the playlist before another change.",
        )
    return _visibility_response(result)


@api_bp.route("/playlist/<playlist_id>/live-context", methods=["POST"])
@no_store
@require_auth_and_db(on_preflight_error=context_preflight())
def live_context(playlist_id, api=None, user=None):
    """Read-only review of one Library or Workshop live action; changes nothing."""
    # Read first: an oversized body must reach the 413 handler, not the fallback below.
    body = request.get_json(silent=True)
    try:
        reply = LiveActionContextService(api).resolve(playlist_id, user, session, body)
    except Exception:
        logger.exception("Live action review unavailable for %s", playlist_id)
        reply = context_unavailable(context_kind(body))
    return json_reply(reply)


def _inspection_preflight(message, status_code):
    return json_reply(inspection_rejected(
        request.get_json(silent=True), preflight_error_code(status_code), status_code,
        f"{message} This check did not run, so earlier results remain unverified.",
    ))


@api_bp.route("/playlist/<playlist_id>/inspect-live", methods=["POST"])
@no_store
@require_auth_and_db(on_preflight_error=_inspection_preflight)
def inspect_live(playlist_id, api=None, user=None):
    """Observe current Spotify state against retained intent; never writes or replays."""
    body = request.get_json(silent=True)
    try:
        kind, desired = parse_inspect_request(body)
    except InspectRequestError:
        return json_reply(inspection_rejected(
            body, "invalid_request", 400,
            "This check request is not valid. Earlier results remain unverified.",
        ))
    try:
        if kind == "privacy":
            result = LiveMetadataService(api).inspect_visibility(
                playlist_id, user, desired_public=desired,
            )
        else:
            result = LivePlaylistService(api).inspect_tracks(
                playlist_id, user, desired_track_uris=desired,
            )
    except Exception:
        logger.exception("Live inspection unavailable for %s", playlist_id)
        return json_reply(inspection_rejected(
            body, "inspection_failed", 502,
            "The current playlist could not be checked. Earlier results remain unverified.",
        ))
    return json_reply(inspection_payload(result, INSPECTION_KINDS[kind]))
