"""
Playlist snapshot service for capturing and restoring playlist states.

Handles CRUD operations for PlaylistSnapshot records, enabling users
to capture point-in-time snapshots of playlist track orderings and
restore them later.
"""

import logging
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from shuffify.enums import SnapshotType  # noqa: F401
from shuffify.models.db import PlaylistSnapshot, db
from shuffify.services.base import commit_failure, get_owned_entity, safe_commit
from shuffify.services.storage_limits import StorageLimitReached, check_storage_growth, lock_storage_inventory

logger = logging.getLogger(__name__)

# The snapshot limit for a user with no settings row, and for a settings read
# that fails without a caller's session.
FALLBACK_SNAPSHOTS_LIMIT = 50


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
        job_execution_id: Optional[int] = None,
        *,
        session=None,
        commit=True,
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
        return PlaylistSnapshotService.create_snapshots(
            user_id, [(playlist_id, playlist_name, track_uris)], snapshot_type, trigger_description,
            job_execution_id, session=session, commit=commit,
        )[0]

    @staticmethod
    def create_snapshots(user_id, playlists, snapshot_type, trigger_description=None, job_execution_id=None,
                         *, session=None, commit=True):
        """Store one snapshot per destination as a unit. The result follows the caller's playlist order."""
        if session is not None and commit:
            raise ValueError("An explicit snapshot session requires commit=False.")
        storage = session if session is not None else db.session
        snapshots = []
        for playlist_id, playlist_name, track_uris in playlists:
            snapshot = PlaylistSnapshot(
                user_id=user_id, playlist_id=playlist_id, playlist_name=playlist_name,
                track_count=len(track_uris), snapshot_type=snapshot_type,
                trigger_description=trigger_description, job_execution_id=job_execution_id,
            )
            snapshot.track_uris = track_uris
            snapshots.append(snapshot)
        playlist_ids = {row.playlist_id for row in snapshots}
        if len(playlist_ids) != len(snapshots):
            raise ValueError("A snapshot set requires distinct playlists.")
        if not snapshots:
            return []
        operation = f"create {snapshot_type} snapshot for user {user_id}"
        try:
            lock_storage_inventory(storage, user_id)
            max_count = PlaylistSnapshotService._get_max_snapshots(user_id, session=session)
            counts = dict(storage.execute(select(PlaylistSnapshot.playlist_id, func.count()).where(
                PlaylistSnapshot.user_id == user_id, PlaylistSnapshot.playlist_id.in_(playlist_ids),
            ).group_by(PlaylistSnapshot.playlist_id)).all())
            # Each destination ends with min(previous + 1, max_count) rows. A lowered playlist
            # setting can free multiple slots in the same save, independent of destination order.
            growth = sum(min(1, max_count - counts.get(key, 0)) for key in playlist_ids)
            check_storage_growth(storage, PlaylistSnapshot, user_id, playlist_ids, row_growth=growth)
            storage.add_all(snapshots)
            rotation = [row for row in snapshots if counts.get(row.playlist_id, 0) >= max_count]
            if rotation:
                storage.flush()
                for row in rotation:
                    PlaylistSnapshotService.cleanup_old_snapshots(
                        user_id, row.playlist_id, max_count, session=storage, commit=False, keep_snapshot_id=row.id,
                    )
            if commit:
                safe_commit(operation, PlaylistSnapshotError)
            else:
                storage.flush()
        except StorageLimitReached:
            if commit:
                storage.rollback()
            raise
        except SQLAlchemyError as error:
            if not commit:
                raise
            storage.rollback()
            logger.exception("Failed to %s", operation)
            raise commit_failure(PlaylistSnapshotError, operation) from error

        return snapshots

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
            .order_by(PlaylistSnapshot.created_at.desc(), PlaylistSnapshot.id.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_snapshot(
        snapshot_id: int, user_id: int, *, session=None,
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
        if session is not None:
            snapshot = session.query(PlaylistSnapshot).filter_by(id=snapshot_id, user_id=user_id).first()
            if snapshot is None:
                raise PlaylistSnapshotNotFoundError(f"PlaylistSnapshot {snapshot_id} not found")
            return snapshot
        return get_owned_entity(
            PlaylistSnapshot,
            snapshot_id,
            user_id,
            PlaylistSnapshotNotFoundError,
        )

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
        lock_storage_inventory(db.session, user_id)
        snapshot = PlaylistSnapshotService.get_snapshot(snapshot_id, user_id)

        db.session.delete(snapshot)
        safe_commit(
            f"delete snapshot {snapshot_id}",
            PlaylistSnapshotError,
        )
        return True

    @staticmethod
    def cleanup_old_snapshots(
        user_id: int,
        playlist_id: str,
        max_count: int,
        *,
        session=None,
        commit=True,
        keep_snapshot_id=None,
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
        if session is not None and commit:
            raise ValueError("An explicit snapshot session requires commit=False.")
        storage = session if session is not None else db.session
        candidates = storage.query(PlaylistSnapshot).filter_by(user_id=user_id, playlist_id=playlist_id)
        if keep_snapshot_id is not None:
            # The new save owns one slot even if an older writer's wall clock was ahead.
            candidates = candidates.filter(PlaylistSnapshot.id != keep_snapshot_id)
            max_count -= 1
        keep_ids = candidates.with_entities(PlaylistSnapshot.id).order_by(
            PlaylistSnapshot.created_at.desc(), PlaylistSnapshot.id.desc(),
        ).limit(max_count).subquery()
        deleted_count = candidates.filter(
            ~PlaylistSnapshot.id.in_(storage.query(keep_ids)),
        ).delete(synchronize_session="fetch")

        if not commit:
            storage.flush()
        else:
            # DELETE starts a physical transaction even when it matches no
            # snapshots. Always finish it before another independent queue
            # transaction borrows this connection (notably SQLite StaticPool).
            try:
                safe_commit(
                    f"cleanup {deleted_count} old snapshots "
                    f"for user {user_id}, "
                    f"playlist {playlist_id}",
                    PlaylistSnapshotError,
                )
            except PlaylistSnapshotError:
                return 0

        return deleted_count

    @staticmethod
    def _get_max_snapshots(user_id: int, *, session=None) -> int:
        """
        Get the max snapshots setting for a user.

        Reads from UserSettings if available,
        otherwise returns FALLBACK_SNAPSHOTS_LIMIT.

        Args:
            user_id: The internal database user ID.

        Returns:
            Maximum number of snapshots per playlist.
        """
        try:
            from shuffify.models.db import UserSettings

            storage = session if session is not None else db.session
            settings = storage.query(UserSettings).filter_by(
                user_id=user_id
            ).first()
            if (
                settings
                and settings.max_snapshots_per_playlist
            ):
                return settings.max_snapshots_per_playlist
        except (ImportError, Exception) as err:
            if session is not None:
                raise
            # Database errors can embed passwords, DSNs and hostnames; log only their type.
            logger.warning(
                "Could not read the snapshot limit for user %s; falling back to %s (%s)",
                user_id,
                FALLBACK_SNAPSHOTS_LIMIT,
                type(err).__name__,
            )

        return FALLBACK_SNAPSHOTS_LIMIT

    @staticmethod
    def is_auto_snapshot_enabled(user_id: int, *, session=None) -> bool:
        """
        Check if auto-snapshot is enabled for a user.

        Reads from UserSettings if available,
        otherwise defaults to True.

        Args:
            user_id: The internal database user ID.

        Returns:
            True if auto-snapshots are enabled.
        """
        try:
            from shuffify.models.db import UserSettings

            storage = session if session is not None else db.session
            settings = storage.query(UserSettings).filter_by(
                user_id=user_id
            ).first()
            if settings is not None:
                return settings.auto_snapshot_enabled
        except (ImportError, Exception):
            if session is not None:
                raise

        return True
