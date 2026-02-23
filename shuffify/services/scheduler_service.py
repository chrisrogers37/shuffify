"""
Scheduler service for CRUD operations on Schedule models.

Handles creating, reading, updating, and deleting scheduled job
configurations. Does NOT execute jobs -- that is handled by
JobExecutorService.
"""

import logging
from typing import List, Optional, Dict, Any

from shuffify.models.db import db, Schedule
from shuffify.services.base import safe_commit

logger = logging.getLogger(__name__)


class ScheduleError(Exception):
    """Base exception for schedule operations."""

    pass


class ScheduleNotFoundError(ScheduleError):
    """Raised when a schedule is not found."""

    pass


class ScheduleLimitError(ScheduleError):
    """Raised when user exceeds max schedule limit."""

    pass


class SchedulerService:
    """Service for managing scheduled job configurations."""

    MAX_SCHEDULES_PER_USER = 5

    @staticmethod
    def get_user_schedules(user_id: int) -> List[Schedule]:
        """Get all schedules for a user."""
        schedules = (
            Schedule.query.filter_by(user_id=user_id)
            .order_by(Schedule.created_at.desc())
            .all()
        )
        logger.debug(
            f"Retrieved {len(schedules)} schedules "
            f"for user {user_id}"
        )
        return schedules

    @staticmethod
    def get_schedule(
        schedule_id: int, user_id: int
    ) -> Schedule:
        """
        Get a single schedule, verifying ownership.

        Raises:
            ScheduleNotFoundError: If not found or wrong user.
        """
        schedule = Schedule.query.filter_by(
            id=schedule_id, user_id=user_id
        ).first()

        if not schedule:
            raise ScheduleNotFoundError(
                f"Schedule {schedule_id} not found "
                f"for user {user_id}"
            )

        return schedule

    @staticmethod
    def create_schedule(
        user_id: int,
        job_type: str,
        target_playlist_id: str,
        target_playlist_name: str,
        schedule_type: str,
        schedule_value: str,
        source_playlist_ids: Optional[List[str]] = None,
        algorithm_name: Optional[str] = None,
        algorithm_params: Optional[Dict[str, Any]] = None,
    ) -> Schedule:
        """
        Create a new scheduled job.

        Raises:
            ScheduleLimitError: If user has reached max schedules.
            ScheduleError: If creation fails.
        """
        existing_count = Schedule.query.filter_by(
            user_id=user_id
        ).count()
        if (
            existing_count
            >= SchedulerService.MAX_SCHEDULES_PER_USER
        ):
            raise ScheduleLimitError(
                f"Maximum of "
                f"{SchedulerService.MAX_SCHEDULES_PER_USER} "
                f"schedules per user reached"
            )

        schedule = Schedule(
            user_id=user_id,
            job_type=job_type,
            target_playlist_id=target_playlist_id,
            target_playlist_name=target_playlist_name,
            source_playlist_ids=source_playlist_ids or [],
            algorithm_name=algorithm_name,
            algorithm_params=algorithm_params or {},
            schedule_type=schedule_type,
            schedule_value=schedule_value,
            is_enabled=True,
        )

        db.session.add(schedule)
        safe_commit(
            f"create schedule for user {user_id}: "
            f"{job_type} on {target_playlist_name}",
            ScheduleError,
        )
        return schedule

    @staticmethod
    def update_schedule(
        schedule_id: int,
        user_id: int,
        **kwargs,
    ) -> Schedule:
        """
        Update an existing schedule.

        Only provided keyword arguments in the allowed set
        are updated.

        Raises:
            ScheduleNotFoundError: If not found.
            ScheduleError: If update fails.
        """
        schedule = SchedulerService.get_schedule(
            schedule_id, user_id
        )

        allowed_fields = {
            "job_type",
            "target_playlist_id",
            "target_playlist_name",
            "source_playlist_ids",
            "algorithm_name",
            "algorithm_params",
            "schedule_type",
            "schedule_value",
            "is_enabled",
        }

        for key, value in kwargs.items():
            if key in allowed_fields:
                setattr(schedule, key, value)

        safe_commit(
            f"update schedule {schedule_id}",
            ScheduleError,
        )
        return schedule

    @staticmethod
    def delete_schedule(
        schedule_id: int, user_id: int
    ) -> None:
        """
        Delete a schedule and its execution history.

        Raises:
            ScheduleNotFoundError: If not found.
            ScheduleError: If deletion fails.
        """
        schedule = SchedulerService.get_schedule(
            schedule_id, user_id
        )

        from shuffify.models.db import JobExecution

        JobExecution.query.filter_by(
            schedule_id=schedule_id
        ).delete()

        db.session.delete(schedule)
        safe_commit(
            f"delete schedule {schedule_id}",
            ScheduleError,
        )

    @staticmethod
    def toggle_schedule(
        schedule_id: int, user_id: int
    ) -> Schedule:
        """
        Toggle a schedule's enabled/disabled state.

        Raises:
            ScheduleNotFoundError: If not found.
        """
        schedule = SchedulerService.get_schedule(
            schedule_id, user_id
        )
        schedule.is_enabled = not schedule.is_enabled
        safe_commit(
            f"toggle schedule {schedule_id} to "
            f"{'enabled' if schedule.is_enabled else 'disabled'}",
            ScheduleError,
        )
        return schedule

    @staticmethod
    def get_execution_history(
        schedule_id: int, user_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get recent execution history for a schedule."""
        # Verify ownership
        SchedulerService.get_schedule(schedule_id, user_id)

        from shuffify.models.db import JobExecution

        executions = (
            JobExecution.query.filter_by(
                schedule_id=schedule_id
            )
            .order_by(JobExecution.started_at.desc())
            .limit(limit)
            .all()
        )

        return [ex.to_dict() for ex in executions]
