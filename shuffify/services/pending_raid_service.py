"""
Service for managing pending raid tracks.

Staged tracks live in the database until the user promotes (adds to
Spotify) or dismisses them from the workshop Track Inbox.
"""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from shuffify.models.db import db, PendingRaidTrack
from shuffify.enums import PendingRaidStatus
from shuffify.services.base import safe_commit

logger = logging.getLogger(__name__)


class PendingRaidService:
    """CRUD operations for pending raid tracks."""

    @staticmethod
    def stage_tracks(
        user_id: int,
        target_playlist_id: str,
        tracks: List[Dict[str, Any]],
        source_playlist_id: Optional[str] = None,
        source_name: Optional[str] = None,
    ) -> int:
        """
        Bulk-insert pending tracks with deduplication.

        Returns the number of newly staged tracks.
        """
        # Collect all candidate URIs from the batch
        candidate_uris = [
            t.get("uri") for t in tracks if t.get("uri")
        ]
        if not candidate_uris:
            return 0

        # Single query to find existing URIs (replaces N per-track queries)
        existing_uris = set(
            row[0]
            for row in db.session.query(
                PendingRaidTrack.track_uri
            )
            .filter(
                PendingRaidTrack.user_id == user_id,
                PendingRaidTrack.target_playlist_id
                == target_playlist_id,
                PendingRaidTrack.track_uri.in_(
                    candidate_uris
                ),
            )
            .all()
        )

        staged = 0
        for track in tracks:
            uri = track.get("uri")
            if not uri or uri in existing_uris:
                continue

            artists = track.get("artists", [])
            if isinstance(artists, list):
                artists = ", ".join(artists)

            pending = PendingRaidTrack(
                user_id=user_id,
                target_playlist_id=target_playlist_id,
                track_uri=uri,
                track_name=track.get("name", "Unknown"),
                track_artists=artists,
                track_album=track.get("album_name", ""),
                track_image_url=track.get(
                    "album_image_url", ""
                ),
                track_duration_ms=track.get(
                    "duration_ms"
                ),
                source_playlist_id=source_playlist_id,
                source_name=source_name,
                status=PendingRaidStatus.PENDING,
            )
            db.session.add(pending)
            existing_uris.add(uri)
            staged += 1

        if staged > 0:
            safe_commit(
                f"stage {staged} tracks for user "
                f"{user_id}",
            )
            logger.info(
                "Staged %d tracks for user %d, "
                "playlist %s",
                staged,
                user_id,
                target_playlist_id,
            )

        return staged

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
    def promote_tracks(
        user_id: int,
        target_playlist_id: str,
        track_ids: List[int],
    ) -> List[PendingRaidTrack]:
        """
        Mark specific tracks as promoted.

        Returns the promoted track records.
        """
        now = datetime.now(timezone.utc)
        tracks = PendingRaidTrack.query.filter(
            PendingRaidTrack.id.in_(track_ids),
            PendingRaidTrack.user_id == user_id,
            PendingRaidTrack.target_playlist_id
            == target_playlist_id,
            PendingRaidTrack.status
            == PendingRaidStatus.PENDING,
        ).all()

        for t in tracks:
            t.status = PendingRaidStatus.PROMOTED
            t.resolved_at = now

        if tracks:
            safe_commit("promote pending tracks")
        return tracks

    @staticmethod
    def promote_all(
        user_id: int,
        target_playlist_id: str,
    ) -> List[PendingRaidTrack]:
        """Mark all pending tracks as promoted."""
        now = datetime.now(timezone.utc)
        tracks = PendingRaidTrack.query.filter_by(
            user_id=user_id,
            target_playlist_id=target_playlist_id,
            status=PendingRaidStatus.PENDING,
        ).all()

        for t in tracks:
            t.status = PendingRaidStatus.PROMOTED
            t.resolved_at = now

        if tracks:
            safe_commit("promote pending tracks")
        return tracks

    @staticmethod
    def unpromote_tracks(
        user_id: int,
        target_playlist_id: str,
        track_uris: List[str],
    ) -> int:
        """Revert promoted tracks back to pending status.

        Returns the number of tracks reverted.
        """
        count = PendingRaidTrack.query.filter(
            PendingRaidTrack.user_id == user_id,
            PendingRaidTrack.target_playlist_id
            == target_playlist_id,
            PendingRaidTrack.track_uri.in_(track_uris),
            PendingRaidTrack.status
            == PendingRaidStatus.PROMOTED,
        ).update(
            {
                "status": PendingRaidStatus.PENDING,
                "resolved_at": None,
            },
            synchronize_session="fetch",
        )
        safe_commit("unpromote pending tracks")
        return count

    @staticmethod
    def dismiss_tracks(
        user_id: int,
        target_playlist_id: str,
        track_ids: List[int],
    ) -> int:
        """Mark specific tracks as dismissed."""
        now = datetime.now(timezone.utc)
        count = PendingRaidTrack.query.filter(
            PendingRaidTrack.id.in_(track_ids),
            PendingRaidTrack.user_id == user_id,
            PendingRaidTrack.target_playlist_id
            == target_playlist_id,
            PendingRaidTrack.status
            == PendingRaidStatus.PENDING,
        ).update(
            {
                "status": PendingRaidStatus.DISMISSED,
                "resolved_at": now,
            },
            synchronize_session="fetch",
        )
        safe_commit("dismiss pending tracks")
        return count

    @staticmethod
    def dismiss_all(
        user_id: int,
        target_playlist_id: str,
    ) -> int:
        """Mark all pending tracks as dismissed."""
        now = datetime.now(timezone.utc)
        count = PendingRaidTrack.query.filter_by(
            user_id=user_id,
            target_playlist_id=target_playlist_id,
            status=PendingRaidStatus.PENDING,
        ).update(
            {
                "status": PendingRaidStatus.DISMISSED,
                "resolved_at": now,
            },
            synchronize_session="fetch",
        )
        safe_commit("dismiss all pending tracks")
        return count

    @staticmethod
    def get_pending_count(
        user_id: int,
        target_playlist_id: str,
    ) -> int:
        """Get the count of pending tracks."""
        return PendingRaidTrack.query.filter_by(
            user_id=user_id,
            target_playlist_id=target_playlist_id,
            status=PendingRaidStatus.PENDING,
        ).count()

    @staticmethod
    def cleanup_resolved(
        user_id: int,
        target_playlist_id: str,
    ) -> int:
        """Remove old promoted/dismissed records."""
        count = PendingRaidTrack.query.filter(
            PendingRaidTrack.user_id == user_id,
            PendingRaidTrack.target_playlist_id
            == target_playlist_id,
            PendingRaidTrack.status.in_([
                PendingRaidStatus.PROMOTED,
                PendingRaidStatus.DISMISSED,
            ]),
        ).delete(synchronize_session="fetch")
        safe_commit("cleanup resolved pending tracks")
        return count
