"""
Activity log service for recording and querying user actions.

All logging methods are designed to be non-blocking: if logging fails,
the error is caught and logged but never propagated to the caller.
This ensures that activity tracking never breaks primary operations.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from shuffify.models.db import db, ActivityLog

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
    ) -> Optional[ActivityLog]:
        """
        Record a user activity. Non-blocking: never raises.

        Args:
            user_id: The internal database user ID.
            activity_type: One of the ActivityType enum values.
            description: Human-readable description of the action.
            playlist_id: Spotify playlist ID (if applicable).
            playlist_name: Playlist name (if applicable).
            metadata: Additional context as a JSON-serializable
                dict.

        Returns:
            The created ActivityLog instance, or None if failed.
        """
        try:
            activity = ActivityLog(
                user_id=user_id,
                activity_type=activity_type,
                description=description[:500],
                playlist_id=playlist_id,
                playlist_name=playlist_name,
                metadata_json=metadata,
            )
            db.session.add(activity)
            db.session.commit()
            logger.debug(
                "Activity logged: %s for user %s",
                activity_type,
                user_id,
            )
            return activity
        except Exception as e:
            db.session.rollback()
            logger.warning(
                "Failed to log activity "
                "(%s for user %s): %s",
                activity_type,
                user_id,
                e,
            )
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

    @staticmethod
    def get_activity_since(
        user_id: int,
        since: datetime,
    ) -> List[ActivityLog]:
        """
        Get all activity for a user since a given datetime.

        Args:
            user_id: The internal database user ID.
            since: The datetime cutoff (UTC).

        Returns:
            List of ActivityLog instances, most recent first.
        """
        try:
            return (
                ActivityLog.query.filter(
                    ActivityLog.user_id == user_id,
                    ActivityLog.created_at >= since,
                )
                .order_by(ActivityLog.created_at.desc())
                .all()
            )
        except Exception as e:
            logger.warning(
                "Failed to get activity since "
                "%s for user %s: %s",
                since,
                user_id,
                e,
            )
            return []

    @staticmethod
    def get_activity_summary(
        user_id: int,
        days: int = 30,
    ) -> Dict[str, int]:
        """
        Get aggregated activity counts by type for a user.

        Args:
            user_id: The internal database user ID.
            days: Number of days to look back.

        Returns:
            Dictionary mapping activity_type to count.
        """
        try:
            since = datetime.now(
                timezone.utc
            ) - timedelta(days=days)
            results = (
                db.session.query(
                    ActivityLog.activity_type,
                    db.func.count(ActivityLog.id),
                )
                .filter(
                    ActivityLog.user_id == user_id,
                    ActivityLog.created_at >= since,
                )
                .group_by(ActivityLog.activity_type)
                .all()
            )
            return {
                activity_type: count
                for activity_type, count in results
            }
        except Exception as e:
            logger.warning(
                "Failed to get activity summary "
                "for user %s: %s",
                user_id,
                e,
            )
            return {}
