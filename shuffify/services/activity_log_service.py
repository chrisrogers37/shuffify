"""
Activity log service for recording and querying user actions.

All logging methods are designed to be non-blocking: if logging fails,
the error is caught and logged but never propagated to the caller.
This ensures that activity tracking never breaks primary operations.
The one exception is a staged log (``commit=False``), which is part of
its caller's transaction and lets errors propagate.
"""

import logging
from typing import Any, Dict, List, Optional

from shuffify.models.db import ActivityLog, db
from shuffify.services.base import commit_or_stage

logger = logging.getLogger(__name__)


class ActivityLogError(Exception):
    """Base exception for activity log operations."""

    pass


class ActivityLogService:
    """Service for recording and querying user activity."""

    @staticmethod
    def log(
        user_id: int,
        activity_type: str,
        description: str,
        playlist_id: Optional[str] = None,
        playlist_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        commit: bool = True,
        session=None,
    ) -> Optional[ActivityLog]:
        """
        Record a user activity. Non-blocking when it commits: never raises.

        Args:
            user_id: The internal database user ID.
            activity_type: One of the ActivityType enum values.
            description: Human-readable description of the action.
            playlist_id: Spotify playlist ID (if applicable).
            playlist_name: Playlist name (if applicable).
            metadata: Additional context as a JSON-serializable
                dict.
            commit: False stages the row in the caller's transaction
                (add and flush); errors then propagate.

        Returns:
            The created ActivityLog instance, or None if failed.
        """
        if session is not None and commit:
            raise ValueError("An explicit activity session requires commit=False.")
        storage = session if session is not None else db.session
        try:
            activity = ActivityLog(
                user_id=user_id,
                activity_type=activity_type,
                description=description[:500],
                playlist_id=playlist_id,
                playlist_name=playlist_name,
                metadata_json=metadata,
            )
            storage.add(activity)
            if session is not None:
                storage.flush()
            else:
                commit_or_stage(
                    commit,
                    f"log activity {activity_type} "
                    f"for user {user_id}",
                    ActivityLogError,
                )
            return activity
        except Exception:
            if not commit:
                raise
            # Non-blocking: activity logging must never
            # propagate errors to callers
            return None

    @staticmethod
    def get_recent(
        user_id: int,
        limit: int = 20,
        activity_type: Optional[str] = None,
    ) -> List[ActivityLog]:
        """
        Get recent activity for a user.

        Args:
            user_id: The internal database user ID.
            limit: Maximum number of records to return.
            activity_type: Optional filter by activity type.

        Returns:
            List of ActivityLog instances, most recent first.
        """
        try:
            query = ActivityLog.query.filter_by(
                user_id=user_id
            )
            if activity_type:
                query = query.filter_by(
                    activity_type=activity_type
                )
            return (
                query.order_by(
                    ActivityLog.created_at.desc()
                )
                .limit(limit)
                .all()
            )
        except Exception as e:
            logger.warning(
                "Failed to get recent activity "
                "for user %s: %s",
                user_id,
                e,
            )
            return []
