"""
Service for managing pending raid tracks.

Staged tracks live in the database until the user promotes (adds to
Spotify) or dismisses them from the workshop Track Inbox.
"""

import logging
from datetime import datetime, timezone
from typing import List

from shuffify.enums import PendingRaidStatus
from shuffify.models.db import PendingRaidTrack
from shuffify.services.base import safe_commit

logger = logging.getLogger(__name__)


class PendingRaidSelectionError(ValueError):
    """Requested records are missing, dismissed, or outside the user's target."""


class PendingRaidService:
    """CRUD operations for pending raid tracks."""

    @staticmethod
    def list_pending(
        user_id: int,
        target_playlist_id: str,
    ) -> List[PendingRaidTrack]:
        """List all pending tracks for a playlist."""
        return (
            PendingRaidTrack.query.filter_by(
                user_id=user_id,
                target_playlist_id=target_playlist_id,
                status=PendingRaidStatus.PENDING,
            )
            .order_by(PendingRaidTrack.created_at.desc())
            .all()
        )

    @staticmethod
    def get_for_finalize(user_id, target_playlist_id, track_ids):
        """Load exactly the captured IDs, including already promoted retries.

        Discovery source IDs on these rows are provenance, not the linked
        collection that finalization is authorized to clean up.
        """
        requested = set(track_ids)
        tracks = PendingRaidTrack.query.filter(
            PendingRaidTrack.id.in_(requested),
            PendingRaidTrack.user_id == user_id,
            PendingRaidTrack.target_playlist_id == target_playlist_id,
            PendingRaidTrack.status.in_([
                PendingRaidStatus.PENDING, PendingRaidStatus.PROMOTED,
            ]),
        ).all()
        if not requested or {track.id for track in tracks} != requested:
            raise PendingRaidSelectionError("Invalid captured track selection")
        return tracks

    @staticmethod
    def complete_finalize(tracks):
        """Transition only verified records after source removal is observed."""
        pending = [t for t in tracks if t.status == PendingRaidStatus.PENDING]
        for track in pending:
            track.status = PendingRaidStatus.PROMOTED
            track.resolved_at = datetime.now(timezone.utc)
        if pending:
            safe_commit("finalize verified pending tracks")
