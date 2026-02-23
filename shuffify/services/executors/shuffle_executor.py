"""
Shuffle executor: run shuffle algorithms on target playlists.
"""

import logging

from shuffify.models.db import Schedule
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.enums import SnapshotType
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)

logger = logging.getLogger(__name__)


def execute_shuffle(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """Run a shuffle algorithm on the target playlist."""
    from shuffify.services.executors.base_executor import (
        JobExecutionError,
    )

    target_id = schedule.target_playlist_id
    algorithm_name = schedule.algorithm_name

    if not algorithm_name:
        raise JobExecutionError(
            f"Schedule {schedule.id}: "
            f"no algorithm configured for shuffle"
        )

    try:
        raw_tracks = api.get_playlist_tracks(target_id)
        if not raw_tracks:
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
                        "artists": [
                            a.get("name", "")
                            for a in t.get("artists", [])
                        ],
                        "album": t.get("album", {}),
                    }
                )

        if not tracks:
            return {"tracks_added": 0, "tracks_total": 0}

        algorithm_class = ShuffleRegistry.get_algorithm(
            algorithm_name
        )
        algorithm = algorithm_class()
        params = schedule.algorithm_params or {}
        shuffled_uris = algorithm.shuffle(
            tracks, **params
        )

        api.update_playlist_tracks(
            target_id, shuffled_uris
        )

        logger.info(
            f"Schedule {schedule.id}: shuffled "
            f"{schedule.target_playlist_name} "
            f"with {algorithm_name}"
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
    try:
        pre_shuffle_uris = [
            t["uri"]
            for t in raw_tracks
            if t.get("uri")
        ]
        if (
            pre_shuffle_uris
            and PlaylistSnapshotService
            .is_auto_snapshot_enabled(
                schedule.user_id
            )
        ):
            PlaylistSnapshotService.create_snapshot(
                user_id=schedule.user_id,
                playlist_id=schedule.target_playlist_id,
                playlist_name=(
                    schedule.target_playlist_name
                    or schedule.target_playlist_id
                ),
                track_uris=pre_shuffle_uris,
                snapshot_type=(
                    SnapshotType
                    .SCHEDULED_PRE_EXECUTION
                ),
                trigger_description=(
                    "Before scheduled "
                    f"{algorithm_name}"
                ),
            )
    except Exception as snap_err:
        logger.warning(
            "Auto-snapshot before scheduled "
            f"shuffle failed: {snap_err}"
        )
