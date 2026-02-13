"""
Dashboard service for aggregating personalized dashboard data.

Combines data from ActivityLogService, SchedulerService, and
database models to provide a single dashboard data payload.
This service performs read-only operations only.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from shuffify.models.db import db, Schedule, JobExecution

logger = logging.getLogger(__name__)


class DashboardError(Exception):
    """Base exception for dashboard service operations."""

    pass


class DashboardService:
    """
    Service for aggregating personalized dashboard data.

    All methods are static and read-only. Failures in any
    subsection are caught and return empty/default values
    so the dashboard always renders.
    """

    @staticmethod
    def get_dashboard_data(
        user_id: int,
        last_login_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Aggregate all dashboard data for a user.

        Args:
            user_id: The database user ID.
            last_login_at: The user's previous login timestamp.
                If None, the user is treated as a first-time
                visitor.

        Returns:
            Dictionary with keys:
                - is_returning_user (bool)
                - recent_activity (list of dicts)
                - activity_since_last_login (list of dicts)
                - activity_since_last_login_count (int)
                - quick_stats (dict with counts)
                - active_schedules (list of dicts)
                - recent_job_executions (list of dicts)
        """
        data = {
            "is_returning_user": last_login_at is not None,
            "recent_activity": [],
            "activity_since_last_login": [],
            "activity_since_last_login_count": 0,
            "quick_stats": DashboardService._empty_stats(),
            "active_schedules": [],
            "recent_job_executions": [],
        }

        data["recent_activity"] = (
            DashboardService._get_recent_activity(
                user_id, limit=10
            )
        )

        if last_login_at:
            data["activity_since_last_login"] = (
                DashboardService._get_activity_since(
                    user_id, last_login_at, limit=20
                )
            )
            data["activity_since_last_login_count"] = len(
                data["activity_since_last_login"]
            )

        data["quick_stats"] = (
            DashboardService._get_quick_stats(user_id)
        )

        data["active_schedules"] = (
            DashboardService._get_active_schedules(user_id)
        )

        data["recent_job_executions"] = (
            DashboardService._get_recent_executions(
                user_id, limit=5
            )
        )

        return data

    @staticmethod
    def _get_recent_activity(
        user_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get the most recent activity log entries."""
        try:
            from shuffify.models.db import ActivityLog

            logs = (
                ActivityLog.query.filter_by(
                    user_id=user_id
                )
                .order_by(ActivityLog.created_at.desc())
                .limit(limit)
                .all()
            )
            return [log.to_dict() for log in logs]
        except Exception as e:
            logger.warning(
                "Failed to fetch recent activity for "
                "user %s: %s",
                user_id,
                e,
            )
            return []

    @staticmethod
    def _get_activity_since(
        user_id: int,
        since: datetime,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get activity that occurred after a timestamp."""
        try:
            from shuffify.models.db import ActivityLog

            logs = (
                ActivityLog.query.filter(
                    ActivityLog.user_id == user_id,
                    ActivityLog.created_at > since,
                )
                .order_by(ActivityLog.created_at.desc())
                .limit(limit)
                .all()
            )
            return [log.to_dict() for log in logs]
        except Exception as e:
            logger.warning(
                "Failed to fetch activity since "
                "%s for user %s: %s",
                since,
                user_id,
                e,
            )
            return []

    @staticmethod
    def _get_quick_stats(
        user_id: int,
    ) -> Dict[str, int]:
        """
        Calculate quick stats for the user.

        Returns dict with:
            - total_shuffles
            - total_scheduled_runs
            - total_snapshots
            - active_schedule_count
        """
        try:
            from shuffify.models.db import (
                ActivityLog,
                PlaylistSnapshot,
            )

            total_shuffles = (
                ActivityLog.query.filter(
                    ActivityLog.user_id == user_id,
                    ActivityLog.activity_type == "shuffle",
                ).count()
            )

            total_scheduled_runs = (
                db.session.query(JobExecution)
                .join(Schedule)
                .filter(Schedule.user_id == user_id)
                .count()
            )

            total_snapshots = (
                PlaylistSnapshot.query.filter_by(
                    user_id=user_id
                ).count()
            )

            active_schedule_count = (
                Schedule.query.filter_by(
                    user_id=user_id, is_enabled=True
                ).count()
            )

            return {
                "total_shuffles": total_shuffles,
                "total_scheduled_runs": (
                    total_scheduled_runs
                ),
                "total_snapshots": total_snapshots,
                "active_schedule_count": (
                    active_schedule_count
                ),
            }
        except Exception as e:
            logger.warning(
                "Failed to calculate stats for "
                "user %s: %s",
                user_id,
                e,
            )
            return DashboardService._empty_stats()

    @staticmethod
    def _get_active_schedules(
        user_id: int,
    ) -> List[Dict[str, Any]]:
        """Get all active (enabled) schedules."""
        try:
            schedules = (
                Schedule.query.filter_by(
                    user_id=user_id, is_enabled=True
                )
                .order_by(Schedule.created_at.desc())
                .all()
            )
            return [s.to_dict() for s in schedules]
        except Exception as e:
            logger.warning(
                "Failed to fetch active schedules for "
                "user %s: %s",
                user_id,
                e,
            )
            return []

    @staticmethod
    def _get_recent_executions(
        user_id: int, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Get recent job executions across all schedules."""
        try:
            executions = (
                db.session.query(JobExecution)
                .join(Schedule)
                .filter(Schedule.user_id == user_id)
                .order_by(JobExecution.started_at.desc())
                .limit(limit)
                .all()
            )
            result = []
            for ex in executions:
                ex_dict = ex.to_dict()
                ex_dict["schedule_name"] = (
                    ex.schedule.target_playlist_name
                    if ex.schedule
                    else "Unknown"
                )
                ex_dict["job_type"] = (
                    ex.schedule.job_type
                    if ex.schedule
                    else "unknown"
                )
                result.append(ex_dict)
            return result
        except Exception as e:
            logger.warning(
                "Failed to fetch recent executions for "
                "user %s: %s",
                user_id,
                e,
            )
            return []

    @staticmethod
    def _empty_stats() -> Dict[str, int]:
        """Return a zeroed-out stats dictionary."""
        return {
            "total_shuffles": 0,
            "total_scheduled_runs": 0,
            "total_snapshots": 0,
            "active_schedule_count": 0,
        }
