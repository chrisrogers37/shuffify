"""
Raid panel routes.

Manage shared sources, collection links, pending tracks and reviewed raid/drip actions.
"""

import logging
from contextlib import ExitStack

from flask import jsonify, request
from pydantic import ValidationError

from shuffify.enums import ActivityType
from shuffify.routes import (
    api_bp,
    json_error,
    json_reply,
    json_success,
    log_activity,
    require_auth_and_db,
    require_session_and_db,
    validate_json,
)
from shuffify.routes.asset_queue import admit_asset
from shuffify.routes.finalizer_queue import admit_finalizer
from shuffify.routes.routine_queue import admit_direct
from shuffify.schemas.pending_raid_requests import (
    DismissTracksRequest,
)
from shuffify.schemas.raid_link_requests import (
    CreateRaidLinkRequest,
    UpdateRaidLinkRequest,
    UpdateSourceRaidCountRequest,
)
from shuffify.schemas.raid_requests import (
    AddRaidUrlRequest,
    RaidNowRequest,
    UnwatchPlaylistRequest,
)
from shuffify.schemas.workshop_finalize_requests import FinalizeRaidRequest
from shuffify.services.api_response import busy_error
from shuffify.services.live_operation_service import (
    LIVE_ENDPOINT_KINDS,
    error_status,
    normalized_operation,
    preflight_error_code,
    rejected_result,
)
from shuffify.services.live_review_bindings import (
    ReviewBindingError,
    invalid_binding_result,
    request_binding,
    review_message,
)
from shuffify.services.pending_raid_service import (
    PendingRaidService,
)
from shuffify.services.playlist_lock import (
    PlaylistBusyError,
    playlist_configuration_lock,
    playlist_lock,
)
from shuffify.services.playlist_service import (
    PlaylistNotFoundError,
    PlaylistService,
)
from shuffify.services.raid_link_service import (
    RaidLinkError,
    RaidLinkNotFoundError,
    RaidLinkService,
)
from shuffify.services.raid_sync_service import (
    RaidSyncError,
    RaidSyncService,
)
from shuffify.services.scheduler_service import (
    ScheduleBusyError,
)
from shuffify.services.upstream_source_service import (
    UpstreamSourceLimitError,
    UpstreamSourceNotFoundError,
    UpstreamSourceService,
)
from shuffify.services.workshop_finalize_service import (
    WorkshopFinalizeResult,
    WorkshopFinalizeService,
)
from shuffify.spotify.url_parser import (
    parse_spotify_playlist_url,
)

logger = logging.getLogger(__name__)


# =============================================================
# Raid Status
# =============================================================


@api_bp.route(
    "/playlist/<playlist_id>/raid-status",
    methods=["GET"],
)
@require_auth_and_db
def raid_status(playlist_id, api=None, user=None):
    """Get raid panel status for a playlist."""
    status = RaidSyncService.get_raid_status(
        user.spotify_id, playlist_id, user=user
    )
    return json_success(
        "Raid status loaded", raid_status=status
    )


# =============================================================
# Raid Playlist Link CRUD
# =============================================================


@api_bp.route(
    "/playlist/<playlist_id>/raid-link",
    methods=["POST"],
)
@require_session_and_db
def raid_link_create(
    playlist_id, api=None, user=None
):
    """Accept one owned collection creation/association without provider I/O."""
    req, error = validate_json(CreateRaidLinkRequest)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    return admit_asset(user, playlist_id, "raid_link_create", {
        "create_new": req.create_new,
        "target_playlist_name": data.get("target_playlist_name"),
        "asset_playlist_id": None if req.create_new else req.raid_playlist_id,
        "asset_playlist_name": None if req.create_new else data.get("raid_playlist_name"),
        "drip_count": req.drip_count, "drip_enabled": req.drip_enabled,
    })


@api_bp.route(
    "/playlist/<playlist_id>/raid-link",
    methods=["PUT"],
)
@require_auth_and_db
def raid_link_update(
    playlist_id, api=None, user=None
):
    """Update a raid playlist link."""
    req, err = validate_json(UpdateRaidLinkRequest)
    if err:
        return err

    try:
        update_fields = {}
        if req.drip_count is not None:
            update_fields["drip_count"] = req.drip_count
        if req.drip_enabled is not None:
            update_fields["drip_enabled"] = (
                req.drip_enabled
            )

        link = RaidLinkService.update_link(
            user.id, playlist_id, **update_fields
        )

        return json_success(
            "Raid link updated.",
            raid_link=link.to_dict(),
        )
    except RaidLinkNotFoundError:
        return json_error(
            "No raid link found", 404
        )
    except PlaylistBusyError:
        return json_reply(busy_error())
    except RaidLinkError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error(
            "Failed to update raid link: %s", e
        )
        return json_error(
            "Failed to update raid link", 500
        )


@api_bp.route(
    "/playlist/<playlist_id>/raid-link",
    methods=["DELETE"],
)
@require_auth_and_db
def raid_link_delete(
    playlist_id, api=None, user=None
):
    """Delete a raid playlist link."""
    try:
        RaidLinkService.delete_link(
            user.id, playlist_id
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_LINK_DELETE,
            description="Removed raid playlist link",
            playlist_id=playlist_id,
        )

        return json_success("Raid link removed.")
    except RaidLinkNotFoundError:
        return json_error(
            "No raid link found", 404
        )
    except PlaylistBusyError:
        return json_reply(busy_error())
    except RaidLinkError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error(
            "Failed to delete raid link: %s", e
        )
        return json_error(
            "Failed to delete raid link", 500
        )


# =============================================================
# Source raid_count update
# =============================================================


@api_bp.route(
    "/playlist/<playlist_id>/raid-source-count",
    methods=["PUT"],
)
@require_auth_and_db
def raid_source_count_update(
    playlist_id, api=None, user=None
):
    """Update a source's raid_count."""
    req, err = validate_json(
        UpdateSourceRaidCountRequest
    )
    if err:
        return err

    try:
        source = UpstreamSourceService.update_raid_count(
            user.id,
            playlist_id,
            req.source_id,
            req.raid_count,
        )
        return json_success(
            "Source raid count updated.",
            source=source.to_dict(),
        )
    except UpstreamSourceNotFoundError:
        return json_error("Source not found", 404)
    except PlaylistBusyError:
        return json_reply(busy_error())


# =============================================================
# Watch / Unwatch Sources
# =============================================================


@api_bp.route(
    "/playlist/<playlist_id>/raid-add-url",
    methods=["POST"],
)
@require_auth_and_db
def raid_add_url(playlist_id, api=None, user=None):
    """Add an external playlist as a raid source by URL."""
    req, err = validate_json(AddRaidUrlRequest)
    if err:
        return err

    # 1. Parse URL to playlist ID. The schema validator already
    # rejected unparseable URLs at the 400 boundary, so this call
    # is guaranteed to return a non-empty ID.
    source_playlist_id = parse_spotify_playlist_url(req.url)

    # 2. Guard: not self-referencing
    if source_playlist_id == playlist_id:
        return json_error(
            "Cannot raid from the same playlist", 400
        )

    # 3. Get playlist metadata for ownership check.
    # The service tries the API first and falls back to
    # the public embed page when the API answers 404.
    try:
        playlist_svc = PlaylistService(api)
        playlist_meta = playlist_svc.get_playlist_metadata(
            source_playlist_id
        )
    except PlaylistNotFoundError:
        return json_error(
            "Playlist not found. It may be private, "
            "deleted, or region-restricted.",
            404,
        )
    except Exception as e:
        logger.warning(
            "Could not fetch playlist metadata "
            "for %s: %s",
            source_playlist_id, e,
        )
        return json_error(
            "Could not access playlist", 400
        )

    # 4. Guard: owner is not current user (external-only).
    # Skip check if owner_id is unknown (embed-page fallback).
    owner_id = playlist_meta.get("owner_id", "unknown")
    if (
        owner_id != "unknown"
        and owner_id == user.spotify_id
    ):
        return json_error(
            "Cannot raid your own playlist. "
            "Use rotation instead.",
            400,
        )

    # 5. Best-effort track count
    track_count = playlist_meta.get("total_tracks")

    # 6. Register source
    data = request.get_json(silent=True)
    try:
        with ExitStack() as locks:
            # An auto-watch may edit a saved routine's source IDs. Take
            # execution before configuration, matching SchedulerService.
            if req.auto_schedule and not locks.enter_context(playlist_lock(playlist_id, timeout_s=2.0)):
                raise PlaylistBusyError("Playlist is busy; please retry.")
            if not locks.enter_context(playlist_configuration_lock(playlist_id, timeout_s=2.0)):
                raise PlaylistBusyError("Playlist is busy; please retry.")
            result = RaidSyncService.watch_playlist(
                spotify_id=user.spotify_id,
                target_playlist_id=playlist_id,
                target_playlist_name=data.get(
                    "target_playlist_name", playlist_id
                ),
                source_playlist_id=source_playlist_id,
                source_playlist_name=playlist_meta["name"],
                source_url=req.url,
                auto_schedule=req.auto_schedule,
                schedule_value=req.schedule_value,
                schedule_time=req.schedule_time,
                source_type="external",
                user=user,
            )

            # Update track count on source record
            if track_count is not None:
                UpstreamSourceService.update_track_count(
                    user_id=user.id,
                    target_playlist_id=playlist_id,
                    source_playlist_id=source_playlist_id,
                    track_count=track_count,
                )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_WATCH_ADD,
            description=(
                "Watching external: "
                "'{}'".format(playlist_meta["name"])
            ),
            playlist_id=playlist_id,
        )

        return json_success(
            "External source added.",
            source=result["source"],
            schedule=result["schedule"],
            track_count=track_count,
        )
    except (UpstreamSourceLimitError, RaidSyncError) as e:
        return json_error(str(e), 400)
    except (PlaylistBusyError, ScheduleBusyError):
        return json_reply(busy_error())
    except Exception as e:
        logger.error(
            "Failed to add external source: %s", e
        )
        return json_error(
            "Failed to add external source", 500
        )


@api_bp.route(
    "/playlist/<playlist_id>/raid-unwatch",
    methods=["POST"],
)
@require_auth_and_db
def raid_unwatch(playlist_id, api=None, user=None):
    """Unwatch a source playlist."""
    req, err = validate_json(UnwatchPlaylistRequest)
    if err:
        return err

    try:
        RaidSyncService.unwatch_playlist(
            spotify_id=user.spotify_id,
            source_id=req.source_id,
            target_playlist_id=playlist_id,
            user=user,
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_WATCH_REMOVE,
            description="Removed raid source",
            playlist_id=playlist_id,
        )

        return json_success("Source removed.")
    except UpstreamSourceNotFoundError:
        return json_error("Source not found.", 404)
    except RaidSyncError as e:
        return json_error(str(e), 400)
    except (PlaylistBusyError, ScheduleBusyError):
        return json_reply(busy_error())
    except Exception as e:
        logger.error("Failed to unwatch: %s", e)
        return json_error(
            "Failed to remove source", 500
        )


# =============================================================
# Raid / Drip Execution
# =============================================================


def _live_result_response(result, success_message, *, message=None):
    """Forward verified facts without treating a returned dict as success.

    An incomplete outcome keeps ``message``, else a reviewed rejection's own
    copy, else the generic copy for its outcome.
    """
    payload = {key: value for key, value in result.items()
               if key not in {"http_status", "message", "error", "success",
                              "category"}}
    operation = payload["operation"] = normalized_operation(result, "unknown")
    if operation.get("aggregate_status") == "complete":
        return json_success(success_message, **payload)
    message = message or review_message(result) or (
        "This action could not be verified. Review the playlist results "
        "before running another action."
        if operation.get("aggregate_status") == "unknown"
        else "This action did not finish. Review the playlist results "
             "before running another action."
    )
    return jsonify({
        **payload, "success": False, "category": "error", "message": message,
        "error": message,
    }), error_status(result)


def _live_preflight_error(message, status_code):
    kind = LIVE_ENDPOINT_KINDS.get(request.endpoint, "raid")
    return _live_result_response(
        rejected_result(
            kind, error_code=preflight_error_code(status_code),
            http_status=status_code,
        ),
        "", message=message,
    )


def _live_review(kind):
    """Return a validated optional JSON binding before any local capture."""
    # A malformed binding is refused before any read, never run as legacy.
    try:
        binding = request_binding(request, kind)
    except ReviewBindingError:
        return None, _live_result_response(invalid_binding_result(kind), "")
    return binding, None


def _live_raid_request(kind):
    # Empty POSTs remain valid for the existing all-sources controls.
    data = request.get_json(silent=True)
    if data is None and not request.data:
        data = {}
    if not isinstance(data, dict):
        return None, None, _live_preflight_error(
            "Request body must be a JSON object.", 400,
        )
    # Before RaidNowRequest, whose extra="ignore" would drop the binding.
    review, error = _live_review(kind)
    if error:
        return None, None, error
    try:
        return RaidNowRequest(**data), review, None
    except ValidationError:
        return None, None, _live_preflight_error(
            "Choose valid raid sources, or use all configured sources.", 400,
        )


@api_bp.route(
    "/playlist/<playlist_id>/raid-now",
    methods=["POST"],
)
@require_session_and_db(on_preflight_error=_live_preflight_error)
def raid_now(playlist_id, api=None, user=None):
    """Collect discoveries for review, regardless of saved routine type."""
    req, review, error = _live_raid_request("raid")
    if error:
        return error
    return admit_direct(user, playlist_id, "raid", source_playlist_ids=req.source_playlist_ids,
                        reviewed=review, on_rejection=_live_preflight_error)


@api_bp.route(
    "/playlist/<playlist_id>/drip-now",
    methods=["POST"],
)
@require_session_and_db(on_preflight_error=_live_preflight_error)
def drip_now(playlist_id, api=None, user=None):
    """Trigger an immediate drip from raid playlist."""
    review, error = _live_review("drip")
    if error:
        return error
    return admit_direct(user, playlist_id, "drip", reviewed=review, on_rejection=_live_preflight_error)


@api_bp.route(
    "/playlist/<playlist_id>/raid-and-drip",
    methods=["POST"],
)
@require_session_and_db(on_preflight_error=_live_preflight_error)
def raid_and_drip(playlist_id, api=None, user=None):
    """Accept one composite invocation with separate durable child outcomes."""
    req, review, error = _live_raid_request("raid_and_drip")
    if error:
        return error
    return admit_direct(user, playlist_id, "raid_and_drip", source_playlist_ids=req.source_playlist_ids,
                        reviewed=review, on_rejection=_live_preflight_error)


# =============================================================
# Pending Raid Tracks (Track Inbox)
# =============================================================


@api_bp.route(
    "/playlist/<playlist_id>/pending-raids",
    methods=["GET"],
)
@require_auth_and_db
def pending_raids_list(
    playlist_id, api=None, user=None
):
    """List pending raid tracks for a playlist."""
    tracks = PendingRaidService.list_pending(
        user.id, playlist_id
    )
    return json_success(
        "Pending raids loaded",
        tracks=[t.to_dict() for t in tracks],
    )


@api_bp.route(
    "/playlist/<playlist_id>/pending-raids/dismiss",
    methods=["POST"],
)
@require_session_and_db
def pending_raids_dismiss(playlist_id, user=None):
    """Accept the owned pending intersection without Spotify authentication."""
    req, err = validate_json(DismissTracksRequest)
    if err:
        return err
    from shuffify.routes.pending_dismiss_queue import admit_pending_dismiss

    return admit_pending_dismiss(user, playlist_id, track_ids=req.track_ids)


@api_bp.route(
    "/playlist/<playlist_id>/pending-raids/dismiss-all",
    methods=["POST"],
)
@require_session_and_db
def pending_raids_dismiss_all(playlist_id, user=None):
    """Accept all currently owned pending IDs, including an explicit empty set."""
    from shuffify.routes.pending_dismiss_queue import admit_pending_dismiss

    return admit_pending_dismiss(user, playlist_id)


def _raid_finalize_preflight_error(message, status_code):
    result = WorkshopFinalizeResult.request_rejected(
        request.get_json(silent=True), message, status_code, "promoted_count"
    )
    return jsonify(result.to_dict()), result.http_status


@api_bp.route(
    "/playlist/<playlist_id>/pending-raids/finalize",
    methods=["POST"],
)
@require_session_and_db(on_preflight_error=_raid_finalize_preflight_error)
def pending_raids_finalize(playlist_id, user=None):
    """Finalize exactly the captured IDs after verified target/source checks."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _raid_finalize_preflight_error("Request body must be a JSON object.", 400)
    try:
        req = FinalizeRaidRequest(**data)
    except ValidationError:
        return _raid_finalize_preflight_error(
            "Review the source changes again; the captured task is incomplete or invalid.", 400
        )
    if not req.inspect_only:
        return admit_finalizer(user, playlist_id, "workshop_finalize_raids", req)
    from shuffify.routes import require_auth

    api = require_auth()
    if api is None:
        return _raid_finalize_preflight_error("Please log in first.", 401)
    result = WorkshopFinalizeService(api).finalize_raids(playlist_id, user, **req.model_dump())
    return jsonify(result.to_dict()), result.http_status
