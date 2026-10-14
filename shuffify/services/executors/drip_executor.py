"""
Drip executor: move tracks from raid playlist into target playlist.

Selects random tracks from the raid playlist, adds them to the top
of the target playlist, and removes them from the raid playlist.
PendingRaidTrack records are updated to PROMOTED status.
"""

import logging
import random
from datetime import datetime, timezone

from shuffify.enums import PendingRaidStatus, SnapshotType
from shuffify.models.db import Schedule
from shuffify.services.executors.base_executor import (
    JobExecutionError,
    verify_playlist_state,
)
from shuffify.services.live_operation_service import (
    book_not_required,
    current_operation,
    plan_tracks,
    read_tracks,
    write_tracks,
)
from shuffify.services.live_review_scope import (
    drip_gate,
    effective_drip_count,
    require_reviewed,
)
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)
from shuffify.shuffle_algorithms.utils import extract_uris
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
)

logger = logging.getLogger(__name__)


def execute_drip(schedule: Schedule, api: SpotifyAPI) -> dict:
    """
    Move tracks from raid playlist to the top of the
    target playlist.
    """
    from shuffify.services.raid_link_service import (
        RaidLinkService,
    )

    target_id = schedule.target_playlist_id
    user_id = schedule.user_id

    link = RaidLinkService.get_link_for_playlist(user_id, target_id)
    # A reviewed run never moves tracks through a collection other than the reviewed one.
    require_reviewed("link", link)
    if not link:
        raise JobExecutionError(
            "No raid playlist link found for " "playlist {}. Create a raid link " "first.".format(target_id)
        )

    params = schedule.algorithm_params or {}

    # A manual "Drip Now" runs regardless of the link's drip_enabled setting.
    operation = current_operation()
    if drip_gate(params, link) == "closed":
        logger.info(
            "Schedule %s: drip disabled for %s, " "skipping",
            schedule.id,
            target_id,
        )
        target_tracks = read_tracks(api, target_id)
        if operation:
            # The closed gate stops this step before it writes or promotes: say so for the review's facts.
            operation.record_not_started(["drip"])
        return {
            "tracks_added": 0,
            "tracks_total": len(target_tracks),
            "skipped_reason": "drip_disabled",
        }

    drip_count = effective_drip_count(params, link)
    require_reviewed("count", drip_count)

    try:
        raid_id = link.raid_playlist_id
        if current_operation() and raid_id == target_id:
            raise JobExecutionError("The collection must be distinct from the target")

        raid_tracks = read_tracks(api, raid_id)
        raid_uris = extract_uris(raid_tracks or [])

        if not raid_uris:
            logger.info(
                "Schedule %s: raid playlist %s is " "empty, nothing to drip",
                schedule.id,
                raid_id,
            )
            target_tracks = read_tracks(api, target_id)
            book_not_required("pending_promotion", target_id)
            return {
                "tracks_added": 0,
                "tracks_total": len(target_tracks),
            }

        _auto_snapshot_before_drip(schedule, api, target_id, raid_id)

        drip_uris = _select_drip_tracks(raid_uris, drip_count)

        target_tracks = read_tracks(api, target_id)
        target_uri_set = {t.get("uri") for t in target_tracks if t.get("uri")}
        drip_uris = [u for u in drip_uris if u not in target_uri_set]

        if not drip_uris:
            logger.info(
                "Schedule %s: all drip candidates " "already in target",
                schedule.id,
            )
            book_not_required("pending_promotion", target_id)
            return {
                "tracks_added": 0,
                "tracks_total": len(target_tracks),
            }

        plan_tracks(api, target_id, list(drip_uris) + extract_uris(target_tracks), step="drip")
        drip_set = set(drip_uris)
        plan_tracks(api, raid_id, [u for u in raid_uris if u not in drip_set], step="drip")
        write_tracks(api, "playlist_add_items", target_id, drip_uris, step="drip", position=0)
        if operation:
            count_key = "tracks_dripped" if operation.steps else "tracks_added"
            operation.counts.update({count_key: len(drip_uris), "tracks_total": len(target_tracks) + len(drip_uris)})
        write_tracks(api, "playlist_remove_items", raid_id, drip_uris, step="drip")

        # Verify before marking PROMOTED in the DB —
        # otherwise tracks can be flagged as promoted while
        # absent from the target (failed add) or still
        # living in the raid playlist (failed remove).
        prev_target_uris = extract_uris(target_tracks)
        # drip adds at position=0, so expected order is
        # drip_uris first, then existing target tracks.
        expected_target = list(drip_uris) + prev_target_uris
        # Order-sensitive: a drip that appended to the end instead of
        # prepending has the same multiset but the wrong sequence.
        verify_playlist_state(
            api,
            target_id,
            expected_target,
            schedule.id,
            "drip target",
            ordered=True,
        )

        expected_raid = [u for u in raid_uris if u not in drip_set]
        verify_playlist_state(
            api,
            raid_id,
            expected_raid,
            schedule.id,
            "drip raid",
        )

        # Reconcile lock positions (drip at pos 0
        # shifts all existing tracks down)
        new_total_uris = drip_uris + prev_target_uris
        from shuffify.services.track_lock_service import (
            TrackLockService,
        )

        TrackLockService.safe_reconcile_positions(user_id, target_id, new_total_uris)

        # A later pending-inbox commit failure must not leave locks pointing at
        # pre-Drip positions after both Spotify effects have been verified.
        _mark_dripped_as_promoted(user_id, target_id, drip_uris)

        logger.info(
            "Schedule %s: dripped %d tracks from " "'%s' to '%s'",
            schedule.id,
            len(drip_uris),
            link.raid_playlist_name,
            schedule.target_playlist_name,
        )

        return {
            "tracks_added": len(drip_uris),
            "tracks_total": (len(target_tracks) + len(drip_uris)),
        }

    except JobExecutionError:
        raise
    except SpotifyPartialBatchError:
        # Half-written: the job runner restores this job's snapshots.
        raise
    except SpotifyNotFoundError:
        raise JobExecutionError(
            "Playlist not found during drip. " "Target: {}, Raid: {}".format(target_id, link.raid_playlist_id)
        )
    except SpotifyAPIError as e:
        raise JobExecutionError("Spotify API error during " "drip: {}".format(e))


def _select_drip_tracks(raid_uris, drip_count):
    """Select random tracks from the raid playlist."""
    if len(raid_uris) <= drip_count:
        return list(raid_uris)
    return random.sample(raid_uris, drip_count)


def _auto_snapshot_before_drip(
    schedule,
    api,
    target_id,
    raid_id,
):
    """Create auto-snapshots before drip if enabled."""
    try:
        target_tracks = read_tracks(api, target_id)
        raid_tracks = read_tracks(api, raid_id)
    except Exception as snap_err:
        logger.warning("Auto-snapshot before drip " "failed: %s", snap_err)
        return

    PlaylistSnapshotService.auto_snapshot_if_enabled(
        user_id=schedule.user_id,
        playlist_id=target_id,
        playlist_name=schedule.target_playlist_name or target_id,
        track_uris=extract_uris(target_tracks or []),
        snapshot_type=SnapshotType.AUTO_PRE_DRIP,
        trigger_description="Before scheduled drip (target)",
    )
    PlaylistSnapshotService.auto_snapshot_if_enabled(
        user_id=schedule.user_id,
        playlist_id=raid_id,
        playlist_name="Raid playlist",
        track_uris=extract_uris(raid_tracks or []),
        snapshot_type=SnapshotType.AUTO_PRE_DRIP,
        trigger_description="Before scheduled drip (raid)",
    )


def _mark_dripped_as_promoted(
    user_id,
    target_playlist_id,
    drip_uris,
):
    """Mark dripped tracks as PROMOTED in the DB."""
    from shuffify.models.db import PendingRaidTrack, db
    from shuffify.services.base import safe_commit

    operation = current_operation()
    now = datetime.now(timezone.utc)
    ids = []
    try:
        rows = PendingRaidTrack.query.filter(
            PendingRaidTrack.user_id == user_id,
            PendingRaidTrack.target_playlist_id == target_playlist_id,
            PendingRaidTrack.track_uri.in_(drip_uris),
            PendingRaidTrack.status == PendingRaidStatus.PENDING,
        ).all()
        ids = [row.id for row in rows]
        for row in rows:
            row.status, row.resolved_at = PendingRaidStatus.PROMOTED, now
        safe_commit("mark dripped tracks as promoted")
        if operation:
            operation.promoted_ids.extend(ids)
            operation.book(
                "pending_promotion",
                "complete" if ids else "not_required",
                resource=target_playlist_id,
                affected_count=len(ids),
            )
    except Exception as error:
        logger.warning("Failed to mark dripped tracks as promoted: %s", error)
        if operation:
            # Attempted: unknown until shown unchanged, even if the rollback itself fails.
            operation.book(
                "pending_promotion", "unknown", resource=target_playlist_id, error_code="pending_promotion_failed"
            )
        db.session.rollback()
        if operation:
            try:
                unchanged = (
                    all(
                        row.status == PendingRaidStatus.PENDING
                        for row in PendingRaidTrack.query.filter(
                            PendingRaidTrack.id.in_(ids),
                            PendingRaidTrack.user_id == user_id,
                        ).all()
                    )
                    if ids
                    else True
                )
            except Exception:
                unchanged = False
            operation.book(
                "pending_promotion",
                "failed" if unchanged else "unknown",
                resource=target_playlist_id,
                error_code="pending_promotion_failed",
            )
            operation.error_code = "pending_promotion_failed"
            raise
