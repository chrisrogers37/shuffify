"""
Scheduler service for CRUD operations on Schedule models.

Handles creating, reading, updating, and deleting scheduled job
configurations. Does NOT execute jobs -- that is handled by
JobExecutorService.
"""

import logging
from typing import List, Optional, Dict, Any

from shuffify.models.db import db, Schedule, JobExecution
from shuffify.services.base import safe_commit, get_owned_entity

logger = logging.getLogger(__name__)


class ScheduleError(Exception):
    """Base exception for schedule operations."""

    pass


class ScheduleNotFoundError(ScheduleError):
    """Raised when a schedule is not found."""

    pass


class SchedulerService:
    """Service for managing scheduled job configurations."""

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
    def get_schedules_for_playlist(
        user_id: int, playlist_id: str
    ) -> List[Schedule]:
        """Get all schedules for a specific playlist."""
        return (
            Schedule.query.filter_by(
                user_id=user_id,
                target_playlist_id=playlist_id,
            )
            .order_by(Schedule.created_at.desc())
            .all()
        )

    @staticmethod
    def get_schedule(
        schedule_id: int, user_id: int
    ) -> Schedule:
        """
        Get a single schedule, verifying ownership.

        Raises:
            ScheduleNotFoundError: If not found or wrong user.
        """
        return get_owned_entity(
            Schedule, schedule_id, user_id,
            ScheduleNotFoundError,
        )

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
        register: bool = True,
    ) -> Schedule:
        """
        Create a new scheduled job and register it with APScheduler.

        Args:
            register: Register the job with APScheduler after creation (the
                default). Pass ``False`` for bulk/startup paths that register
                jobs separately, so an enabled schedule is never left without a
                running job (SR-013).

        Raises:
            ScheduleError: If creation fails.
        """
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
        if register:
            SchedulerService._sync_apscheduler_job(schedule)
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
        SchedulerService._sync_apscheduler_job(schedule)
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

        JobExecution.query.filter_by(
            schedule_id=schedule_id
        ).delete()

        db.session.delete(schedule)
        safe_commit(
            f"delete schedule {schedule_id}",
            ScheduleError,
        )
        # Remove the APScheduler job only after the DB delete commits, so a
        # failed delete never orphans a schedule row with no running job.
        SchedulerService._remove_apscheduler_job(schedule_id)

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
        SchedulerService._sync_apscheduler_job(schedule)
        return schedule

    @staticmethod
    def get_execution_history(
        schedule_id: int, user_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get recent execution history for a schedule."""
        # Verify ownership
        SchedulerService.get_schedule(schedule_id, user_id)

        executions = (
            JobExecution.query.filter_by(
                schedule_id=schedule_id
            )
            .order_by(JobExecution.started_at.desc())
            .limit(limit)
            .all()
        )

        return [ex.to_dict() for ex in executions]

    @staticmethod
    def _sync_apscheduler_job(schedule) -> None:
        """Register (enabled) or unregister (disabled) the schedule's
        APScheduler job so the running jobs always match the DB state.

        Best-effort: a scheduler error is logged, not raised — the database is
        the source of truth and enabled jobs re-register on next startup.
        Imported lazily to avoid a circular import with ``shuffify.scheduler``.
        """
        try:
            from shuffify.scheduler import (
                add_job_for_schedule,
                remove_job_for_schedule,
            )

            if schedule.is_enabled:
                add_job_for_schedule(schedule)
            else:
                remove_job_for_schedule(schedule.id)
        except Exception as e:
            logger.warning(
                "APScheduler sync failed for schedule %s: %s",
                getattr(schedule, "id", "?"),
                e,
            )

    @staticmethod
    def _remove_apscheduler_job(schedule_id: int) -> None:
        """Remove a schedule's APScheduler job (best-effort)."""
        try:
            from shuffify.scheduler import remove_job_for_schedule

            remove_job_for_schedule(schedule_id)
        except Exception as e:
            logger.warning(
                "APScheduler job removal failed for schedule %s: %s",
                schedule_id,
                e,
            )
