"""
Raid executor: pull new tracks from source playlists into target.
"""

import logging
from typing import List

from shuffify.models.db import Schedule
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.enums import SnapshotType
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)

logger = logging.getLogger(__name__)


def execute_raid(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """
    Pull new tracks from source playlists into the target.
    """
    from shuffify.services.executors.base_executor import (
        JobExecutorService,
        JobExecutionError,
    )

    target_id = schedule.target_playlist_id
    source_ids = schedule.source_playlist_ids or []

    if not source_ids:
        logger.info(
            f"Schedule {schedule.id}: no source playlists "
            f"configured, skipping raid"
        )
        target_tracks = api.get_playlist_tracks(target_id)
        return {
            "tracks_added": 0,
            "tracks_total": len(target_tracks),
        }

    try:
        target_tracks = api.get_playlist_tracks(target_id)
        target_uris = {
            t.get("uri")
            for t in target_tracks
            if t.get("uri")
        }

        _auto_snapshot_before_raid(
            schedule, target_tracks
        )

        new_uris = _fetch_raid_sources(
            api, source_ids, target_uris
        )

        if not new_uris:
            logger.info(
                f"Schedule {schedule.id}: "
                f"no new tracks to add"
            )
            return {
                "tracks_added": 0,
                "tracks_total": len(target_tracks),
            }

        JobExecutorService._batch_add_tracks(
            api, target_id, new_uris
        )

        total = len(target_tracks) + len(new_uris)
        logger.info(
            f"Schedule {schedule.id}: added "
            f"{len(new_uris)} tracks to "
            f"{schedule.target_playlist_name} "
            f"(total: {total})"
        )

        return {
            "tracks_added": len(new_uris),
            "tracks_total": total,
        }

    except SpotifyNotFoundError:
        raise JobExecutionError(
            f"Target playlist {target_id} not found. "
            f"It may have been deleted."
        )
    except SpotifyAPIError as e:
        raise JobExecutionError(
            f"Spotify API error during raid: {e}"
        )


def _auto_snapshot_before_raid(
    schedule: Schedule,
    target_tracks: list,
) -> None:
    """Create an auto-snapshot before a scheduled raid
    if enabled."""
    try:
        pre_raid_uris = [
            t.get("uri")
            for t in target_tracks
            if t.get("uri")
        ]
        if (
            pre_raid_uris
            and PlaylistSnapshotService
            .is_auto_snapshot_enabled(
                schedule.user_id
            )
        ):
            PlaylistSnapshotService.create_snapshot(
                user_id=schedule.user_id,
                playlist_id=(
                    schedule.target_playlist_id
                ),
                playlist_name=(
                    schedule.target_playlist_name
                    or schedule.target_playlist_id
                ),
                track_uris=pre_raid_uris,
                snapshot_type=(
                    SnapshotType.AUTO_PRE_RAID
                ),
                trigger_description=(
                    "Before scheduled raid"
                ),
            )
    except Exception as snap_err:
        logger.warning(
            "Auto-snapshot before scheduled "
            f"raid failed: {snap_err}"
        )


def _fetch_raid_sources(
    api: SpotifyAPI,
    source_ids: list,
    target_uris: set,
) -> List[str]:
    """
    Fetch new tracks from source playlists not already
    in target.

    Returns:
        List of new track URIs (deduplicated).
    """
    new_uris: List[str] = []
    for source_id in source_ids:
        try:
            source_tracks = api.get_playlist_tracks(
                source_id
            )
            for track in source_tracks:
                uri = track.get("uri")
                if (
                    uri
                    and uri not in target_uris
                    and uri not in new_uris
                ):
                    new_uris.append(uri)
        except SpotifyNotFoundError:
            logger.warning(
                f"Source playlist {source_id} "
                f"not found, skipping"
            )
            continue
    return new_uris
