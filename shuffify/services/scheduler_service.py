"""
Scheduler service for CRUD operations on Schedule models.

Handles creating, reading, updating, and deleting scheduled job
configurations. The worker admits due runs into the durable operation queue.
"""

import logging
from contextlib import ExitStack, contextmanager
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from shuffify.models.db import JobExecution, PlaylistSnapshot, Schedule, db
from shuffify.services.base import get_owned_entity, safe_commit
from shuffify.services.playlist_lock import playlist_configuration_lock, playlist_lock

logger = logging.getLogger(__name__)
LOCK_WAIT_S = 2.0


class ScheduleError(Exception):
    """Base exception for schedule operations."""

    pass


class ScheduleNotFoundError(ScheduleError):
    """Raised when a schedule is not found."""

    pass


class ScheduleBusyError(ScheduleError):
    """The target lock could not be acquired before a legacy mutation."""

    pass


@contextmanager
def _locked_exact_schedule(schedule_id, user_id, *, destination=None, execution=False):
    """Re-read an owned row under its target lock before a legacy mutation.

    A target move locks both playlists in stable order. Definition edits take
    execution locks before configuration locks so an active runner cannot
    observe options changing between its transaction boundaries. If another writer moved
    the row between the initial lookup and lock acquisition, fail rather than
    mutating it under the wrong target's lock.
    """
    initial = SchedulerService.get_schedule(schedule_id, user_id)
    original_target = initial.target_playlist_id
    targets = sorted({original_target, destination or original_target})
    with ExitStack() as stack:
        if execution:
            for target in targets:
                acquired = stack.enter_context(playlist_lock(target, timeout_s=LOCK_WAIT_S))
                if not acquired:
                    raise ScheduleBusyError("A routine is using this playlist. Try again.")
        for target in targets:
            acquired = stack.enter_context(playlist_configuration_lock(target, timeout_s=LOCK_WAIT_S))
            if not acquired:
                raise ScheduleBusyError("A routine is using this playlist. Try again.")
        db.session.expire_all()
        schedule = SchedulerService.get_schedule(schedule_id, user_id)
        if schedule.target_playlist_id != original_target:
            raise ScheduleError("Schedule changed while it was being updated. Try again.")
        yield schedule


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
        timezone: Optional[str] = None,
    ) -> Schedule:
        """
        Save a schedule and request a worker refresh.

        Args:
            register: Send an additional best-effort refresh hint after creation.
                Transactional definition notifications are always published;
                registration is reported separately from worker memory.

        Raises:
            ScheduleError: If creation fails.
        """
        with playlist_configuration_lock(target_playlist_id, timeout_s=LOCK_WAIT_S) as acquired:
            if not acquired:
                raise ScheduleBusyError("A routine is using this playlist. Try again.")
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
                timezone=timezone,
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
        allowed_fields = {
            "job_type",
            "target_playlist_id",
            "target_playlist_name",
            "source_playlist_ids",
            "algorithm_name",
            "algorithm_params",
            "schedule_type",
            "schedule_value",
            "timezone",
            "is_enabled",
        }
        # A state-only Pause/Resume must remain available while a runner owns
        # the execution lock. All definition edits still wait for that runner.
        definition_edit = (set(kwargs) & allowed_fields) != {"is_enabled"}

        with _locked_exact_schedule(
            schedule_id, user_id, destination=kwargs.get("target_playlist_id"),
            execution=definition_edit,
        ) as schedule:
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
        Delete a schedule and legacy history, retaining durable receipts and snapshots.

        Raises:
            ScheduleNotFoundError: If not found.
            ScheduleError: If deletion fails.
        """
        with _locked_exact_schedule(schedule_id, user_id, execution=True) as schedule:
            try:
                # Saved playlists outlive the execution history. Clear only links
                # to this schedule's executions before deleting those rows.
                execution_ids = db.select(JobExecution.id).where(
                    JobExecution.schedule_id == schedule_id, JobExecution.kind.is_(None),
                )
                PlaylistSnapshot.query.filter(
                    PlaylistSnapshot.job_execution_id.in_(execution_ids)
                ).update({PlaylistSnapshot.job_execution_id: None}, synchronize_session="fetch")
                JobExecution.query.filter_by(schedule_id=schedule_id).filter(
                    JobExecution.kind.is_not(None),
                ).update({JobExecution.schedule_id: None}, synchronize_session="fetch")
                JobExecution.query.filter_by(schedule_id=schedule_id).filter(
                    JobExecution.kind.is_(None),
                ).delete(synchronize_session="fetch")
                db.session.delete(schedule)
                # Bulk UPDATE/DELETE execute immediately, so the transaction guard
                # must include them as well as the final commit.
                db.session.commit()
            except Exception as error:
                db.session.rollback()
                logger.error(
                    "Failed to delete schedule %s: %s",
                    schedule_id, error, exc_info=True,
                )
                raise ScheduleError(
                    f"Failed to delete schedule {schedule_id}: {error}"
                ) from error

            logger.info("Success: delete schedule %s", schedule_id)
            # Remove the worker registration only after the DB delete commits, so a
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
        with _locked_exact_schedule(schedule_id, user_id) as schedule:
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
    def get_latest_executions(user_id: int, playlist_id: Optional[str] = None) -> Dict[int, Optional[Dict[str, Any]]]:
        """Return one latest exact execution, or None, for every owned routine.

        Ranking occurs in SQL, so a management page does not materialize each
        routine's full history or issue a history query for every card.
        """
        owned = db.select(Schedule.id).where(Schedule.user_id == user_id)
        if playlist_id is not None:
            owned = owned.where(Schedule.target_playlist_id == playlist_id)
        schedule_ids = list(db.session.scalars(owned))
        latest = {schedule_id: None for schedule_id in schedule_ids}
        if not schedule_ids:
            return latest
        ranked = (
            db.select(
                JobExecution.id.label("execution_id"),
                JobExecution.schedule_id.label("schedule_id"),
                func.row_number().over(
                    partition_by=JobExecution.schedule_id,
                    order_by=(JobExecution.started_at.desc(), JobExecution.id.desc()),
                ).label("position"),
            )
            .where(JobExecution.schedule_id.in_(schedule_ids))
            .subquery()
        )
        rows = db.session.scalars(
            db.select(JobExecution)
            .join(ranked, ranked.c.execution_id == JobExecution.id)
            .where(ranked.c.position == 1)
        )
        for row in rows:
            latest[row.schedule_id] = row.to_dict()
        return latest

    @staticmethod
    def _sync_apscheduler_job(schedule) -> None:
        """Request a worker refresh after the committed state change.

        This hint never claims registration succeeded. Transactional database
        notifications and reconnect refresh cover an unavailable private listener.
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
        except Exception as error:
            logger.warning(
                "Worker scheduler refresh unavailable for schedule %s (%s)",
                getattr(schedule, "id", "?"),
                type(error).__name__,
            )

    @staticmethod
    def _remove_apscheduler_job(schedule_id: int) -> None:
        """Request a worker refresh after deletion (best-effort)."""
        try:
            from shuffify.scheduler import remove_job_for_schedule

            remove_job_for_schedule(schedule_id)
        except Exception as error:
            logger.warning(
                "Worker scheduler removal hint unavailable for schedule %s (%s)",
                schedule_id,
                type(error).__name__,
            )
