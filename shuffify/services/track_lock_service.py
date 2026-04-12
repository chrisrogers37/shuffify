"""
Service for managing per-track position locks within playlists.

Supports two lock tiers:
- Standard: auto-expires after 30 days.
- Super: permanent until manually removed.

Locked tracks are protected from shuffle reordering,
rotation swap-outs, and manual drag-and-drop in the Workshop.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from shuffify.enums import LockTier
from shuffify.models.db import db, TrackLock
from shuffify.services.base import safe_commit

logger = logging.getLogger(__name__)

STANDARD_EXPIRY_DAYS = TrackLock.STANDARD_EXPIRY_DAYS


class TrackLockError(Exception):
    """Base error for track lock operations."""

    pass


class TrackLockService:
    """Manages per-track position locks."""

    @staticmethod
    def _active_filter():
        """SQLAlchemy filter clause for non-expired locks."""
        now = datetime.now(timezone.utc)
        return db.or_(
            TrackLock.expires_at.is_(None),
            TrackLock.expires_at > now,
        )

    @staticmethod
    def toggle_lock(
        user_id: int,
        playlist_id: str,
        track_uri: str,
        position: int,
    ) -> Optional[Dict]:
        """
        Cycle lock tier: unlocked -> standard -> super -> unlocked.

        Returns:
            Dict with new lock state, or None if unlocked.
        """
        existing = TrackLock.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=playlist_id,
            track_uri=track_uri,
        ).first()

        if existing is None:
            return TrackLockService._create_lock(
                user_id, playlist_id, track_uri,
                position, LockTier.STANDARD,
            )

        if existing.is_expired:
            db.session.delete(existing)
            db.session.flush()
            return TrackLockService._create_lock(
                user_id, playlist_id, track_uri,
                position, LockTier.STANDARD,
            )

        if existing.lock_tier == LockTier.STANDARD:
            existing.lock_tier = LockTier.SUPER
            existing.expires_at = None
            existing.position = position
            safe_commit(
                "upgrade lock to super for "
                f"{track_uri} in {playlist_id}",
                TrackLockError,
            )
            return existing.to_dict()

        # Super lock -> unlock
        db.session.delete(existing)
        safe_commit(
            f"unlock {track_uri} in {playlist_id}",
            TrackLockError,
        )
        return None

    @staticmethod
    def _create_lock(
        user_id: int,
        playlist_id: str,
        track_uri: str,
        position: int,
        tier: str,
    ) -> Dict:
        """Create a new TrackLock record."""
        now = datetime.now(timezone.utc)
        expires_at = None
        if tier == LockTier.STANDARD:
            expires_at = now + timedelta(
                days=STANDARD_EXPIRY_DAYS
            )

        lock = TrackLock(
            user_id=user_id,
            spotify_playlist_id=playlist_id,
            track_uri=track_uri,
            position=position,
            lock_tier=tier,
            created_at=now,
            expires_at=expires_at,
        )
        db.session.add(lock)
        safe_commit(
            f"create {tier} lock for {track_uri} "
            f"at pos {position} in {playlist_id}",
            TrackLockError,
        )
        return lock.to_dict()

    @staticmethod
    def set_lock(
        user_id: int,
        playlist_id: str,
        track_uri: str,
        position: int,
        tier: str,
    ) -> Dict:
        """Set a specific lock tier (create or update)."""
        existing = TrackLock.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=playlist_id,
            track_uri=track_uri,
        ).first()

        now = datetime.now(timezone.utc)
        expires_at = None
        if tier == LockTier.STANDARD:
            expires_at = now + timedelta(
                days=STANDARD_EXPIRY_DAYS
            )

        if existing:
            existing.lock_tier = tier
            existing.position = position
            existing.expires_at = expires_at
            safe_commit(
                f"update lock to {tier} for "
                f"{track_uri} in {playlist_id}",
                TrackLockError,
            )
            return existing.to_dict()

        return TrackLockService._create_lock(
            user_id, playlist_id, track_uri,
            position, tier,
        )

    @staticmethod
    def unlock(
        user_id: int,
        playlist_id: str,
        track_uri: str,
    ) -> bool:
        """
        Remove a lock for a specific track.

        Returns:
            True if a lock was removed, False if none existed.
        """
        count = TrackLock.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=playlist_id,
            track_uri=track_uri,
        ).delete()
        safe_commit(
            f"unlock {track_uri} in {playlist_id}",
            TrackLockError,
        )
        return count > 0

    @staticmethod
    def bulk_unlock(
        user_id: int,
        playlist_id: str,
        track_uris: Optional[List[str]] = None,
    ) -> int:
        """
        Unlock multiple (or all) tracks in a playlist.

        Args:
            track_uris: Specific URIs to unlock, or None
                to unlock all tracks in the playlist.

        Returns:
            Number of locks removed.
        """
        query = TrackLock.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=playlist_id,
        )
        if track_uris is not None:
            query = query.filter(
                TrackLock.track_uri.in_(track_uris)
            )
        count = query.delete(
            synchronize_session="fetch"
        )
        safe_commit(
            f"bulk unlock {count} tracks in {playlist_id}",
            TrackLockError,
        )
        return count

    @staticmethod
    def get_locks_for_playlist(
        user_id: int,
        playlist_id: str,
    ) -> List[TrackLock]:
        """Return all active (non-expired) locks for a playlist."""
        return TrackLock.query.filter(
            TrackLock.user_id == user_id,
            TrackLock.spotify_playlist_id == playlist_id,
            TrackLockService._active_filter(),
        ).all()

    @staticmethod
    def get_locked_positions(
        user_id: int,
        playlist_id: str,
    ) -> Dict[int, str]:
        """
        Return {position: track_uri} for all active locks.

        Used by shuffle algorithms to know which positions
        are immovable.
        """
        locks = TrackLockService.get_locks_for_playlist(
            user_id, playlist_id,
        )
        return {lock.position: lock.track_uri for lock in locks}

    @staticmethod
    def get_locked_uris(
        user_id: int,
        playlist_id: str,
    ) -> Set[str]:
        """
        Return set of track URIs that are actively locked.

        Used by rotation to exclude locked tracks from
        the eligible swap-out pool.
        """
        locks = TrackLockService.get_locks_for_playlist(
            user_id, playlist_id,
        )
        return {lock.track_uri for lock in locks}

    @staticmethod
    def is_locked(
        user_id: int,
        playlist_id: str,
        track_uri: str,
    ) -> bool:
        """Check if a specific track is actively locked."""
        lock = TrackLock.query.filter(
            TrackLock.user_id == user_id,
            TrackLock.spotify_playlist_id == playlist_id,
            TrackLock.track_uri == track_uri,
            TrackLockService._active_filter(),
        ).first()
        return lock is not None

    @staticmethod
    def cleanup_expired() -> int:
        """
        Delete all expired standard locks.

        Intended to be called by a scheduled cleanup job.

        Returns:
            Number of expired locks deleted.
        """
        now = datetime.now(timezone.utc)
        count = TrackLock.query.filter(
            TrackLock.expires_at.isnot(None),
            TrackLock.expires_at <= now,
        ).delete(synchronize_session="fetch")
        safe_commit(
            f"cleanup {count} expired track locks",
            TrackLockError,
        )
        return count

    @staticmethod
    def update_positions_after_reorder(
        user_id: int,
        playlist_id: str,
        new_uri_order: List[str],
    ) -> int:
        """
        Reconcile lock positions after a playlist reorder.

        Updates position for locks whose track still exists
        in the playlist. Deletes locks for tracks that were
        removed.

        Args:
            new_uri_order: The new ordered list of track URIs.

        Returns:
            Number of locks updated (not deleted).
        """
        locks = TrackLock.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=playlist_id,
        ).all()

        if not locks:
            return 0

        uri_to_position = {
            uri: idx
            for idx, uri in enumerate(new_uri_order)
        }

        updated = 0
        for lock in locks:
            new_pos = uri_to_position.get(lock.track_uri)
            if new_pos is None:
                db.session.delete(lock)
                logger.info(
                    "Deleted orphaned lock for %s in %s",
                    lock.track_uri, playlist_id,
                )
            else:
                if lock.position != new_pos:
                    lock.position = new_pos
                updated += 1

        safe_commit(
            f"reconcile lock positions in {playlist_id} "
            f"({updated} updated)",
            TrackLockError,
        )
        return updated

    # ---------------------------------------------------------
    # Graceful-fallback helpers for callers (executors, routes)
    # that should never crash when lock queries fail.
    # ---------------------------------------------------------

    @staticmethod
    def safe_get_locked_positions(
        user_id: int,
        playlist_id: str,
    ) -> Dict[int, str]:
        """get_locked_positions with graceful fallback to {}."""
        try:
            return TrackLockService.get_locked_positions(
                user_id, playlist_id
            )
        except Exception as e:
            db.session.rollback()
            logger.warning(
                "Failed to query track locks for "
                "%s: %s — proceeding without locks",
                playlist_id, e,
            )
            return {}

    @staticmethod
    def safe_get_locked_uris(
        user_id: int,
        playlist_id: str,
    ) -> Set[str]:
        """get_locked_uris with graceful fallback to empty set."""
        try:
            return TrackLockService.get_locked_uris(
                user_id, playlist_id
            )
        except Exception as e:
            db.session.rollback()
            logger.warning(
                "Failed to query track locks for "
                "%s: %s — proceeding without locks",
                playlist_id, e,
            )
            return set()

    @staticmethod
    def safe_reconcile_positions(
        user_id: int,
        playlist_id: str,
        new_uris: List[str],
    ) -> None:
        """update_positions_after_reorder with graceful fallback."""
        try:
            TrackLockService.update_positions_after_reorder(
                user_id, playlist_id, new_uris
            )
        except Exception as e:
            db.session.rollback()
            logger.warning(
                "Failed to reconcile locks after "
                "reorder for %s: %s",
                playlist_id, e,
            )
