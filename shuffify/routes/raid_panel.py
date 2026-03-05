"""
Raid panel routes.

Smart raid management: watch/unwatch sources, trigger raids,
and manage raid schedules from the workshop sidebar.
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
from shuffify.services.upstream_source_service import (
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
)
from shuffify.schemas.pending_raid_requests import (
    PromoteTracksRequest,
    DismissTracksRequest,
)
from shuffify.services.pending_raid_service import (
    PendingRaidService,
)

logger = logging.getLogger(__name__)


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
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_WATCH_ADD,
            description=(
                f"Watching '"
                f"{req.source_playlist_name or req.source_playlist_id}'"
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
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_WATCH_ADD,
            description=(
                f"Watching search: "
                f"'{req.search_query}'"
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
    source_playlist_id = parse_spotify_playlist_url(req.url)
    if not source_playlist_id:
        return json_error(
            "Invalid Spotify playlist URL", 400
        )

    # 2. Guard: not self-referencing
    if source_playlist_id == playlist_id:
        return json_error(
            "Cannot raid from the same playlist", 400
        )

    # 3. Get playlist metadata for ownership check
    try:
        playlist_svc = PlaylistService(client)
        playlist_info = playlist_svc.get_playlist(
            source_playlist_id
        )
    except PlaylistNotFoundError:
        return json_error("Playlist not found", 404)
    except Exception as e:
        logger.warning(
            "Could not fetch playlist metadata for %s: %s",
            source_playlist_id, e,
        )
        return json_error(
            "Could not access playlist", 400
        )

    # 4. Guard: owner is not current user (external-only)
    if playlist_info.owner_id == user.spotify_id:
        return json_error(
            "Cannot raid your own playlist. "
            "Use rotation instead.",
            400,
        )

    # 5. Best-effort track count via playlist metadata
    track_count = playlist_info.total_tracks

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
            source_playlist_name=playlist_info.name,
            source_url=req.url,
            auto_schedule=req.auto_schedule,
            schedule_value=req.schedule_value,
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
                f"Watching external: "
                f"'{playlist_info.name}'"
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
                f"Raid: {result.get('tracks_added', 0)}"
                f" tracks added"
            ),
            playlist_id=playlist_id,
            metadata={
                "tracks_added": result.get(
                    "tracks_added", 0
                ),
            },
        )

        return json_success(
            f"Raid complete: "
            f"{result.get('tracks_added', 0)} "
            f"new tracks added.",
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

    try:
        schedule = SchedulerService.toggle_schedule(
            schedule.id, user.id
        )

        # Update APScheduler
        try:
            if schedule.is_enabled:
                from shuffify.scheduler import (
                    add_job_for_schedule,
                )
                from flask import current_app
                add_job_for_schedule(
                    schedule,
                    current_app._get_current_object(),
                )
            else:
                from shuffify.scheduler import (
                    remove_job_for_schedule,
                )
                remove_job_for_schedule(schedule.id)
        except Exception as e:
            logger.warning(
                "APScheduler toggle failed: %s", e
            )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.SCHEDULE_TOGGLE,
            description=(
                f"Raid schedule "
                f"{'enabled' if schedule.is_enabled else 'disabled'}"
            ),
            playlist_id=playlist_id,
        )

        return json_success(
            f"Schedule "
            f"{'enabled' if schedule.is_enabled else 'disabled'}.",
            schedule=schedule.to_dict(),
        )
    except Exception as e:
        logger.error(
            "Failed to toggle schedule: %s", e
        )
        return json_error(
            "Failed to toggle schedule", 500
        )


# =============================================================
# Pending Raid Tracks (Track Inbox)
# =============================================================


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
        # Add tracks to Spotify playlist
        uris = [t.track_uri for t in promoted]
        try:
            client.api.playlist_add_items(
                playlist_id, uris
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
                f"Promoted {len(promoted)} "
                f"raided tracks"
            ),
            playlist_id=playlist_id,
        )

    return json_success(
        f"{len(promoted)} tracks promoted.",
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

    count = PendingRaidService.dismiss_tracks(
        user.id, playlist_id, req.track_ids
    )

    log_activity(
        user_id=user.id,
        activity_type=ActivityType.RAID_DISMISS,
        description=(
            f"Dismissed {count} raided tracks"
        ),
        playlist_id=playlist_id,
    )

    return json_success(
        f"{count} tracks dismissed.",
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
                f"Promoted all {len(promoted)} "
                f"raided tracks"
            ),
            playlist_id=playlist_id,
        )

    return json_success(
        f"{len(promoted)} tracks promoted.",
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
    count = PendingRaidService.dismiss_all(
        user.id, playlist_id
    )

    log_activity(
        user_id=user.id,
        activity_type=ActivityType.RAID_DISMISS,
        description=(
            f"Dismissed all {count} raided tracks"
        ),
        playlist_id=playlist_id,
    )

    return json_success(
        f"{count} tracks dismissed.",
        dismissed_count=count,
    )
