"""
Drip executor: move tracks from raid playlist into target playlist.

Selects random tracks from the raid playlist, adds them to the top
of the target playlist, and removes them from the raid playlist.
PendingRaidTrack records are updated to PROMOTED status.
"""

import logging
import random
from datetime import datetime, timezone

from shuffify.models.db import Schedule
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.enums import SnapshotType, PendingRaidStatus
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)

logger = logging.getLogger(__name__)


def execute_drip(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """
    Move tracks from raid playlist to the top of the
    target playlist.
    """
    from shuffify.services.executors.base_executor import (
        JobExecutionError,
    )
    from shuffify.services.raid_link_service import (
        RaidLinkService,
    )

    target_id = schedule.target_playlist_id
    user_id = schedule.user_id

    link = RaidLinkService.get_link_for_playlist(
        user_id, target_id
    )
    if not link:
        raise JobExecutionError(
            "No raid playlist link found for "
            "playlist {}. Create a raid link "
            "first.".format(target_id)
        )

    if not link.drip_enabled:
        logger.info(
            "Schedule %s: drip disabled for %s, "
            "skipping",
            schedule.id, target_id,
        )
        target_tracks = api.get_playlist_tracks(target_id)
        return {
            "tracks_added": 0,
            "tracks_total": len(target_tracks),
            "skipped_reason": "drip_disabled",
        }

    params = schedule.algorithm_params or {}
    drip_count = params.get(
        "drip_count", link.drip_count
    )
    drip_count = max(1, int(drip_count))

    try:
        raid_id = link.raid_playlist_id

        # Fetch raid playlist tracks
        raid_tracks = api.get_playlist_tracks(raid_id)
        raid_uris = [
            t["uri"]
            for t in raid_tracks
            if t.get("uri")
        ]

        if not raid_uris:
            logger.info(
                "Schedule %s: raid playlist %s is "
                "empty, nothing to drip",
                schedule.id, raid_id,
            )
            target_tracks = api.get_playlist_tracks(
                target_id
            )
            return {
                "tracks_added": 0,
                "tracks_total": len(target_tracks),
            }

        # Snapshot before drip
        _auto_snapshot_before_drip(
            schedule, api, target_id, raid_id
        )

        # Select random tracks to drip
        drip_uris = _select_drip_tracks(
            raid_uris, drip_count
        )

        # Dedupe against target (safety check)
        target_tracks = api.get_playlist_tracks(target_id)
        target_uri_set = {
            t.get("uri")
            for t in target_tracks
            if t.get("uri")
        }
        drip_uris = [
            u for u in drip_uris
            if u not in target_uri_set
        ]

        if not drip_uris:
            logger.info(
                "Schedule %s: all drip candidates "
                "already in target",
                schedule.id,
            )
            return {
                "tracks_added": 0,
                "tracks_total": len(target_tracks),
            }

        # Add to top of target playlist (position 0)
        api.playlist_add_items(
            target_id, drip_uris, position=0
        )

        # Remove from raid playlist
        api.playlist_remove_items(raid_id, drip_uris)

        # Update PendingRaidTrack status
        _mark_dripped_as_promoted(
            user_id, target_id, drip_uris
        )

        logger.info(
            "Schedule %s: dripped %d tracks from "
            "'%s' to '%s'",
            schedule.id,
            len(drip_uris),
            link.raid_playlist_name,
            schedule.target_playlist_name,
        )

        return {
            "tracks_added": len(drip_uris),
            "tracks_total": (
                len(target_tracks) + len(drip_uris)
            ),
        }

    except JobExecutionError:
        raise
    except SpotifyNotFoundError:
        raise JobExecutionError(
            "Playlist not found during drip. "
            "Target: {}, Raid: {}".format(
                target_id, link.raid_playlist_id
            )
        )
    except SpotifyAPIError as e:
        raise JobExecutionError(
            "Spotify API error during "
            "drip: {}".format(e)
        )


def _select_drip_tracks(raid_uris, drip_count):
    """Select random tracks from the raid playlist."""
    if len(raid_uris) <= drip_count:
        return list(raid_uris)
    return random.sample(raid_uris, drip_count)


def _auto_snapshot_before_drip(
    schedule, api, target_id, raid_id,
):
    """Create auto-snapshots before drip if enabled."""
    try:
        if not PlaylistSnapshotService.is_auto_snapshot_enabled(
            schedule.user_id
        ):
            return

        # Snapshot target
        target_tracks = api.get_playlist_tracks(target_id)
        target_uris = [
            t.get("uri")
            for t in target_tracks
            if t.get("uri")
        ]
        if target_uris:
            PlaylistSnapshotService.create_snapshot(
                user_id=schedule.user_id,
                playlist_id=target_id,
                playlist_name=(
                    schedule.target_playlist_name
                    or target_id
                ),
                track_uris=target_uris,
                snapshot_type=(
                    SnapshotType.AUTO_PRE_DRIP
                ),
                trigger_description=(
                    "Before scheduled drip "
                    "(target)"
                ),
            )

        # Snapshot raid playlist
        raid_tracks = api.get_playlist_tracks(raid_id)
        raid_uris = [
            t.get("uri")
            for t in raid_tracks
            if t.get("uri")
        ]
        if raid_uris:
            PlaylistSnapshotService.create_snapshot(
                user_id=schedule.user_id,
                playlist_id=raid_id,
                playlist_name="Raid playlist",
                track_uris=raid_uris,
                snapshot_type=(
                    SnapshotType.AUTO_PRE_DRIP
                ),
                trigger_description=(
                    "Before scheduled drip "
                    "(raid)"
                ),
            )
    except Exception as snap_err:
        logger.warning(
            "Auto-snapshot before drip "
            "failed: %s", snap_err
        )


def _mark_dripped_as_promoted(
    user_id, target_playlist_id, drip_uris,
):
    """Mark dripped tracks as PROMOTED in the DB."""
    from shuffify.models.db import db, PendingRaidTrack

    now = datetime.now(timezone.utc)
    try:
        PendingRaidTrack.query.filter(
            PendingRaidTrack.user_id == user_id,
            PendingRaidTrack.target_playlist_id
            == target_playlist_id,
            PendingRaidTrack.track_uri.in_(drip_uris),
            PendingRaidTrack.status
            == PendingRaidStatus.PENDING,
        ).update(
            {
                "status": PendingRaidStatus.PROMOTED,
                "resolved_at": now,
            },
            synchronize_session="fetch",
        )
        db.session.commit()
    except Exception as e:
        logger.warning(
            "Failed to mark dripped tracks as "
            "promoted: %s", e
        )
