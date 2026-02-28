"""
Base job executor: lifecycle, token management, dispatch,
and shared utilities.

Contains the JobExecutorService class which is the single public
entry point for all job execution. Operation-specific logic is
delegated to sibling modules (raid_executor, shuffle_executor,
rotate_executor).
"""

import logging
from datetime import datetime, timezone
from typing import List

from shuffify.models.db import db, Schedule, JobExecution, User
from shuffify.services.base import safe_commit
from shuffify.services.token_service import (
    TokenService,
    TokenEncryptionError,
)
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import SpotifyTokenError
from shuffify.enums import JobType, ActivityType

logger = logging.getLogger(__name__)


class JobExecutionError(Exception):
    """Raised when a scheduled job fails to execute."""

    pass


class JobExecutorService:
    """Service that executes scheduled playlist operations."""

    @staticmethod
    def execute(schedule_id: int) -> None:
        """
        Execute a scheduled job.

        This is the main entry point called by the scheduler.
        It handles all error scenarios and records the execution.
        """
        execution = None
        schedule = None

        try:
            schedule = db.session.get(Schedule, schedule_id)
            if not schedule:
                logger.error(
                    f"Schedule {schedule_id} not found, "
                    f"skipping"
                )
                return

            if not schedule.is_enabled:
                logger.info(
                    f"Schedule {schedule_id} is disabled, "
                    f"skipping"
                )
                return

            execution = (
                JobExecutorService._create_execution_record(
                    schedule_id
                )
            )

            user = db.session.get(User, schedule.user_id)
            if not user:
                raise JobExecutionError(
                    f"User {schedule.user_id} not found"
                )

            api = JobExecutorService._get_spotify_api(user)

            result = JobExecutorService._execute_job_type(
                schedule, api
            )

            JobExecutorService._record_success(
                execution, schedule, result
            )

        except Exception as e:
            JobExecutorService._record_failure(
                execution, schedule, e, schedule_id
            )

    @staticmethod
    def _create_execution_record(
        schedule_id: int,
    ) -> JobExecution:
        """Create a running execution record in the database."""
        execution = JobExecution(
            schedule_id=schedule_id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        safe_commit(
            f"create execution record for schedule {schedule_id}",
            JobExecutionError,
        )
        return execution

    @staticmethod
    def _record_success(
        execution: JobExecution,
        schedule: Schedule,
        result: dict,
    ) -> None:
        """Record a successful job execution."""
        execution.status = "success"
        execution.completed_at = datetime.now(timezone.utc)
        execution.tracks_added = result.get(
            "tracks_added", 0
        )
        execution.tracks_total = result.get(
            "tracks_total", 0
        )

        schedule.last_run_at = datetime.now(timezone.utc)
        schedule.last_status = "success"
        schedule.last_error = None

        db.session.commit()

        # Log activity (non-blocking)
        try:
            from shuffify.services.activity_log_service import (  # noqa: E501
                ActivityLogService,
            )

            ActivityLogService.log(
                user_id=schedule.user_id,
                activity_type=(
                    ActivityType.SCHEDULE_RUN
                ),
                description=(
                    f"Scheduled "
                    f"{schedule.job_type} on "
                    f"'{schedule.target_playlist_name}'"
                    f" completed"
                ),
                playlist_id=(
                    schedule.target_playlist_id
                ),
                playlist_name=(
                    schedule.target_playlist_name
                ),
                metadata={
                    "schedule_id": schedule.id,
                    "job_type": schedule.job_type,
                    "tracks_added": result.get(
                        "tracks_added", 0
                    ),
                    "tracks_total": result.get(
                        "tracks_total", 0
                    ),
                    "triggered_by": "scheduler",
                },
            )
        except Exception:
            pass

        logger.info(
            f"Schedule {schedule.id} executed "
            f"successfully: "
            f"added={result.get('tracks_added', 0)}, "
            f"total={result.get('tracks_total', 0)}"
        )

    @staticmethod
    def _record_failure(
        execution,
        schedule,
        error: Exception,
        schedule_id: int,
    ) -> None:
        """Record a failed job execution."""
        logger.error(
            f"Schedule {schedule_id} execution "
            f"failed: {error}",
            exc_info=True,
        )
        try:
            if execution:
                execution.status = "failed"
                execution.completed_at = datetime.now(
                    timezone.utc
                )
                execution.error_message = str(error)[:1000]

            if schedule:
                schedule.last_run_at = datetime.now(
                    timezone.utc
                )
                schedule.last_status = "failed"
                schedule.last_error = str(error)[:1000]

            db.session.commit()
        except Exception as db_err:
            logger.error(
                f"Failed to record execution failure: "
                f"{db_err}"
            )
            db.session.rollback()

    @staticmethod
    def execute_now(
        schedule_id: int, user_id: int
    ) -> dict:
        """
        Manually trigger a schedule execution (from the UI).

        Raises:
            JobExecutionError: If execution fails.
        """
        from shuffify.services.scheduler_service import (
            SchedulerService,
            ScheduleNotFoundError,
        )

        try:
            schedule = SchedulerService.get_schedule(
                schedule_id, user_id
            )
        except ScheduleNotFoundError:
            raise JobExecutionError(
                f"Schedule {schedule_id} not found"
            )

        # Execute synchronously
        JobExecutorService.execute(schedule_id)

        # Reload to get updated status
        db.session.refresh(schedule)

        if schedule.last_status == "failed":
            raise JobExecutionError(
                f"Execution failed: {schedule.last_error}"
            )

        return {
            "status": schedule.last_status,
            "last_run_at": (
                schedule.last_run_at.isoformat()
                if schedule.last_run_at
                else None
            ),
        }

    @staticmethod
    def _get_spotify_api(user: User) -> SpotifyAPI:
        """
        Create a SpotifyAPI client using the user's stored
        refresh token.

        Raises:
            JobExecutionError: If token decryption or refresh
                fails.
        """
        if not user.encrypted_refresh_token:
            raise JobExecutionError(
                f"User {user.spotify_id} has no stored "
                f"refresh token. User must log in to enable "
                f"scheduled operations."
            )

        try:
            refresh_token = TokenService.decrypt_token(
                user.encrypted_refresh_token
            )
        except TokenEncryptionError as e:
            raise JobExecutionError(
                f"Failed to decrypt refresh token for user "
                f"{user.spotify_id}: {e}"
            )

        try:
            from flask import current_app

            credentials = SpotifyCredentials.from_flask_config(
                current_app.config
            )
            auth_manager = SpotifyAuthManager(credentials)

            # Create a token_info with an expired access token
            # and valid refresh token. SpotifyAPI will
            # auto-refresh on first call.
            token_info = TokenInfo(
                access_token="expired_placeholder",
                token_type="Bearer",
                expires_at=0,
                refresh_token=refresh_token,
            )

            api = SpotifyAPI(
                token_info,
                auth_manager,
                auto_refresh=True,
            )

            # Update stored refresh token if it was rotated
            new_token = api.token_info
            if (
                new_token.refresh_token
                and new_token.refresh_token != refresh_token
            ):
                user.encrypted_refresh_token = (
                    TokenService.encrypt_token(
                        new_token.refresh_token
                    )
                )
                db.session.commit()
                logger.info(
                    f"Updated rotated refresh token for "
                    f"user {user.spotify_id}"
                )

            return api

        except SpotifyTokenError as e:
            raise JobExecutionError(
                f"Failed to refresh Spotify token for user "
                f"{user.spotify_id}: {e}"
            )

    @staticmethod
    def _execute_job_type(
        schedule: Schedule, api: SpotifyAPI
    ) -> dict:
        """Execute the appropriate operation based on job type."""
        from shuffify.services.executors.raid_executor import (
            execute_raid,
        )
        from shuffify.services.executors.shuffle_executor import (  # noqa: E501
            execute_shuffle,
        )
        from shuffify.services.executors.rotate_executor import (  # noqa: E501
            execute_rotate,
        )

        if schedule.job_type == JobType.RAID:
            return execute_raid(schedule, api)
        elif schedule.job_type == JobType.SHUFFLE:
            return execute_shuffle(schedule, api)
        elif schedule.job_type == JobType.RAID_AND_SHUFFLE:
            result = execute_raid(schedule, api)
            shuffle_result = execute_shuffle(schedule, api)
            result["tracks_total"] = shuffle_result[
                "tracks_total"
            ]
            return result
        elif schedule.job_type == JobType.ROTATE:
            return execute_rotate(schedule, api)
        else:
            raise JobExecutionError(
                f"Unknown job type: {schedule.job_type}"
            )

    @staticmethod
    def _batch_add_tracks(
        api: SpotifyAPI,
        playlist_id: str,
        uris: List[str],
        batch_size: int = 100,
    ) -> None:
        """Add tracks to a playlist in batches."""
        api.playlist_add_items(playlist_id, uris)
