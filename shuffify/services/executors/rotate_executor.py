"""
Rotate executor: rotation modes and pairing logic for
production/archive playlist management.
"""

import logging

from shuffify.models.db import Schedule
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.enums import SnapshotType, RotationMode
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)

logger = logging.getLogger(__name__)


def execute_rotate(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """
    Rotate tracks between production and archive
    playlists.
    """
    from shuffify.services.executors.base_executor import (
        JobExecutionError,
    )

    target_id = schedule.target_playlist_id
    rotation_mode, rotation_count, pair = (
        _validate_rotation_config(schedule)
    )
    archive_id = pair.archive_playlist_id

    try:
        prod_tracks = api.get_playlist_tracks(
            target_id
        )
        if not prod_tracks:
            return {
                "tracks_added": 0,
                "tracks_total": 0,
            }

        prod_uris = [
            t["uri"]
            for t in prod_tracks
            if t.get("uri")
        ]

        _auto_snapshot_before_rotate(
            schedule, prod_uris, rotation_mode
        )

        actual_count = min(
            rotation_count, len(prod_uris)
        )
        if actual_count == 0:
            return {
                "tracks_added": 0,
                "tracks_total": len(prod_uris),
            }

        oldest_uris = prod_uris[:actual_count]

        if rotation_mode == RotationMode.ARCHIVE_OLDEST:
            return _rotate_archive(
                api, schedule, target_id,
                archive_id, oldest_uris,
                prod_uris, actual_count,
            )
        elif rotation_mode == RotationMode.REFRESH:
            return _rotate_refresh(
                api, schedule, target_id,
                archive_id, prod_uris,
                actual_count,
            )
        elif rotation_mode == RotationMode.SWAP:
            return _rotate_swap(
                api, schedule, target_id,
                archive_id, oldest_uris,
                prod_uris, actual_count,
            )
        else:
            raise JobExecutionError(
                "Unknown rotation mode: "
                "{}".format(rotation_mode)
            )

    except JobExecutionError:
        raise
    except SpotifyNotFoundError:
        raise JobExecutionError(
            "Playlist not found during rotation. "
            "Target: {}, Archive: {}".format(
                target_id, archive_id
            )
        )
    except SpotifyAPIError as e:
        raise JobExecutionError(
            "Spotify API error during "
            "rotation: {}".format(e)
        )


def _validate_rotation_config(
    schedule: Schedule,
) -> tuple:
    """
    Extract and validate rotation parameters from
    schedule.

    Returns:
        Tuple of (rotation_mode, rotation_count, pair).

    Raises:
        JobExecutionError: If mode is invalid or no
            pair found.
    """
    from shuffify.services.executors.base_executor import (
        JobExecutionError,
    )
    from shuffify.services.playlist_pair_service import (
        PlaylistPairService,
    )

    params = schedule.algorithm_params or {}
    rotation_mode = params.get(
        "rotation_mode", RotationMode.ARCHIVE_OLDEST
    )
    rotation_count = max(
        1, int(params.get("rotation_count", 5))
    )

    valid_modes = set(RotationMode)
    if rotation_mode not in valid_modes:
        raise JobExecutionError(
            "Invalid rotation_mode: "
            "{}".format(rotation_mode)
        )

    pair = PlaylistPairService.get_pair_for_playlist(
        user_id=schedule.user_id,
        production_playlist_id=(
            schedule.target_playlist_id
        ),
    )
    if not pair:
        raise JobExecutionError(
            "No archive pair found for playlist "
            "{}. Create a pair in the workshop "
            "first.".format(
                schedule.target_playlist_id
            )
        )

    return rotation_mode, rotation_count, pair


def _auto_snapshot_before_rotate(
    schedule: Schedule,
    prod_uris: list,
    rotation_mode: str,
) -> None:
    """Create an auto-snapshot before rotation if
    enabled."""
    try:
        if (
            prod_uris
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
                track_uris=prod_uris,
                snapshot_type=(
                    SnapshotType.AUTO_PRE_ROTATE
                ),
                trigger_description=(
                    "Before scheduled "
                    "{} rotation".format(
                        rotation_mode
                    )
                ),
            )
    except Exception as snap_err:
        logger.warning(
            "Auto-snapshot before rotation "
            "failed: %s", snap_err
        )


def _rotate_archive(
    api, schedule, target_id, archive_id,
    oldest_uris, prod_uris, actual_count,
):
    """Archive oldest tracks from production."""
    from shuffify.services.executors.base_executor import (
        JobExecutorService,
    )

    JobExecutorService._batch_add_tracks(
        api, archive_id, oldest_uris
    )
    api.playlist_remove_items(
        target_id, oldest_uris
    )

    logger.info(
        "Schedule %s: archived %d oldest tracks "
        "from '%s'",
        schedule.id, actual_count,
        schedule.target_playlist_name,
    )

    return {
        "tracks_added": 0,
        "tracks_total": (
            len(prod_uris) - actual_count
        ),
    }


def _rotate_refresh(
    api, schedule, target_id, archive_id,
    prod_uris, actual_count,
):
    """Replace oldest production tracks with newest
    archive tracks."""
    from shuffify.services.executors.base_executor import (
        JobExecutorService,
    )

    archive_tracks = api.get_playlist_tracks(
        archive_id
    )
    archive_uris = [
        t["uri"]
        for t in archive_tracks
        if t.get("uri")
    ]

    prod_set = set(prod_uris)
    available = [
        u for u in archive_uris
        if u not in prod_set
    ]
    refresh_uris = available[-actual_count:]
    remove_count = min(
        actual_count, len(refresh_uris)
    )
    to_remove = prod_uris[:remove_count]

    if refresh_uris:
        api.playlist_remove_items(
            target_id, to_remove
        )
        JobExecutorService._batch_add_tracks(
            api, target_id, refresh_uris
        )

    new_total = (
        len(prod_uris) - remove_count
        + len(refresh_uris)
    )

    logger.info(
        "Schedule %s: refreshed %d tracks in '%s'",
        schedule.id, len(refresh_uris),
        schedule.target_playlist_name,
    )

    return {
        "tracks_added": len(refresh_uris),
        "tracks_total": new_total,
    }


def _rotate_swap(
    api, schedule, target_id, archive_id,
    oldest_uris, prod_uris, actual_count,
):
    """Exchange tracks between production and
    archive."""
    from shuffify.services.executors.base_executor import (
        JobExecutorService,
    )

    archive_tracks = api.get_playlist_tracks(
        archive_id
    )
    archive_uris = [
        t["uri"]
        for t in archive_tracks
        if t.get("uri")
    ]

    prod_set = set(prod_uris)
    available = [
        u for u in archive_uris
        if u not in prod_set
    ]
    swap_in_uris = available[-actual_count:]
    swap_out_uris = oldest_uris[
        :len(swap_in_uris)
    ]

    if swap_in_uris and swap_out_uris:
        JobExecutorService._batch_add_tracks(
            api, archive_id, swap_out_uris
        )
        api.playlist_remove_items(
            target_id, swap_out_uris
        )

        JobExecutorService._batch_add_tracks(
            api, target_id, swap_in_uris
        )
        api.playlist_remove_items(
            archive_id, swap_in_uris
        )

    swapped = min(
        len(swap_in_uris),
        len(swap_out_uris),
    )

    logger.info(
        "Schedule %s: swapped %d tracks between "
        "'%s' and archive",
        schedule.id, swapped,
        schedule.target_playlist_name,
    )

    return {
        "tracks_added": swapped,
        "tracks_total": len(prod_uris),
    }
