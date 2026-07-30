"""
Base job executor: lifecycle, token management, dispatch,
and shared utilities.

Contains the JobExecutorService class which is the single public
entry point for all job execution. Operation-specific logic is
delegated to sibling modules (raid_executor, shuffle_executor,
rotate_executor).
"""

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import List

from shuffify.models.db import db, Schedule, JobExecution, User
from shuffify.services.base import safe_commit
from shuffify.services.playlist_lock import playlist_lock
from shuffify.services.token_service import (
    TokenService,
    TokenEncryptionError,
)
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import (
    SpotifyPartialBatchError,
    SpotifyTokenError,
)
from shuffify.shuffle_algorithms.utils import extract_uris
from shuffify.enums import JobType, ActivityType

# Sentinel token used when constructing a SpotifyAPI with only a
# refresh token. The access_token is intentionally invalid so the
# client triggers an auto-refresh on the first API call.
_EXPIRED_ACCESS_TOKEN = "expired_placeholder"

logger = logging.getLogger(__name__)


def _tag_sentry_scope(schedule, schedule_id):
    """Attach schedule context to the current Sentry scope.

    Background jobs run outside the Flask request context, so the
    FlaskIntegration can't auto-tag them. This makes Sentry's UI
    filterable by schedule, playlist, job type, and user.

    Silent no-op when sentry-sdk is missing or no DSN is configured
    (sentry_sdk.Hub.current is the no-op hub in that case).
    """
    try:
        import sentry_sdk
    except ImportError:
        return
    try:
        scope = sentry_sdk.get_current_scope()
        scope.set_tag("schedule_id", schedule_id)
        if schedule is not None:
            scope.set_tag("job_type", str(schedule.job_type))
            scope.set_tag("playlist_id", schedule.target_playlist_id)
            scope.set_user({"id": str(schedule.user_id)})
    except Exception:
        # Never let observability tagging break job execution.
        pass


class JobExecutionError(Exception):
    """Raised when a scheduled job fails to execute."""

    pass


class PlaylistVerificationError(JobExecutionError):
    """Raised when post-write playlist state diverges from expected.

    Compares actual URIs to expected URIs as multisets so duplicate
    track counts must also match. Downstream consumers (rollback,
    structured logging) read `missing`, `extra`, `playlist_id`,
    `phase`, and `schedule_id` directly off the instance.
    """

    def __init__(
        self,
        playlist_id: str,
        expected: List[str],
        actual: List[str],
        schedule_id: int,
        phase: str,
    ):
        self.playlist_id = playlist_id
        self.expected = expected
        self.actual = actual
        self.schedule_id = schedule_id
        self.phase = phase

        exp_counter = Counter(expected)
        act_counter = Counter(actual)
        self.missing = list((exp_counter - act_counter).elements())
        self.extra = list((act_counter - exp_counter).elements())

        super().__init__(
            f"Schedule {schedule_id}: {phase} verification failed "
            f"— expected {len(expected)} tracks, got "
            f"{len(actual)}, missing {len(self.missing)}, "
            f"extra {len(self.extra)}"
        )


def verify_playlist_state(
    api: SpotifyAPI,
    playlist_id: str,
    expected_uris: List[str],
    schedule_id: int,
    phase: str,
    ordered: bool = False,
) -> List[str]:
    """Re-fetch playlist and verify it matches expected.

    Belt-and-suspenders: write methods on SpotifyAPI already invalidate
    the playlist cache, but skip_cache=True here ensures correctness
    even if a future write path forgets to invalidate.

    Args:
        api: SpotifyAPI client.
        playlist_id: Target playlist.
        expected_uris: URIs the playlist should contain.
        schedule_id: For error attribution.
        phase: Short label like "swap", "shuffle", "drip target".
        ordered: When True, require the exact URI sequence (order-sensitive).
            When False (default), compare as a multiset (order ignored,
            duplicate counts honored). Order-sensitive checks catch a shuffle
            that didn't reorder or a drip that appended instead of prepended —
            both preserve the multiset but not the sequence (SR-007).

    Returns:
        The actual URI list (in fetch order) on success.

    Raises:
        PlaylistVerificationError: If the actual state diverges from expected
            (by sequence when ordered, otherwise by multiset).
    """
    verified = api.get_playlist_tracks(
        playlist_id,
        skip_cache=True,
    )
    actual_uris = extract_uris(verified or [])

    if ordered:
        diverged = actual_uris != expected_uris
    else:
        diverged = Counter(actual_uris) != Counter(expected_uris)

    if diverged:
        raise PlaylistVerificationError(
            playlist_id=playlist_id,
            expected=expected_uris,
            actual=actual_uris,
            schedule_id=schedule_id,
            phase=phase,
        )
    return actual_uris


def _rollback_trigger_phrase(error) -> str:
    """Short human description for the rollback activity log."""
    if isinstance(error, PlaylistVerificationError):
        return "verification failure"
    if isinstance(error, SpotifyPartialBatchError):
        return f"partial {error.method} write failure"
    return "execution failure"


def _rollback_metadata(error, schedule, restored) -> dict:
    """Build the ActivityLog metadata payload for a rollback.

    Shape depends on the error type so the audit trail keeps
    the right fields for each failure mode.
    """
    base = {
        "schedule_id": schedule.id,
        "job_type": schedule.job_type,
        "restored": restored,
        "triggered_by": "scheduler",
    }
    if isinstance(error, PlaylistVerificationError):
        base.update(
            {
                "failure_type": "verification",
                "phase": error.phase,
                "failing_playlist_id": error.playlist_id,
                "expected_count": len(error.expected),
                "actual_count": len(error.actual),
                "missing_total": len(error.missing),
                "extra_total": len(error.extra),
                "missing_uris": error.missing[:50],
                "extra_uris": error.extra[:50],
            }
        )
    elif isinstance(error, SpotifyPartialBatchError):
        base.update(
            {
                "failure_type": "partial_batch",
                "method": error.method,
                "failing_playlist_id": error.playlist_id,
                "completed_batches": error.completed_batches,
                "total_batches": error.total_batches,
                "completed_count": len(error.completed_uris),
                "remaining_count": len(error.remaining_uris),
                "remaining_uris": error.remaining_uris[:50],
                "cause": (str(error.cause) if error.cause else None),
            }
        )
    return base


def _restore_job_snapshots(execution, schedule, api, schedule_id):
    """Restore auto-snapshots taken during this job.

    Returns a list of restoration dicts on success, or None if
    restoration fails (caller should fall back to plain failure).
    """
    from shuffify.services.playlist_snapshot_service import (  # noqa: E501
        PlaylistSnapshotService,
        PlaylistSnapshotError,
    )
    from shuffify.models.db import PlaylistSnapshot

    try:
        user_id = schedule.user_id if schedule else None
        since = execution.started_at if execution else None
        execution_id = execution.id if execution else None
        if not user_id or not since:
            raise PlaylistSnapshotError(
                "Missing user_id or job start time; cannot scope snapshots."
            )

        # Prefer snapshots tagged with this job's execution id, so a manual
        # snapshot or a concurrent schedule taken during a long job can't be
        # swept into this rollback (SR-019).
        pre_snapshots = []
        if execution_id is not None:
            pre_snapshots = (
                PlaylistSnapshot.query.filter(
                    PlaylistSnapshot.user_id == user_id,
                    PlaylistSnapshot.job_execution_id == execution_id,
                )
                .order_by(PlaylistSnapshot.created_at.asc())
                .all()
            )

        if not pre_snapshots:
            # Fallback for untagged snapshots (pre-migration rows, or an
            # execution with no tagged snapshots): scope by the job's time
            # window as before, so rollback never silently restores nothing.
            pre_snapshots = (
                PlaylistSnapshot.query.filter(
                    PlaylistSnapshot.user_id == user_id,
                    PlaylistSnapshot.created_at >= since,
                )
                .order_by(PlaylistSnapshot.created_at.asc())
                .all()
            )

        if not pre_snapshots:
            raise PlaylistSnapshotError(
                "No pre-snapshot available for rollback "
                "(auto-snapshots disabled or empty "
                "source playlists)."
            )

        # One snapshot per playlist — keep the most recent.
        latest_by_playlist = {}
        for snap in pre_snapshots:
            latest_by_playlist[snap.playlist_id] = snap

        restored = []
        for playlist_id, snap in latest_by_playlist.items():
            applied = PlaylistSnapshotService.restore_to_playlist(
                snap.id,
                user_id,
                api,
            )
            restored.append(
                {
                    "playlist_id": playlist_id,
                    "snapshot_id": snap.id,
                    "track_count": applied.track_count,
                }
            )
        return restored

    except Exception as restore_err:
        logger.error(
            "Schedule %s: rollback failed: %s. Falling back to plain failure.",
            schedule_id,
            restore_err,
            exc_info=True,
        )
        return None


def _revert_job_raid_staging(execution, schedule):
    """Delete raid tracks staged during this job that are still pending.

    A composite job (RAID_AND_SHUFFLE / RAID_AND_DRIP) whose later step fails
    would otherwise leave the raid step's pending tracks staged even after the
    playlists are rolled back — a non-atomic side effect (SR-008). Scoped to
    the schedule's target and the job window (``created_at >= started_at``).

    Returns the number of rows deleted. Best-effort — a failure here must not
    mask the rollback already under way.
    """
    from shuffify.models.db import PendingRaidTrack
    from shuffify.enums import PendingRaidStatus

    try:
        user_id = schedule.user_id if schedule else None
        target_id = schedule.target_playlist_id if schedule else None
        since = execution.started_at if execution else None
        if not user_id or not target_id or not since:
            return 0

        deleted = PendingRaidTrack.query.filter(
            PendingRaidTrack.user_id == user_id,
            PendingRaidTrack.target_playlist_id == target_id,
            PendingRaidTrack.created_at >= since,
            PendingRaidTrack.status == PendingRaidStatus.PENDING,
        ).delete(synchronize_session=False)
        db.session.commit()

        if deleted:
            logger.info(
                "Schedule %s: reverted %d raid tracks staged during a "
                "rolled-back job (SR-008)",
                getattr(schedule, "id", "?"),
                deleted,
            )
        return deleted
    except Exception as e:
        logger.warning(
            "Failed to revert raid staging on rollback: %s", e
        )
        db.session.rollback()
        return 0


def _persist_rollback_status(execution, schedule, ve, schedule_id):
    """Write failed_rolled_back status to db."""
    try:
        if execution:
            execution.status = "failed_rolled_back"
            execution.completed_at = datetime.now(timezone.utc)
            execution.error_message = str(ve)[:1000]

        if schedule:
            schedule.last_run_at = datetime.now(timezone.utc)
            schedule.last_status = "failed_rolled_back"
            schedule.last_error = str(ve)[:1000]

        db.session.commit()
    except Exception as db_err:
        logger.error(
            "Schedule %s: failed to persist rollback status: %s",
            schedule_id,
            db_err,
        )
        db.session.rollback()


class JobExecutorService:
    """Service that executes scheduled playlist operations."""

    @staticmethod
    def execute(schedule_id: int) -> None:
        """
        Execute a scheduled job.

        This is the main entry point called by the scheduler.
        It handles all error scenarios and records the execution.

        Runs are serialized per target playlist via
        :func:`playlist_lock` so two schedules sharing a cron firing
        time on the same playlist can't interleave and corrupt each
        other's verification (e.g. a shuffle + a rotate that both
        fire at ``0 9 * * *`` on the same target).
        """
        schedule = db.session.get(Schedule, schedule_id)
        if not schedule:
            logger.error(f"Schedule {schedule_id} not found, skipping")
            return

        if not schedule.is_enabled:
            logger.info(f"Schedule {schedule_id} is disabled, skipping")
            return

        _tag_sentry_scope(schedule, schedule_id)
        JobExecutorService._run_job(schedule, schedule_id)

    @staticmethod
    def _run_job(schedule, schedule_id) -> dict:
        """Run one job through the full safety rails and record it.

        Shared by scheduled runs (:meth:`execute`) and inline raids
        (:meth:`execute_raid_for_user`). ``schedule`` may be a persisted
        Schedule or a *transient* (unsaved) one for a schedule-less raid, in
        which case ``schedule_id`` is ``None``: a transient object is never in
        the session, so the ``_record_*`` helpers set attributes on a throwaway
        and ``commit()`` only persists the JobExecution (recorded with a null
        ``schedule_id``).

        Provides the per-playlist :func:`playlist_lock`, a JobExecution record,
        snapshot-based rollback on verification/partial-write failure, and
        activity logging. Records the outcome and returns a result dict; never
        raises (mirrors the fire-and-forget scheduler contract).
        """
        execution = None
        api = None
        snapshot_token = None

        try:
            with playlist_lock(schedule.target_playlist_id) as acquired:
                if not acquired:
                    logger.warning(
                        "Schedule %s: skipping run — another job is "
                        "in progress on playlist %s and the lock did "
                        "not release within the timeout. Next "
                        "scheduled fire will retry.",
                        schedule_id,
                        schedule.target_playlist_id,
                    )
                    # Record the skip so contention is visible in execution
                    # history instead of only a WARNING log (SR-035).
                    JobExecutorService._record_lock_timeout(
                        schedule, schedule_id
                    )
                    return {
                        "status": "skipped",
                        "tracks_added": 0,
                        "tracks_total": 0,
                    }

                execution = JobExecutorService._create_execution_record(
                    schedule_id
                )
                # Tag snapshots created during this job so rollback restores
                # only this execution's snapshots (SR-019).
                from shuffify.services.playlist_snapshot_service import (
                    set_current_job_execution,
                )

                snapshot_token = set_current_job_execution(execution.id)

                user = db.session.get(User, schedule.user_id)
                if not user:
                    raise JobExecutionError(f"User {schedule.user_id} not found")

                api = JobExecutorService._get_spotify_api(user)

                result = JobExecutorService._execute_job_type(schedule, api)

                JobExecutorService._record_success(execution, schedule, result)
                return {
                    "status": "success",
                    "tracks_added": result.get("tracks_added", 0),
                    "tracks_total": result.get("tracks_total", 0),
                }

        except (
            PlaylistVerificationError,
            SpotifyPartialBatchError,
        ) as ve:
            JobExecutorService._record_rollback(
                execution,
                schedule,
                api,
                ve,
                schedule_id,
            )
            return {
                "status": "failed_rolled_back",
                "tracks_added": 0,
                "tracks_total": 0,
                "error": str(ve),
            }
        except Exception as e:
            JobExecutorService._record_failure(execution, schedule, e, schedule_id)
            return {
                "status": "failed",
                "tracks_added": 0,
                "tracks_total": 0,
                "error": str(e),
            }
        finally:
            if snapshot_token is not None:
                from shuffify.services.playlist_snapshot_service import (
                    reset_current_job_execution,
                )

                reset_current_job_execution(snapshot_token)

    @staticmethod
    def execute_raid_for_user(
        user_id: int,
        target_playlist_id: str,
        source_playlist_ids=None,
        target_playlist_name: str = None,
    ) -> dict:
        """Run an inline (schedule-less) raid through the full executor safety
        rails, so a manual "Raid Now" behaves identically to a scheduled raid:
        per-playlist lock, JobExecution record, auto-snapshots, rollback, and
        activity logging (SR-010).

        Uses a *transient* Schedule (never added to the session), so no
        schedule row is created and the JobExecution is recorded with a null
        ``schedule_id``.

        Returns a result dict with ``status`` / ``tracks_added`` /
        ``tracks_total`` (and ``error`` on failure).
        """
        schedule = Schedule(
            user_id=user_id,
            job_type=JobType.RAID,
            target_playlist_id=target_playlist_id,
            target_playlist_name=(target_playlist_name or target_playlist_id),
            source_playlist_ids=source_playlist_ids or [],
            is_enabled=True,
        )
        return JobExecutorService._run_job(schedule, None)

    @staticmethod
    def execute_drip_for_user(
        user_id: int,
        target_playlist_id: str,
        target_playlist_name: str = None,
        drip_count: int = None,
    ) -> dict:
        """Run an inline (schedule-less) drip through the full executor safety
        rails, so a manual "Drip Now" behaves like a scheduled drip instead of
        a hand-rolled ``Schedule(id=0)`` that bypassed the lock / execution
        record / rollback (#332, the SR-010 twin).

        Uses a *transient* Schedule (never persisted); the JobExecution is
        recorded with a null ``schedule_id``. The caller must ensure drip is
        enabled on the raid link.
        """
        schedule = Schedule(
            user_id=user_id,
            job_type=JobType.DRIP,
            target_playlist_id=target_playlist_id,
            target_playlist_name=(target_playlist_name or target_playlist_id),
            algorithm_params=(
                {"drip_count": drip_count} if drip_count else {}
            ),
            is_enabled=True,
        )
        return JobExecutorService._run_job(schedule, None)

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
    def _record_lock_timeout(schedule, schedule_id) -> None:
        """Record a JobExecution for a run skipped because the per-playlist
        lock did not release within the timeout (SR-035).

        Best-effort — a failure here must not turn a benign skip into a crash.
        Works for a persisted or a transient schedule (schedule_id may be
        None); a transient schedule is never in the session, so its
        ``last_status`` change is discarded.
        """
        try:
            now = datetime.now(timezone.utc)
            execution = JobExecution(
                schedule_id=schedule_id,
                started_at=now,
                completed_at=now,
                status="skipped",
                error_message=(
                    "Skipped: another job held the playlist lock past the "
                    "timeout; the next scheduled fire will retry."
                ),
            )
            db.session.add(execution)
            if schedule is not None:
                schedule.last_run_at = now
                schedule.last_status = "skipped"
            db.session.commit()
        except Exception as e:
            logger.warning(
                "Failed to record lock-timeout execution for schedule "
                "%s: %s",
                schedule_id,
                e,
            )
            db.session.rollback()

    @staticmethod
    def _record_success(
        execution: JobExecution,
        schedule: Schedule,
        result: dict,
    ) -> None:
        """Record a successful job execution."""
        execution.status = "success"
        execution.completed_at = datetime.now(timezone.utc)
        execution.tracks_added = result.get("tracks_added", 0)
        execution.tracks_total = result.get("tracks_total", 0)

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
                activity_type=(ActivityType.SCHEDULE_RUN),
                description=(
                    f"Scheduled "
                    f"{schedule.job_type} on "
                    f"'{schedule.target_playlist_name}'"
                    f" completed"
                ),
                playlist_id=(schedule.target_playlist_id),
                playlist_name=(schedule.target_playlist_name),
                metadata={
                    "schedule_id": schedule.id,
                    "job_type": schedule.job_type,
                    "tracks_added": result.get("tracks_added", 0),
                    "tracks_total": result.get("tracks_total", 0),
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
            f"Schedule {schedule_id} execution failed: {error}",
            exc_info=True,
        )
        try:
            if execution:
                execution.status = "failed"
                execution.completed_at = datetime.now(timezone.utc)
                execution.error_message = str(error)[:1000]

            if schedule:
                schedule.last_run_at = datetime.now(timezone.utc)
                schedule.last_status = "failed"
                schedule.last_error = str(error)[:1000]

            db.session.commit()
        except Exception as db_err:
            logger.error(f"Failed to record execution failure: {db_err}")
            db.session.rollback()

    @staticmethod
    def _record_rollback(
        execution: JobExecution,
        schedule: Schedule,
        api: SpotifyAPI,
        ve,
        schedule_id: int,
    ) -> None:
        """Handle a verification or partial-write failure by
        restoring the auto-snapshots taken during this job,
        marking the execution `failed_rolled_back`, and
        emitting a structured ActivityLog entry.

        Accepts either a `PlaylistVerificationError` (F1) or a
        `SpotifyPartialBatchError` (F4). The metadata payload
        is shaped to the error type.

        If restoration itself fails (Spotify down, snapshot
        missing), falls through to `_record_failure` so the
        execution is still recorded as failed.
        """

        if isinstance(ve, PlaylistVerificationError):
            logger.error(
                "Schedule %s: verification failed in phase "
                "'%s' on playlist %s — attempting snapshot "
                "rollback. Missing=%d, extra=%d.",
                schedule_id,
                ve.phase,
                ve.playlist_id,
                len(ve.missing),
                len(ve.extra),
            )
        else:
            logger.error(
                "Schedule %s: partial %s write on playlist "
                "%s (batch %d/%d) — attempting snapshot "
                "rollback. Completed=%d, remaining=%d. "
                "Cause: %s",
                schedule_id,
                ve.method,
                ve.playlist_id,
                ve.completed_batches,
                ve.total_batches,
                len(ve.completed_uris),
                len(ve.remaining_uris),
                ve.cause,
            )

        restored = _restore_job_snapshots(
            execution,
            schedule,
            api,
            schedule_id,
        )
        if restored is None:
            JobExecutorService._record_failure(
                execution,
                schedule,
                ve,
                schedule_id,
            )
            return

        # Composite atomicity: a raid step may have staged pending tracks
        # before a later step failed. Now that the playlists are restored,
        # revert that staging so the whole job is undone (SR-008).
        _revert_job_raid_staging(execution, schedule)

        _persist_rollback_status(
            execution,
            schedule,
            ve,
            schedule_id,
        )

        # Log a structured activity entry (non-blocking).
        try:
            from shuffify.services.activity_log_service import (  # noqa: E501
                ActivityLogService,
            )

            ActivityLogService.log(
                user_id=schedule.user_id,
                activity_type=(ActivityType.SCHEDULE_RUN_ROLLED_BACK),
                description=(
                    "Scheduled "
                    f"{schedule.job_type} on "
                    f"'{schedule.target_playlist_name}'"
                    f" rolled back after "
                    f"{_rollback_trigger_phrase(ve)}"
                ),
                playlist_id=schedule.target_playlist_id,
                playlist_name=(schedule.target_playlist_name),
                metadata=_rollback_metadata(
                    ve,
                    schedule,
                    restored,
                ),
            )
        except Exception:
            pass

    @staticmethod
    def execute_now(schedule_id: int, user_id: int) -> dict:
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
            schedule = SchedulerService.get_schedule(schedule_id, user_id)
        except ScheduleNotFoundError:
            raise JobExecutionError(f"Schedule {schedule_id} not found")

        # Execute synchronously
        JobExecutorService.execute(schedule_id)

        # Reload to get updated status from a clean state
        db.session.expire(schedule)
        db.session.refresh(schedule)

        if schedule.last_status in (
            "failed",
            "failed_rolled_back",
        ):
            raise JobExecutionError(schedule.last_error or "Unknown error")

        # Return detailed result from latest execution
        latest = (
            JobExecution.query.filter_by(schedule_id=schedule_id)
            .order_by(JobExecution.started_at.desc())
            .first()
        )

        result = {
            "status": schedule.last_status or "unknown",
            "last_run_at": (
                schedule.last_run_at.isoformat() if schedule.last_run_at else None
            ),
        }

        if latest:
            result["tracks_total"] = latest.tracks_total or 0
            result["tracks_added"] = latest.tracks_added or 0
            if latest.error_message:
                result["error"] = latest.error_message

        return result

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
            refresh_token = TokenService.decrypt_token(user.encrypted_refresh_token)
        except TokenEncryptionError as e:
            raise JobExecutionError(
                f"Failed to decrypt refresh token for user {user.spotify_id}: {e}"
            )

        try:
            from flask import current_app

            credentials = SpotifyCredentials.from_flask_config(current_app.config)
            auth_manager = SpotifyAuthManager(credentials)

            # Create a token_info with an expired access token
            # and valid refresh token. SpotifyAPI will
            # auto-refresh on first call.
            token_info = TokenInfo(
                access_token=_EXPIRED_ACCESS_TOKEN,
                token_type="Bearer",
                expires_at=0,
                refresh_token=refresh_token,
            )

            # Inject the Redis cache (None when Redis is unavailable) so
            # background jobs share the cached playlist/user data (SR-005).
            from shuffify import get_spotify_cache

            api = SpotifyAPI(
                token_info,
                auth_manager,
                auto_refresh=True,
                cache=get_spotify_cache(),
            )

            # Update stored refresh token if it was rotated
            new_token = api.token_info
            if new_token.refresh_token and new_token.refresh_token != refresh_token:
                user.encrypted_refresh_token = TokenService.encrypt_token(
                    new_token.refresh_token
                )
                db.session.commit()
                logger.info(f"Updated rotated refresh token for user {user.spotify_id}")

            return api

        except SpotifyTokenError as e:
            raise JobExecutionError(
                f"Failed to refresh Spotify token for user {user.spotify_id}: {e}"
            )

    @staticmethod
    def _execute_job_type(schedule: Schedule, api: SpotifyAPI) -> dict:
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
        from shuffify.services.executors.drip_executor import (  # noqa: E501
            execute_drip,
        )

        if schedule.job_type == JobType.RAID:
            return execute_raid(schedule, api)
        elif schedule.job_type == JobType.SHUFFLE:
            return execute_shuffle(schedule, api)
        elif schedule.job_type == JobType.RAID_AND_SHUFFLE:
            result = execute_raid(schedule, api)
            shuffle_result = execute_shuffle(schedule, api)
            result["tracks_total"] = shuffle_result["tracks_total"]
            return result
        elif schedule.job_type == JobType.RAID_AND_DRIP:
            result = execute_raid(schedule, api)
            drip_result = execute_drip(schedule, api)
            result["tracks_dripped"] = drip_result.get("tracks_added", 0)
            return result
        elif schedule.job_type == JobType.ROTATE:
            return execute_rotate(schedule, api)
        elif schedule.job_type == JobType.DRIP:
            return execute_drip(schedule, api)
        else:
            raise JobExecutionError(f"Unknown job type: {schedule.job_type}")
