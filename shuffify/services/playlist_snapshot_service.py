"""
Playlist snapshot service for capturing and restoring playlist states.

Handles CRUD operations for PlaylistSnapshot records, enabling users
to capture point-in-time snapshots of playlist track orderings and
restore them later.
"""

import logging
from typing import List, Optional

from shuffify.models.db import db, PlaylistSnapshot
from shuffify.enums import SnapshotType  # noqa: F401

logger = logging.getLogger(__name__)

# Default max snapshots if UserSettings is unavailable
DEFAULT_MAX_SNAPSHOTS_PER_PLAYLIST = 50


class PlaylistSnapshotError(Exception):
    """Base exception for playlist snapshot operations."""

    pass


class PlaylistSnapshotNotFoundError(PlaylistSnapshotError):
    """Raised when a snapshot cannot be found."""

    pass


class PlaylistSnapshotService:
    """Service for managing playlist snapshots."""

    @staticmethod
    def create_snapshot(
        user_id: int,
        playlist_id: str,
        playlist_name: str,
        track_uris: List[str],
        snapshot_type: str,
        trigger_description: Optional[str] = None,
    ) -> PlaylistSnapshot:
        """
        Create a new playlist snapshot.

        After creation, enforces the max_snapshots_per_playlist limit
        by deleting the oldest snapshots beyond the cap.

        Args:
            user_id: The internal database user ID.
            playlist_id: The Spotify playlist ID.
            playlist_name: Human-readable playlist name at time
                of snapshot.
            track_uris: Ordered list of track URIs.
            snapshot_type: One of the SnapshotType enum values.
            trigger_description: Optional description of what
                triggered the snapshot.

        Returns:
            The created PlaylistSnapshot instance.

        Raises:
            PlaylistSnapshotError: If creation fails.
        """
        try:
            snapshot = PlaylistSnapshot(
                user_id=user_id,
                playlist_id=playlist_id,
                playlist_name=playlist_name,
                track_count=len(track_uris),
                snapshot_type=snapshot_type,
                trigger_description=trigger_description,
            )
            snapshot.track_uris = track_uris

            db.session.add(snapshot)
            db.session.commit()

            logger.info(
                f"Created {snapshot_type} snapshot for user "
                f"{user_id}, playlist {playlist_id} "
                f"({len(track_uris)} tracks)"
            )

            # Enforce retention limit
            max_snapshots = (
                PlaylistSnapshotService._get_max_snapshots(
                    user_id
                )
            )
            PlaylistSnapshotService.cleanup_old_snapshots(
                user_id, playlist_id, max_snapshots
            )

            return snapshot

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to create snapshot: {e}",
                exc_info=True,
            )
            raise PlaylistSnapshotError(
                f"Failed to create snapshot: {e}"
            )

    @staticmethod
    def get_snapshots(
        user_id: int,
        playlist_id: str,
        limit: int = 20,
    ) -> List[PlaylistSnapshot]:
        """
        Get snapshots for a playlist, newest first.

        Args:
            user_id: The internal database user ID.
            playlist_id: The Spotify playlist ID.
            limit: Maximum number of snapshots to return.

        Returns:
            List of PlaylistSnapshot instances, most recent first.
        """
        return (
            PlaylistSnapshot.query.filter_by(
                user_id=user_id, playlist_id=playlist_id
            )
            .order_by(PlaylistSnapshot.created_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_snapshot(
        snapshot_id: int, user_id: int
    ) -> PlaylistSnapshot:
        """
        Get a specific snapshot by ID with ownership check.

        Args:
            snapshot_id: The snapshot database ID.
            user_id: The internal database user ID (for ownership).

        Returns:
            PlaylistSnapshot instance.

        Raises:
            PlaylistSnapshotNotFoundError: If not found or
                not owned.
        """
        snapshot = db.session.get(
            PlaylistSnapshot, snapshot_id
        )
        if not snapshot or snapshot.user_id != user_id:
            raise PlaylistSnapshotNotFoundError(
                f"Snapshot {snapshot_id} not found"
            )
        return snapshot

    @staticmethod
    def restore_snapshot(
        snapshot_id: int, user_id: int
    ) -> List[str]:
        """
        Get the track URIs from a snapshot for restoration.

        The caller (route or service) is responsible for applying
        the URIs to the Spotify playlist via the Spotify API.

        Args:
            snapshot_id: The snapshot database ID.
            user_id: The internal database user ID (for ownership).

        Returns:
            List of track URIs in the snapshot's order.

        Raises:
            PlaylistSnapshotNotFoundError: If not found or
                not owned.
        """
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user_id
        )
        logger.info(
            f"Restoring snapshot {snapshot_id} for user "
            f"{user_id}, playlist {snapshot.playlist_id} "
            f"({snapshot.track_count} tracks)"
        )
        return snapshot.track_uris

    @staticmethod
    def delete_snapshot(
        snapshot_id: int, user_id: int
    ) -> bool:
        """
        Delete a snapshot with ownership check.

        Args:
            snapshot_id: The snapshot database ID.
            user_id: The internal database user ID (for ownership).

        Returns:
            True if deleted successfully.

        Raises:
            PlaylistSnapshotNotFoundError: If not found or
                not owned.
            PlaylistSnapshotError: If deletion fails.
        """
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user_id
        )

        try:
            db.session.delete(snapshot)
            db.session.commit()
            logger.info(f"Deleted snapshot {snapshot_id}")
            return True

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to delete snapshot "
                f"{snapshot_id}: {e}",
                exc_info=True,
            )
            raise PlaylistSnapshotError(
                f"Failed to delete snapshot: {e}"
            )

    @staticmethod
    def cleanup_old_snapshots(
        user_id: int,
        playlist_id: str,
        max_count: int,
    ) -> int:
        """
        Enforce retention limit by deleting oldest snapshots.

        Keeps the most recent `max_count` snapshots and deletes
        any older ones.

        Args:
            user_id: The internal database user ID.
            playlist_id: The Spotify playlist ID.
            max_count: Maximum number of snapshots to retain.

        Returns:
            Number of snapshots deleted.
        """
        all_snapshots = (
            PlaylistSnapshot.query.filter_by(
                user_id=user_id, playlist_id=playlist_id
            )
            .order_by(PlaylistSnapshot.created_at.desc())
            .all()
        )

        if len(all_snapshots) <= max_count:
            return 0

        to_delete = all_snapshots[max_count:]
        deleted_count = 0

        try:
            for snapshot in to_delete:
                db.session.delete(snapshot)
                deleted_count += 1
            db.session.commit()

            logger.info(
                f"Cleaned up {deleted_count} old snapshots "
                f"for user {user_id}, playlist {playlist_id}"
            )
            return deleted_count

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to cleanup snapshots: {e}",
                exc_info=True,
            )
            return 0

    @staticmethod
    def _get_max_snapshots(user_id: int) -> int:
        """
        Get the max snapshots setting for a user.

        Reads from UserSettings if available (Phase 3),
        otherwise returns the default.

        Args:
            user_id: The internal database user ID.

        Returns:
            Maximum number of snapshots per playlist.
        """
        try:
            from shuffify.models.db import UserSettings

            settings = UserSettings.query.filter_by(
                user_id=user_id
            ).first()
            if (
                settings
                and settings.max_snapshots_per_playlist
            ):
                return settings.max_snapshots_per_playlist
        except (ImportError, Exception):
            pass

        return DEFAULT_MAX_SNAPSHOTS_PER_PLAYLIST

    @staticmethod
    def is_auto_snapshot_enabled(user_id: int) -> bool:
        """
        Check if auto-snapshot is enabled for a user.

        Reads from UserSettings if available (Phase 3),
        otherwise defaults to True.

        Args:
            user_id: The internal database user ID.

        Returns:
            True if auto-snapshots are enabled.
        """
        try:
            from shuffify.models.db import UserSettings

            settings = UserSettings.query.filter_by(
                user_id=user_id
            ).first()
            if settings is not None:
                return settings.auto_snapshot_enabled
        except (ImportError, Exception):
            pass

        return True
