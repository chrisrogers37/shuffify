"""
Shuffle executor: run shuffle algorithms on target playlists.
"""

import logging

from shuffify.enums import SnapshotType
from shuffify.models.db import Schedule
from shuffify.services.executors.base_executor import (
    JobExecutionError,
    verify_playlist_state,
)
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.shuffle_algorithms.utils import extract_uris
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
)

logger = logging.getLogger(__name__)


def execute_shuffle(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """Run a shuffle algorithm on the target playlist."""
    target_id = schedule.target_playlist_id
    algorithm_name = schedule.algorithm_name

    if not algorithm_name:
        raise JobExecutionError(
            f"Schedule {schedule.id}: "
            f"no algorithm configured for shuffle"
        )

    try:
        raw_tracks = api.get_playlist_tracks(target_id)

        logger.info(
            "Schedule %d: fetched %d raw tracks from %s",
            schedule.id,
            len(raw_tracks) if raw_tracks else 0,
            target_id,
        )

        if not raw_tracks:
            logger.warning(
                "Schedule %d: no tracks returned for %s "
                "— skipping shuffle",
                schedule.id,
                target_id,
            )
            return {"tracks_added": 0, "tracks_total": 0}

        _auto_snapshot_before_shuffle(
            schedule, raw_tracks, algorithm_name
        )

        tracks = []
        for t in raw_tracks:
            if t.get("uri"):
                tracks.append(
                    {
                        "id": t.get("id", ""),
                        "name": t.get("name", ""),
                        "uri": t["uri"],
                        "added_at": t.get("added_at"),
                        "artists": [
                            a.get("name", "")
                            for a in t.get("artists", [])
                        ],
                        "album": t.get("album", {}),
                    }
                )

        if not tracks:
            logger.warning(
                "Schedule %d: %d raw tracks but 0 had "
                "valid URIs — skipping shuffle",
                schedule.id,
                len(raw_tracks),
            )
            return {"tracks_added": 0, "tracks_total": 0}

        # Query track locks for this playlist
        from shuffify.services.track_lock_service import (
            TrackLockService,
        )
        locked_positions = (
            TrackLockService.safe_get_locked_positions(
                schedule.user_id, target_id
            )
        )

        algorithm_class = ShuffleRegistry.get_algorithm(
            algorithm_name
        )
        algorithm = algorithm_class()
        params = schedule.algorithm_params or {}

        if locked_positions:
            from shuffify.shuffle_algorithms.utils import (
                reassemble_with_locks,
                split_locked_tracks,
            )

            validated_locks, unlocked_tracks = (
                split_locked_tracks(
                    tracks, locked_positions
                )
            )

            if validated_locks and not unlocked_tracks:
                logger.info(
                    "Schedule %d: all tracks locked "
                    "— skipping shuffle",
                    schedule.id,
                )
                return {
                    "tracks_added": 0,
                    "tracks_total": len(tracks),
                    "skipped_reason": (
                        "all_tracks_locked"
                    ),
                }

            shuffled_uris = algorithm.shuffle(
                unlocked_tracks, **params
            )
            shuffled_uris = reassemble_with_locks(
                shuffled_uris,
                validated_locks,
                len(tracks),
            )
            logger.info(
                "Schedule %d: shuffled with %d "
                "locked tracks",
                schedule.id,
                len(validated_locks),
            )
        else:
            shuffled_uris = algorithm.shuffle(
                tracks, **params
            )

        logger.info(
            "Schedule %d: applying %d shuffled tracks "
            "to %s via Spotify API",
            schedule.id,
            len(shuffled_uris),
            target_id,
        )

        api.update_playlist_tracks(
            target_id, shuffled_uris
        )

        # Catches silent multi-batch truncation (update_playlist_tracks
        # returns True even if a POST batch after the initial PUT fails)
        # and, via ordered=True, a write that kept the original order —
        # a shuffle that silently didn't reorder (SR-007).
        verify_playlist_state(
            api, target_id, shuffled_uris,
            schedule.id, "shuffle",
            ordered=True,
        )

        # Reconcile lock positions after reorder
        TrackLockService.safe_reconcile_positions(
            schedule.user_id, target_id,
            shuffled_uris,
        )

        logger.info(
            "Schedule %d: shuffled "
            "%s with %s (%d tracks)",
            schedule.id,
            schedule.target_playlist_name,
            algorithm_name,
            len(shuffled_uris),
        )

        return {
            "tracks_added": 0,
            "tracks_total": len(shuffled_uris),
        }

    except SpotifyNotFoundError:
        raise JobExecutionError(
            f"Target playlist {target_id} not found"
        )
    except ValueError as e:
        raise JobExecutionError(
            f"Invalid algorithm '{algorithm_name}': {e}"
        )
    except SpotifyAPIError as e:
        raise JobExecutionError(
            f"Spotify API error during shuffle: {e}"
        )


def _auto_snapshot_before_shuffle(
    schedule: Schedule,
    raw_tracks: list,
    algorithm_name: str,
) -> None:
    """Create an auto-snapshot before a scheduled shuffle
    if enabled."""
    # AUTO_PRE_SHUFFLE, matching the interactive shuffle route and the
    # AUTO_PRE_* every sibling executor uses. This path used to write
    # SCHEDULED_PRE_EXECUTION, which the snapshot list renders as
    # "Auto-backup" rather than "Pre-Shuffle" -- the same operation labelled
    # differently depending on whether a human or the scheduler triggered it.
    PlaylistSnapshotService.auto_snapshot_if_enabled(
        user_id=schedule.user_id,
        playlist_id=schedule.target_playlist_id,
        playlist_name=(
            schedule.target_playlist_name
            or schedule.target_playlist_id
        ),
        track_uris=extract_uris(raw_tracks),
        snapshot_type=SnapshotType.AUTO_PRE_SHUFFLE,
        trigger_description=f"Before scheduled {algorithm_name}",
    )
