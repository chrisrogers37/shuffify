"""Existing local side effects staged in a fenced operation transaction."""

import logging
from copy import deepcopy

from shuffify.services.activity_log_service import ActivityLogService
from shuffify.services.playlist_snapshot_service import PlaylistSnapshotService
from shuffify.services.track_lock_service import TrackLockService

logger = logging.getLogger(__name__)


class OperationBookkeeping:
    """Use only inside repository.local_step; these helpers never commit or call Spotify."""

    def __init__(self, session, user_id, operation_id):
        self.session = session
        self.user_id = user_id
        self.operation_id = operation_id

    def optional(self, callback, *, fallback=None):
        """Roll back an optional effect while retaining a durable authored failure receipt."""
        from shuffify.services.operations.control import propagate_control
        from shuffify.services.state_service import StaleHistoryError

        try:
            with self.session.begin_nested():
                return callback()
        except StaleHistoryError:
            raise
        except Exception as error:
            propagate_control(error)
            logger.warning("Optional operation bookkeeping was unavailable", exc_info=True)
            return deepcopy(fallback)

    def snapshot(self, playlist_id, playlist_name, track_uris, snapshot_type, trigger_description=None, *, auto=False):
        """Capture and prune snapshots atomically, tagging the accepted operation's ID."""
        if auto and (not track_uris or not PlaylistSnapshotService.is_auto_snapshot_enabled(
            self.user_id, session=self.session,
        )):
            return None
        snapshot = PlaylistSnapshotService.create_snapshot(
            self.user_id, playlist_id, playlist_name, list(track_uris), snapshot_type,
            trigger_description=trigger_description, job_execution_id=self.operation_id,
            session=self.session, commit=False,
        )
        return snapshot.to_dict()

    def activity(self, activity_type, description, *, playlist_id=None, playlist_name=None, metadata=None):
        """Record the existing activity DTO in the same transaction as its step receipt."""
        activity = ActivityLogService.log(
            self.user_id, activity_type, description, playlist_id=playlist_id, playlist_name=playlist_name,
            metadata=deepcopy(metadata), session=self.session, commit=False,
        )
        return activity.to_dict()

    def reconcile_locks(self, playlist_id, track_uris):
        """Apply the existing orphan-removal and position rules without a separate commit."""
        return TrackLockService.update_positions_after_reorder(
            self.user_id, playlist_id, list(track_uris), session=self.session, commit=False,
        )
