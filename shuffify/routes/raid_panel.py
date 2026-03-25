"""
Raid panel routes.

Smart raid management: watch/unwatch sources, trigger raids,
manage raid schedules, raid playlist links, and drip operations.
"""

import logging

from flask import request

from shuffify.routes import (
    main,
    require_auth_and_db,
    json_error,
    json_success,
    log_activity,
    validate_json,
)
from shuffify.services.raid_sync_service import (
    RaidSyncService,
    RaidSyncError,
)
from shuffify.services.raid_link_service import (
    RaidLinkService,
    RaidLinkError,
    RaidLinkExistsError,
    RaidLinkNotFoundError,
)
from shuffify.services.upstream_source_service import (
    UpstreamSourceService,
    UpstreamSourceNotFoundError,
    UpstreamSourceLimitError,
)
from shuffify.services.scheduler_service import (
    SchedulerService,
)
from shuffify.services.playlist_service import (
    PlaylistService,
    PlaylistNotFoundError,
)
from shuffify.spotify.url_parser import (
    parse_spotify_playlist_url,
)
from shuffify.enums import ActivityType
from shuffify.schemas.raid_requests import (
    WatchPlaylistRequest,
    WatchSearchQueryRequest,
    AddRaidUrlRequest,
    UnwatchPlaylistRequest,
    RaidNowRequest,
    UpdateRaidScheduleRequest,
)
from shuffify.schemas.pending_raid_requests import (
    PromoteTracksRequest,
    DismissTracksRequest,
)
from shuffify.schemas.raid_link_requests import (
    CreateRaidLinkRequest,
    UpdateRaidLinkRequest,
    UpdateSourceRaidCountRequest,
)
from shuffify.services.pending_raid_service import (
    PendingRaidService,
)

logger = logging.getLogger(__name__)


# =============================================================
# Raid Status
# =============================================================


@main.route(
    "/playlist/<playlist_id>/raid-status",
    methods=["GET"],
)
@require_auth_and_db
def raid_status(playlist_id, client=None, user=None):
    """Get raid panel status for a playlist."""
    status = RaidSyncService.get_raid_status(
        user.spotify_id, playlist_id
    )
    return json_success(
        "Raid status loaded", raid_status=status
    )


# =============================================================
# Raid Playlist Link CRUD
# =============================================================


@main.route(
    "/playlist/<playlist_id>/raid-link",
    methods=["POST"],
)
@require_auth_and_db
def raid_link_create(
    playlist_id, client=None, user=None
):
    """Create a raid playlist link."""
    req, err = validate_json(CreateRaidLinkRequest)
    if err:
        return err

    data = request.get_json(silent=True) or {}

    # Check for existing link BEFORE creating a Spotify
    # playlist to prevent orphaned playlists on 409.
    existing = RaidLinkService.get_link_for_playlist(
        user.id, playlist_id
    )
    if existing:
        return json_success(
            "Raid playlist already linked.",
            raid_link=existing.to_dict(),
        )

    try:
        if req.create_new:
            target_name = data.get(
                "target_playlist_name", playlist_id
            )
            raid_id, raid_name = (
                RaidLinkService.create_raid_playlist(
                    client.api,
                    user.spotify_id,
                    target_name,
                )
            )
        else:
            raid_id = req.raid_playlist_id
            raid_name = data.get(
                "raid_playlist_name", raid_id
            )

        link = RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id=playlist_id,
            raid_playlist_id=raid_id,
            target_playlist_name=data.get(
                "target_playlist_name"
            ),
            raid_playlist_name=raid_name,
            drip_count=req.drip_count,
            drip_enabled=req.drip_enabled,
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_LINK_CREATE,
            description=(
                "Created raid link: "
                "'{}'".format(raid_name)
            ),
            playlist_id=playlist_id,
        )

        return json_success(
            "Raid playlist linked.",
            raid_link=link.to_dict(),
        )
    except RaidLinkExistsError as e:
        return json_error(str(e), 409)
    except RaidLinkError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error(
            "Failed to create raid link: %s", e
        )
        return json_error(
            "Failed to create raid link", 500
        )


@main.route(
    "/playlist/<playlist_id>/raid-link",
    methods=["PUT"],
)
@require_auth_and_db
def raid_link_update(
    playlist_id, client=None, user=None
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
    except RaidLinkError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error(
            "Failed to update raid link: %s", e
        )
        return json_error(
            "Failed to update raid link", 500
        )


@main.route(
    "/playlist/<playlist_id>/raid-link",
    methods=["DELETE"],
)
@require_auth_and_db
def raid_link_delete(
    playlist_id, client=None, user=None
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


@main.route(
    "/playlist/<playlist_id>/raid-source-count",
    methods=["PUT"],
)
@require_auth_and_db
def raid_source_count_update(
    playlist_id, client=None, user=None
):
    """Update a source's raid_count."""
    req, err = validate_json(
        UpdateSourceRaidCountRequest
    )
    if err:
        return err

    try:
        source = UpstreamSourceService.update_raid_count(
            user.id, req.source_id, req.raid_count
        )
        return json_success(
            "Source raid count updated.",
            source=source.to_dict(),
        )
    except UpstreamSourceNotFoundError:
        return json_error("Source not found", 404)


# =============================================================
# Watch / Unwatch Sources
# =============================================================


@main.route(
    "/playlist/<playlist_id>/raid-watch",
    methods=["POST"],
)
@require_auth_and_db
def raid_watch(playlist_id, client=None, user=None):
    """Watch a playlist as a raid source."""
    req, err = validate_json(WatchPlaylistRequest)
    if err:
        return err

    data = request.get_json(silent=True)
    try:
        result = RaidSyncService.watch_playlist(
            spotify_id=user.spotify_id,
            target_playlist_id=playlist_id,
            target_playlist_name=data.get(
                "target_playlist_name", playlist_id
            ),
            source_playlist_id=req.source_playlist_id,
            source_playlist_name=req.source_playlist_name,
            source_url=req.source_url,
            auto_schedule=req.auto_schedule,
            schedule_value=req.schedule_value,
            schedule_time=req.schedule_time,
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_WATCH_ADD,
            description=(
                "Watching '"
                "{}'"
                .format(
                    req.source_playlist_name
                    or req.source_playlist_id
                )
            ),
            playlist_id=playlist_id,
        )

        return json_success(
            "Source watched.",
            source=result["source"],
            schedule=result["schedule"],
        )
    except RaidSyncError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error("Failed to watch playlist: %s", e)
        return json_error(
            "Failed to watch playlist", 500
        )


@main.route(
    "/playlist/<playlist_id>/raid-watch-search",
    methods=["POST"],
)
@require_auth_and_db
def raid_watch_search(
    playlist_id, client=None, user=None
):
    """Watch a search query as a raid source."""
    req, err = validate_json(WatchSearchQueryRequest)
    if err:
        return err

    data = request.get_json(silent=True)
    try:
        result = RaidSyncService.watch_search_query(
            spotify_id=user.spotify_id,
            target_playlist_id=playlist_id,
            target_playlist_name=data.get(
                "target_playlist_name", playlist_id
            ),
            search_query=req.search_query,
            source_name=req.source_name,
            auto_schedule=req.auto_schedule,
            schedule_value=req.schedule_value,
            schedule_time=req.schedule_time,
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_WATCH_ADD,
            description=(
                "Watching search: "
                "'{}'".format(req.search_query)
            ),
            playlist_id=playlist_id,
        )

        return json_success(
            "Search source watched.",
            source=result["source"],
            schedule=result["schedule"],
        )
    except RaidSyncError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error(
            "Failed to watch search query: %s", e
        )
        return json_error(
            "Failed to watch search query", 500
        )


@main.route(
    "/playlist/<playlist_id>/raid-add-url",
    methods=["POST"],
)
@require_auth_and_db
def raid_add_url(playlist_id, client=None, user=None):
    """Add an external playlist as a raid source by URL."""
    req, err = validate_json(AddRaidUrlRequest)
    if err:
        return err

    # 1. Parse URL to playlist ID
    source_playlist_id = parse_spotify_playlist_url(
        req.url
    )
    if not source_playlist_id:
        return json_error(
            "Invalid Spotify playlist URL", 400
        )

    # 2. Guard: not self-referencing
    if source_playlist_id == playlist_id:
        return json_error(
            "Cannot raid from the same playlist", 400
        )

    # 3. Get playlist metadata for ownership check.
    # Service handles API-first with scraper fallback
    # for non-owned playlists restricted since Feb 2026.
    try:
        playlist_svc = PlaylistService(client)
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
    # Skip check if owner_id is unknown (scraped metadata).
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
        )

        # Update track count on source record
        if track_count is not None:
            from shuffify.models.db import (
                db,
                UpstreamSource,
            )
            src = UpstreamSource.query.filter_by(
                user_id=user.id,
                target_playlist_id=playlist_id,
                source_playlist_id=source_playlist_id,
            ).first()
            if src:
                src.last_track_count = track_count
                db.session.commit()

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
    except UpstreamSourceLimitError as e:
        return json_error(str(e), 400)
    except RaidSyncError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error(
            "Failed to add external source: %s", e
        )
        return json_error(
            "Failed to add external source", 500
        )


@main.route(
    "/playlist/<playlist_id>/raid-unwatch",
    methods=["POST"],
)
@require_auth_and_db
def raid_unwatch(playlist_id, client=None, user=None):
    """Unwatch a source playlist."""
    req, err = validate_json(UnwatchPlaylistRequest)
    if err:
        return err

    try:
        RaidSyncService.unwatch_playlist(
            spotify_id=user.spotify_id,
            source_id=req.source_id,
            target_playlist_id=playlist_id,
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
    except Exception as e:
        logger.error("Failed to unwatch: %s", e)
        return json_error(
            "Failed to remove source", 500
        )


# =============================================================
# Raid / Drip Execution
# =============================================================


@main.route(
    "/playlist/<playlist_id>/raid-now",
    methods=["POST"],
)
@require_auth_and_db
def raid_now(playlist_id, client=None, user=None):
    """Trigger an immediate raid."""
    data = request.get_json(silent=True) or {}
    req = RaidNowRequest(**data)

    try:
        result = RaidSyncService.raid_now(
            spotify_id=user.spotify_id,
            target_playlist_id=playlist_id,
            source_playlist_ids=req.source_playlist_ids,
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_SYNC_NOW,
            description=(
                "Raid: {} tracks added".format(
                    result.get("tracks_added", 0)
                )
            ),
            playlist_id=playlist_id,
            metadata={
                "tracks_added": result.get(
                    "tracks_added", 0
                ),
            },
        )

        return json_success(
            "Raid complete: {} new tracks added.".format(
                result.get("tracks_added", 0)
            ),
            tracks_added=result.get("tracks_added", 0),
            tracks_total=result.get("tracks_total", 0),
        )
    except RaidSyncError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error("Failed to execute raid: %s", e)
        return json_error(
            "Failed to execute raid", 500
        )


@main.route(
    "/playlist/<playlist_id>/drip-now",
    methods=["POST"],
)
@require_auth_and_db
def drip_now(playlist_id, client=None, user=None):
    """Trigger an immediate drip from raid playlist."""
    try:
        result = RaidSyncService.drip_now(
            spotify_id=user.spotify_id,
            target_playlist_id=playlist_id,
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_DRIP,
            description=(
                "Drip: {} tracks moved".format(
                    result.get("tracks_added", 0)
                )
            ),
            playlist_id=playlist_id,
            metadata={
                "tracks_added": result.get(
                    "tracks_added", 0
                ),
            },
        )

        return json_success(
            "Drip complete: {} tracks moved.".format(
                result.get("tracks_added", 0)
            ),
            tracks_added=result.get("tracks_added", 0),
            tracks_total=result.get("tracks_total", 0),
        )
    except RaidSyncError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error("Failed to execute drip: %s", e)
        return json_error(
            "Failed to execute drip", 500
        )


@main.route(
    "/playlist/<playlist_id>/raid-and-drip",
    methods=["POST"],
)
@require_auth_and_db
def raid_and_drip(
    playlist_id, client=None, user=None
):
    """Raid sources then drip into target in one step."""
    data = request.get_json(silent=True) or {}
    req = RaidNowRequest(**data)

    try:
        raid_result = RaidSyncService.raid_now(
            spotify_id=user.spotify_id,
            target_playlist_id=playlist_id,
            source_playlist_ids=req.source_playlist_ids,
        )
    except RaidSyncError as e:
        return json_error(
            "Raid step failed: {}".format(e), 400
        )
    except Exception as e:
        logger.error("Raid step failed: %s", e)
        return json_error(
            "Raid step failed", 500
        )

    try:
        drip_result = RaidSyncService.drip_now(
            spotify_id=user.spotify_id,
            target_playlist_id=playlist_id,
        )
    except RaidSyncError as e:
        return json_error(
            "Drip step failed: {}".format(e), 400
        )
    except Exception as e:
        logger.error("Drip step failed: %s", e)
        return json_error(
            "Drip step failed", 500
        )

    raided = raid_result.get("tracks_added", 0)
    dripped = drip_result.get("tracks_added", 0)

    log_activity(
        user_id=user.id,
        activity_type=ActivityType.RAID_SYNC_NOW,
        description=(
            "Raid & Drip: {} raided, "
            "{} dripped".format(raided, dripped)
        ),
        playlist_id=playlist_id,
        metadata={
            "tracks_raided": raided,
            "tracks_dripped": dripped,
        },
    )

    return json_success(
        "Raid & Drip: {} raided, {} added "
        "to playlist.".format(raided, dripped),
        tracks_raided=raided,
        tracks_dripped=dripped,
    )


# =============================================================
# Raid Schedule Management
# =============================================================


def _toggle_schedule(schedule, user_id, playlist_id, label):
    """Shared toggle logic for raid and drip schedules."""
    try:
        schedule = SchedulerService.toggle_schedule(
            schedule.id, user_id
        )

        try:
            if schedule.is_enabled:
                from shuffify.scheduler import (
                    add_job_for_schedule,
                )
                add_job_for_schedule(schedule)
            else:
                from shuffify.scheduler import (
                    remove_job_for_schedule,
                )
                remove_job_for_schedule(schedule.id)
        except Exception as e:
            logger.warning(
                "APScheduler toggle failed: %s", e
            )

        state = (
            "enabled"
            if schedule.is_enabled
            else "disabled"
        )
        log_activity(
            user_id=user_id,
            activity_type=ActivityType.SCHEDULE_TOGGLE,
            description="{} schedule {}".format(
                label, state
            ),
            playlist_id=playlist_id,
        )

        return json_success(
            "{} schedule {}.".format(label, state),
            schedule=schedule.to_dict(),
        )
    except Exception as e:
        logger.error(
            "Failed to toggle %s schedule: %s",
            label.lower(), e,
        )
        return json_error(
            "Failed to toggle {} schedule".format(
                label.lower()
            ),
            500,
        )


@main.route(
    "/playlist/<playlist_id>/raid-schedule-toggle",
    methods=["POST"],
)
@require_auth_and_db
def raid_schedule_toggle(
    playlist_id, client=None, user=None
):
    """Toggle the raid schedule on/off."""
    schedule = RaidSyncService._find_raid_schedule(
        user.id, playlist_id
    )
    if not schedule:
        return json_error("No raid schedule found", 404)
    return _toggle_schedule(
        schedule, user.id, playlist_id, "Raid"
    )


@main.route(
    "/playlist/<playlist_id>/drip-schedule-toggle",
    methods=["POST"],
)
@require_auth_and_db
def drip_schedule_toggle(
    playlist_id, client=None, user=None
):
    """Toggle the drip schedule on/off."""
    schedule = RaidSyncService._find_drip_schedule(
        user.id, playlist_id
    )
    if not schedule:
        return json_error("No drip schedule found", 404)
    return _toggle_schedule(
        schedule, user.id, playlist_id, "Drip"
    )


@main.route(
    "/playlist/<playlist_id>/raid-schedule",
    methods=["PUT"],
)
@require_auth_and_db
def raid_schedule_update(
    playlist_id, client=None, user=None
):
    """Update a raid schedule (frequency, time, enabled)."""
    req, err = validate_json(UpdateRaidScheduleRequest)
    if err:
        return err

    try:
        schedule = RaidSyncService.update_raid_schedule(
            spotify_id=user.spotify_id,
            target_playlist_id=playlist_id,
            schedule_value=req.schedule_value,
            schedule_time=req.schedule_time,
            is_enabled=req.is_enabled,
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.SCHEDULE_TOGGLE,
            description="Updated raid schedule",
            playlist_id=playlist_id,
        )

        return json_success(
            "Schedule updated.",
            schedule=schedule.to_dict(),
        )
    except RaidSyncError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error(
            "Failed to update raid schedule: %s", e
        )
        return json_error(
            "Failed to update schedule", 500
        )


@main.route(
    "/playlist/<playlist_id>/raid-schedule/history",
    methods=["GET"],
)
@require_auth_and_db
def raid_schedule_history(
    playlist_id, client=None, user=None
):
    """Get execution history for a raid schedule."""
    schedule = RaidSyncService._find_raid_schedule(
        user.id, playlist_id
    )
    if not schedule:
        return json_error("No raid schedule found", 404)

    try:
        history = SchedulerService.get_execution_history(
            schedule.id, user.id, limit=10
        )
        return json_success(
            "History loaded.",
            history=history,
        )
    except Exception as e:
        logger.error(
            "Failed to load raid history: %s", e
        )
        return json_error(
            "Failed to load history", 500
        )


# =============================================================
# Pending Raid Tracks (Track Inbox)
# =============================================================


def _remove_from_raid_playlist(
    api, user_id, playlist_id, uris,
):
    """Remove tracks from the raid Spotify playlist
    if a RaidPlaylistLink exists."""
    link = RaidLinkService.get_link_for_playlist(
        user_id, playlist_id
    )
    if not link:
        return

    try:
        api.playlist_remove_items(
            link.raid_playlist_id, uris
        )
    except Exception as e:
        logger.warning(
            "Failed to remove tracks from raid "
            "playlist: %s", e
        )


@main.route(
    "/playlist/<playlist_id>/pending-raids",
    methods=["GET"],
)
@require_auth_and_db
def pending_raids_list(
    playlist_id, client=None, user=None
):
    """List pending raid tracks for a playlist."""
    tracks = PendingRaidService.list_pending(
        user.id, playlist_id
    )
    return json_success(
        "Pending raids loaded",
        tracks=[t.to_dict() for t in tracks],
    )


@main.route(
    "/playlist/<playlist_id>/pending-raids/promote",
    methods=["POST"],
)
@require_auth_and_db
def pending_raids_promote(
    playlist_id, client=None, user=None
):
    """Promote selected pending tracks."""
    req, err = validate_json(PromoteTracksRequest)
    if err:
        return err

    promoted = PendingRaidService.promote_tracks(
        user.id, playlist_id, req.track_ids
    )

    if promoted:
        uris = [t.track_uri for t in promoted]
        try:
            # Add to target playlist
            client.api.playlist_add_items(
                playlist_id, uris
            )
            # Remove from raid playlist
            _remove_from_raid_playlist(
                client.api, user.id,
                playlist_id, uris,
            )
        except Exception as e:
            logger.error(
                "Failed to add promoted tracks "
                "to Spotify: %s",
                e,
            )
            return json_error(
                "Failed to add tracks to Spotify",
                500,
            )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_PROMOTE,
            description=(
                "Promoted {} raided tracks".format(
                    len(promoted)
                )
            ),
            playlist_id=playlist_id,
        )

    return json_success(
        "{} tracks promoted.".format(len(promoted)),
        promoted_count=len(promoted),
    )


@main.route(
    "/playlist/<playlist_id>/pending-raids/dismiss",
    methods=["POST"],
)
@require_auth_and_db
def pending_raids_dismiss(
    playlist_id, client=None, user=None
):
    """Dismiss selected pending tracks."""
    req, err = validate_json(DismissTracksRequest)
    if err:
        return err

    # Get tracks before dismissing for raid playlist sync
    from shuffify.models.db import PendingRaidTrack
    from shuffify.enums import PendingRaidStatus

    tracks = PendingRaidTrack.query.filter(
        PendingRaidTrack.id.in_(req.track_ids),
        PendingRaidTrack.user_id == user.id,
        PendingRaidTrack.target_playlist_id
        == playlist_id,
        PendingRaidTrack.status
        == PendingRaidStatus.PENDING,
    ).all()
    uris_to_remove = [t.track_uri for t in tracks]

    count = PendingRaidService.dismiss_tracks(
        user.id, playlist_id, req.track_ids
    )

    # Remove from raid playlist
    if uris_to_remove:
        _remove_from_raid_playlist(
            client.api, user.id,
            playlist_id, uris_to_remove,
        )

    log_activity(
        user_id=user.id,
        activity_type=ActivityType.RAID_DISMISS,
        description=(
            "Dismissed {} raided tracks".format(count)
        ),
        playlist_id=playlist_id,
    )

    return json_success(
        "{} tracks dismissed.".format(count),
        dismissed_count=count,
    )


@main.route(
    "/playlist/<playlist_id>/pending-raids/promote-all",
    methods=["POST"],
)
@require_auth_and_db
def pending_raids_promote_all(
    playlist_id, client=None, user=None
):
    """Promote all pending tracks."""
    promoted = PendingRaidService.promote_all(
        user.id, playlist_id
    )

    if promoted:
        uris = [t.track_uri for t in promoted]
        try:
            client.api.playlist_add_items(
                playlist_id, uris
            )
            _remove_from_raid_playlist(
                client.api, user.id,
                playlist_id, uris,
            )
        except Exception as e:
            logger.error(
                "Failed to add promoted tracks "
                "to Spotify: %s",
                e,
            )
            return json_error(
                "Failed to add tracks to Spotify",
                500,
            )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_PROMOTE,
            description=(
                "Promoted all {} "
                "raided tracks".format(len(promoted))
            ),
            playlist_id=playlist_id,
        )

    return json_success(
        "{} tracks promoted.".format(len(promoted)),
        promoted_count=len(promoted),
    )


@main.route(
    "/playlist/<playlist_id>/pending-raids/dismiss-all",
    methods=["POST"],
)
@require_auth_and_db
def pending_raids_dismiss_all(
    playlist_id, client=None, user=None
):
    """Dismiss all pending tracks."""
    # Get URIs before dismissing for raid playlist sync
    pending = PendingRaidService.list_pending(
        user.id, playlist_id
    )
    uris_to_remove = [t.track_uri for t in pending]

    count = PendingRaidService.dismiss_all(
        user.id, playlist_id
    )

    # Remove from raid playlist
    if uris_to_remove:
        _remove_from_raid_playlist(
            client.api, user.id,
            playlist_id, uris_to_remove,
        )

    log_activity(
        user_id=user.id,
        activity_type=ActivityType.RAID_DISMISS,
        description=(
            "Dismissed all {} raided tracks".format(count)
        ),
        playlist_id=playlist_id,
    )

    return json_success(
        "{} tracks dismissed.".format(count),
        dismissed_count=count,
    )
