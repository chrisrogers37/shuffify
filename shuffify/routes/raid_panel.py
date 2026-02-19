"""
Raid panel routes.

Smart raid management: watch/unwatch sources, trigger raids,
and manage raid schedules from the workshop sidebar.
"""

import logging

from flask import request
from pydantic import ValidationError

from shuffify.routes import (
    main,
    require_auth_and_db,
    json_error,
    json_success,
    log_activity,
)
from shuffify.services.raid_sync_service import (
    RaidSyncService,
    RaidSyncError,
)
from shuffify.services.upstream_source_service import (
    UpstreamSourceNotFoundError,
)
from shuffify.services.scheduler_service import (
    SchedulerService,
    ScheduleLimitError,
)
from shuffify.enums import ActivityType
from shuffify.schemas.raid_requests import (
    WatchPlaylistRequest,
    UnwatchPlaylistRequest,
    RaidNowRequest,
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
    data = request.get_json(silent=True)
    if not data:
        return json_error("JSON body required", 400)

    try:
        req = WatchPlaylistRequest(**data)
    except ValidationError as e:
        return json_error(str(e.errors()[0]["msg"]), 400)

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
    except ScheduleLimitError:
        return json_error("Schedule limit reached.", 400)
    except RaidSyncError as e:
        return json_error(str(e), 400)
    except Exception as e:
        logger.error("Failed to watch playlist: %s", e)
        return json_error(
            "Failed to watch playlist", 500
        )


@main.route(
    "/playlist/<playlist_id>/raid-unwatch",
    methods=["POST"],
)
@require_auth_and_db
def raid_unwatch(playlist_id, client=None, user=None):
    """Unwatch a source playlist."""
    data = request.get_json(silent=True)
    if not data:
        return json_error("JSON body required", 400)

    try:
        req = UnwatchPlaylistRequest(**data)
    except ValidationError as e:
        return json_error(str(e.errors()[0]["msg"]), 400)

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
    try:
        req = RaidNowRequest(**data)
    except ValidationError as e:
        return json_error(str(e.errors()[0]["msg"]), 400)

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
