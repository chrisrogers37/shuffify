"""
Rotate executor: swap rotation and pairing logic for
production/archive playlist management.

Currently only supports swap mode. See git history for
prior archive_oldest and refresh implementations.
"""

import logging
import random

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
    (
        rotation_mode, rotation_count,
        target_size, protect_count, pair,
    ) = _validate_rotation_config(schedule)
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

        if target_size is None:
            raise JobExecutionError(
                "Swap rotation requires a playlist "
                "size cap (target_size)"
            )
        return _rotate_swap(
            api, schedule, target_id,
            archive_id, prod_uris,
            rotation_count, target_size,
            protect_count,
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
        Tuple of (rotation_mode, rotation_count,
        target_size, protect_count, pair).

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
        "rotation_mode", RotationMode.SWAP
    )
    rotation_count = max(
        1, int(params.get("rotation_count", 5))
    )
    target_size = params.get("target_size")
    if target_size is not None:
        target_size = max(1, int(target_size))
    protect_count = max(
        0, int(params.get("protect_count", 0))
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

    return (
        rotation_mode, rotation_count,
        target_size, protect_count, pair,
    )


def _compute_rotation_count(
    rotation_count: int,
    target_size: int | None,
    playlist_len: int,
    protect_count: int,
) -> int:
    """
    Determine how many tracks to rotate out.

    If target_size is set and the playlist exceeds it,
    increase the count so the playlist is brought back
    to or under the cap.
    The count is also capped to the number of
    eligible (non-protected) tracks.
    """
    count = rotation_count
    if target_size is not None:
        overflow = playlist_len - target_size
        if overflow > 0:
            count = max(count, overflow)

    eligible = max(0, playlist_len - protect_count)
    return min(count, eligible)


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


# TODO: Re-implement archive_oldest mode
# (see git history for prior _rotate_archive)
# TODO: Re-implement refresh mode
# (see git history for prior _rotate_refresh)


def _purge_archive_overlaps(
    api, archive_id, archive_uris, prod_set,
):
    """Remove tracks from archive that already exist
    in the production playlist.

    This prevents stale overlaps from reducing the
    available swap-in pool and causing rotation to
    short-circuit.

    Returns:
        Tuple of (cleaned_archive_uris, purged_count).
    """
    overlaps = [
        u for u in archive_uris if u in prod_set
    ]
    if overlaps:
        api.playlist_remove_items(
            archive_id, overlaps
        )
        logger.info(
            "Purged %d overlapping tracks from "
            "archive %s",
            len(overlaps), archive_id,
        )
    cleaned = [
        u for u in archive_uris
        if u not in prod_set
    ]
    return cleaned, len(overlaps)


def _rotate_swap(
    api, schedule, target_id, archive_id,
    prod_uris, rotation_count, target_size,
    protect_count=0,
):
    """Exchange tracks between production and archive.

    Three-step approach:

    Step 0 (cleanup): Purge archive tracks that already
    exist in production to prevent stale overlaps from
    reducing the swap-in pool.

    Phase 1 (overflow): If playlist exceeds target_size,
    archive excess tracks to reach the cap. No swap-in
    occurs during overflow — this seeds the archive.

    Phase 2 (swap): When playlist is at or under cap,
    swap rotation_count tracks between production and
    archive.
    """
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

    # Step 0: Purge archive tracks that overlap with
    # production to keep the archive clean.
    archive_uris, purged = _purge_archive_overlaps(
        api, archive_id, archive_uris, prod_set,
    )

    archive_set = set(archive_uris)
    eligible_uris = prod_uris[protect_count:]

    # Phase 1: Archive overflow to reach target_size
    if (
        target_size is not None
        and len(prod_uris) > target_size
    ):
        overflow = len(prod_uris) - target_size
        # Randomly select overflow tracks from eligible
        if len(eligible_uris) <= overflow:
            overflow_uris = list(eligible_uris)
        else:
            overflow_uris = random.sample(
                eligible_uris, overflow
            )

        # Remove from production FIRST
        api.playlist_remove_items(
            target_id, overflow_uris
        )

        # Then archive (deduped)
        new_to_archive = [
            u for u in overflow_uris
            if u not in archive_set
        ]
        if new_to_archive:
            JobExecutorService._batch_add_tracks(
                api, archive_id, new_to_archive
            )

        # Verify actual playlist size after removal
        verified_tracks = api.get_playlist_tracks(
            target_id
        )
        actual_total = len([
            t for t in verified_tracks
            if t.get("uri")
        ]) if verified_tracks else 0

        expected_total = (
            len(prod_uris) - len(overflow_uris)
        )
        if actual_total != expected_total:
            logger.warning(
                "Schedule %s: overflow removal "
                "mismatch — expected %d tracks, "
                "got %d",
                schedule.id, expected_total,
                actual_total,
            )

        logger.info(
            "Schedule %s: archived %d overflow "
            "tracks from '%s' (cap %d, "
            "actual %d)",
            schedule.id, len(overflow_uris),
            schedule.target_playlist_name,
            target_size, actual_total,
        )

        return {
            "tracks_added": 0,
            "tracks_total": actual_total,
        }

    # Phase 2: Normal swap (playlist at or under cap)
    # available = archive-only tracks (overlaps already
    # purged in Step 0, but filter defensively)
    available = [
        u for u in archive_uris
        if u not in prod_set
    ]
    swap_in_uris = available[-rotation_count:]

    # Randomly select swap-out tracks from eligible
    swap_out_count = min(
        len(swap_in_uris), len(eligible_uris)
    )
    if swap_out_count >= len(eligible_uris):
        swap_out_uris = list(eligible_uris)
    else:
        swap_out_uris = random.sample(
            eligible_uris, swap_out_count
        )

    if swap_in_uris and swap_out_uris:
        # Remove outgoing from production first
        api.playlist_remove_items(
            target_id, swap_out_uris
        )

        # Then archive outgoing (deduped)
        new_to_archive = [
            u for u in swap_out_uris
            if u not in archive_set
        ]
        if new_to_archive:
            JobExecutorService._batch_add_tracks(
                api, archive_id, new_to_archive
            )

        # Remove incoming from archive first
        api.playlist_remove_items(
            archive_id, swap_in_uris
        )

        # Then add incoming to production
        JobExecutorService._batch_add_tracks(
            api, target_id, swap_in_uris
        )

    swapped = min(
        len(swap_in_uris),
        len(swap_out_uris),
    )

    # Verify actual playlist size after swap
    verified_tracks = api.get_playlist_tracks(
        target_id
    )
    actual_total = len([
        t for t in verified_tracks
        if t.get("uri")
    ]) if verified_tracks else 0

    if actual_total != len(prod_uris):
        logger.warning(
            "Schedule %s: swap size mismatch — "
            "expected %d tracks, got %d",
            schedule.id, len(prod_uris),
            actual_total,
        )

    logger.info(
        "Schedule %s: swapped %d tracks between "
        "'%s' and archive (purged %d overlaps)",
        schedule.id, swapped,
        schedule.target_playlist_name, purged,
    )

    return {
        "tracks_added": swapped,
        "tracks_total": actual_total,
    }
