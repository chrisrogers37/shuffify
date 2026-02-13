"""
Job executor service for running scheduled playlist operations.

Handles the actual execution of raid, shuffle, and combined jobs.
Uses encrypted refresh tokens to obtain Spotify API access without
user interaction.
"""

import logging
from datetime import datetime, timezone
from typing import List

from shuffify.models.db import db, Schedule, JobExecution, User
from shuffify.services.token_service import (
    TokenService,
    TokenEncryptionError,
)
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import (
    SpotifyTokenError,
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.enums import JobType, SnapshotType, ActivityType
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)

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

            # Create execution record
            execution = JobExecution(
                schedule_id=schedule_id,
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            db.session.add(execution)
            db.session.commit()

            # Load user and get API client
            user = db.session.get(User, schedule.user_id)
            if not user:
                raise JobExecutionError(
                    f"User {schedule.user_id} not found"
                )

            api = JobExecutorService._get_spotify_api(user)

            # Execute based on job type
            result = JobExecutorService._execute_job_type(
                schedule, api
            )

            # Record success
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
                        "schedule_id": schedule_id,
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
                f"Schedule {schedule_id} executed "
                f"successfully: "
                f"added={result.get('tracks_added', 0)}, "
                f"total={result.get('tracks_total', 0)}"
            )

        except Exception as e:
            logger.error(
                f"Schedule {schedule_id} execution "
                f"failed: {e}",
                exc_info=True,
            )
            try:
                if execution:
                    execution.status = "failed"
                    execution.completed_at = datetime.now(
                        timezone.utc
                    )
                    execution.error_message = str(e)[:1000]

                if schedule:
                    schedule.last_run_at = datetime.now(
                        timezone.utc
                    )
                    schedule.last_status = "failed"
                    schedule.last_error = str(e)[:1000]

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
        if schedule.job_type == JobType.RAID:
            return JobExecutorService._execute_raid(
                schedule, api
            )
        elif schedule.job_type == JobType.SHUFFLE:
            return JobExecutorService._execute_shuffle(
                schedule, api
            )
        elif schedule.job_type == JobType.RAID_AND_SHUFFLE:
            result = JobExecutorService._execute_raid(
                schedule, api
            )
            shuffle_result = (
                JobExecutorService._execute_shuffle(
                    schedule, api
                )
            )
            result["tracks_total"] = shuffle_result[
                "tracks_total"
            ]
            return result
        else:
            raise JobExecutionError(
                f"Unknown job type: {schedule.job_type}"
            )

    @staticmethod
    def _execute_raid(
        schedule: Schedule, api: SpotifyAPI
    ) -> dict:
        """
        Pull new tracks from source playlists into the target.
        """
        target_id = schedule.target_playlist_id
        source_ids = schedule.source_playlist_ids or []

        if not source_ids:
            logger.info(
                f"Schedule {schedule.id}: no source playlists "
                f"configured, skipping raid"
            )
            target_tracks = api.get_playlist_tracks(target_id)
            return {
                "tracks_added": 0,
                "tracks_total": len(target_tracks),
            }

        try:
            target_tracks = api.get_playlist_tracks(target_id)
            target_uris = {
                t.get("uri")
                for t in target_tracks
                if t.get("uri")
            }

            # --- Auto-snapshot before scheduled raid ---
            try:
                pre_raid_uris = [
                    t.get("uri")
                    for t in target_tracks
                    if t.get("uri")
                ]
                if (
                    pre_raid_uris
                    and PlaylistSnapshotService
                    .is_auto_snapshot_enabled(
                        schedule.user_id
                    )
                ):
                    PlaylistSnapshotService.create_snapshot(
                        user_id=schedule.user_id,
                        playlist_id=target_id,
                        playlist_name=(
                            schedule.target_playlist_name
                            or target_id
                        ),
                        track_uris=pre_raid_uris,
                        snapshot_type=(
                            SnapshotType.AUTO_PRE_RAID
                        ),
                        trigger_description=(
                            "Before scheduled raid"
                        ),
                    )
            except Exception as snap_err:
                logger.warning(
                    "Auto-snapshot before scheduled "
                    f"raid failed: {snap_err}"
                )
            # --- End auto-snapshot ---

            new_uris: List[str] = []
            for source_id in source_ids:
                try:
                    source_tracks = api.get_playlist_tracks(
                        source_id
                    )
                    for track in source_tracks:
                        uri = track.get("uri")
                        if (
                            uri
                            and uri not in target_uris
                            and uri not in new_uris
                        ):
                            new_uris.append(uri)
                except SpotifyNotFoundError:
                    logger.warning(
                        f"Source playlist {source_id} "
                        f"not found, skipping"
                    )
                    continue

            if not new_uris:
                logger.info(
                    f"Schedule {schedule.id}: "
                    f"no new tracks to add"
                )
                return {
                    "tracks_added": 0,
                    "tracks_total": len(target_tracks),
                }

            # Add new tracks in batches
            batch_size = 100
            for i in range(0, len(new_uris), batch_size):
                batch = new_uris[i: i + batch_size]
                api._ensure_valid_token()
                api._sp.playlist_add_items(target_id, batch)

            total = len(target_tracks) + len(new_uris)
            logger.info(
                f"Schedule {schedule.id}: added "
                f"{len(new_uris)} tracks to "
                f"{schedule.target_playlist_name} "
                f"(total: {total})"
            )

            return {
                "tracks_added": len(new_uris),
                "tracks_total": total,
            }

        except SpotifyNotFoundError:
            raise JobExecutionError(
                f"Target playlist {target_id} not found. "
                f"It may have been deleted."
            )
        except SpotifyAPIError as e:
            raise JobExecutionError(
                f"Spotify API error during raid: {e}"
            )

    @staticmethod
    def _execute_shuffle(
        schedule: Schedule, api: SpotifyAPI
    ) -> dict:
        """Run a shuffle algorithm on the target playlist."""
        target_id = schedule.target_playlist_id
        algorithm_name = schedule.algorithm_name

        if not algorithm_name:
            raise JobExecutionError(
                f"Schedule {schedule.id}: "
                f"no algorithm configured for shuffle"
            )

        try:
            raw_tracks = api.get_playlist_tracks(target_id)
            if not raw_tracks:
                return {"tracks_added": 0, "tracks_total": 0}

            # --- Auto-snapshot before scheduled shuffle ---
            try:
                pre_shuffle_uris = [
                    t["uri"]
                    for t in raw_tracks
                    if t.get("uri")
                ]
                if (
                    pre_shuffle_uris
                    and PlaylistSnapshotService
                    .is_auto_snapshot_enabled(
                        schedule.user_id
                    )
                ):
                    PlaylistSnapshotService.create_snapshot(
                        user_id=schedule.user_id,
                        playlist_id=target_id,
                        playlist_name=(
                            schedule.target_playlist_name
                            or target_id
                        ),
                        track_uris=pre_shuffle_uris,
                        snapshot_type=(
                            SnapshotType
                            .SCHEDULED_PRE_EXECUTION
                        ),
                        trigger_description=(
                            "Before scheduled "
                            f"{algorithm_name}"
                        ),
                    )
            except Exception as snap_err:
                logger.warning(
                    "Auto-snapshot before scheduled "
                    f"shuffle failed: {snap_err}"
                )
            # --- End auto-snapshot ---

            tracks = []
            for t in raw_tracks:
                if t.get("uri"):
                    tracks.append(
                        {
                            "id": t.get("id", ""),
                            "name": t.get("name", ""),
                            "uri": t["uri"],
                            "artists": [
                                a.get("name", "")
                                for a in t.get("artists", [])
                            ],
                            "album": t.get("album", {}),
                        }
                    )

            if not tracks:
                return {"tracks_added": 0, "tracks_total": 0}

            algorithm_class = ShuffleRegistry.get_algorithm(
                algorithm_name
            )
            algorithm = algorithm_class()
            params = schedule.algorithm_params or {}
            shuffled_uris = algorithm.shuffle(
                tracks, **params
            )

            api.update_playlist_tracks(
                target_id, shuffled_uris
            )

            logger.info(
                f"Schedule {schedule.id}: shuffled "
                f"{schedule.target_playlist_name} "
                f"with {algorithm_name}"
            )

            return {
                "tracks_added": 0,
                "tracks_total": len(shuffled_uris),
            }

        except SpotifyNotFoundError:
            raise JobExecutionError(
                f"Target playlist {target_id} not found"
            )
        except ValueError as e:
            raise JobExecutionError(
                f"Invalid algorithm '{algorithm_name}': {e}"
            )
        except SpotifyAPIError as e:
            raise JobExecutionError(
                f"Spotify API error during shuffle: {e}"
            )
