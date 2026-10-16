"""
Base job executor: lifecycle, token management, dispatch,
and shared utilities.

Contains the JobExecutorService class which is the single public
entry point for all job execution. Operation-specific logic is
delegated to sibling modules (raid_executor, shuffle_executor,
rotate_executor, drip_executor).
"""

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import List

from sqlalchemy.exc import SQLAlchemyError

from shuffify.enums import ActivityType, JobType
from shuffify.models.db import JobExecution, Schedule, User, db
from shuffify.services.base import safe_commit
from shuffify.services.execution_result_service import (
    build_live_execution_detail,
    serialize_execution_detail,
)
from shuffify.services.live_operation_service import (
    LiveOperation,
    current_operation,
    read_tracks,
    rejected_result,
)
from shuffify.services.live_review_bindings import REVIEW_CHANGED
from shuffify.services.live_review_scope import (
    JOB_STEPS,
    InvalidSettings,
    ReviewChanged,
    ReviewedInputChanged,
    part_digest,
    step_part,
)
from shuffify.services.playlist_lock import playlist_lock
from shuffify.services.token_service import (
    TokenEncryptionError,
    TokenService,
)
from shuffify.shuffle_algorithms.utils import extract_uris
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import (
    SpotifyPartialBatchError,
    SpotifyTokenError,
)

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


class ExecutionDetailUnavailable(JobExecutionError):
    """A completed step could not be durably summarized; no next step may run."""


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
            both preserve the multiset but not the sequence.

    Returns:
        The actual URI list (in fetch order) on success.

    Raises:
        PlaylistVerificationError: If the actual state diverges from expected
            (by sequence when ordered, otherwise by multiset).
    """
    verified = read_tracks(
        api,
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
    from shuffify.models.db import PlaylistSnapshot
    from shuffify.services.playlist_snapshot_service import (  # noqa: E501
        PlaylistSnapshotError,
        PlaylistSnapshotService,
    )

    try:
        user_id = schedule.user_id if schedule else None
        since = execution.started_at if execution else None
        execution_id = execution.id if execution else None
        if not user_id or not since:
            raise PlaylistSnapshotError("Missing user_id or job start time; cannot scope snapshots.")

        # Prefer snapshots tagged with this job's execution id, so a manual
        # snapshot or a concurrent schedule taken during a long job can't be
        # swept into this rollback.
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
            # Fallback when no snapshot is tagged with this execution (no
            # execution id, or snapshots saved without one): scope by the job's
            # time window, so rollback never silently restores nothing.
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
                "No pre-snapshot available for rollback " "(auto-snapshots disabled or empty " "source playlists)."
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
    playlists are rolled back — a non-atomic side effect. Scoped to
    the schedule's target and the job window (``created_at >= started_at``).

    Returns the number of rows deleted. Best-effort — a failure here must not
    mask the rollback already under way.
    """
    from shuffify.enums import PendingRaidStatus
    from shuffify.models.db import PendingRaidTrack

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
                "Schedule %s: reverted %d raid tracks staged during a " "rolled-back job",
                getattr(schedule, "id", "?"),
                deleted,
            )
        return deleted
    except Exception as e:
        logger.warning("Failed to revert raid staging on rollback: %s", e)
        db.session.rollback()
        return 0


def _persist_rollback_status(execution, schedule, ve, schedule_id):
    """Write failed_rolled_back status to db."""
    try:
        if execution:
            execution.status = "failed_rolled_back"
            execution.completed_at = datetime.now(timezone.utc)
            execution.error_message = "Run failed; playlist restoration was verified."
            operation = current_operation()
            if operation:
                operation.refresh_failed_step()
                execution.result_json = build_live_execution_detail(
                    operation, execution.id, schedule.target_playlist_id
                )

        if schedule:
            schedule.last_run_at = datetime.now(timezone.utc)
            schedule.last_status = "failed_rolled_back"
            schedule.last_error = "Run failed; playlist restoration was verified."

        db.session.commit()
        return True
    except Exception as db_err:
        logger.error(
            "Schedule %s: failed to persist rollback status: %s",
            schedule_id,
            db_err,
        )
        db.session.rollback()
        return False


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
    def _run_job(schedule, schedule_id, *, triggered_by="scheduler", review=None) -> dict:
        """Run once; retain exact identity and resource facts through DB failure.

        ``review`` (a ``ReviewedRun``) binds the run to its review: it compares
        inside the target lock, before any record, Activity or dispatch.
        """
        kind = schedule.job_type
        identity = {"execution_id": None, "started_at": None, "execution_state": "not_started"}
        execution = operation = None
        detail = None
        snapshot_token = operation_token = None
        result = {"tracks_added": 0, "tracks_total": 0}
        locked_target = schedule.target_playlist_id
        try:
            with playlist_lock(locked_target) as acquired:
                if not acquired:
                    execution = JobExecutorService._record_lock_timeout(schedule, schedule_id)
                    rejected = rejected_result(
                        kind,
                        error_code="playlist_busy",
                        retryable=True,
                        http_status=409,
                        message="Another run is using this playlist. This attempt made no changes.",
                    )
                    if execution is not None:
                        rejected.update(
                            execution_id=execution.id,
                            started_at=execution.started_at.isoformat(),
                            execution_state="recorded",
                        )
                    rejected["status"] = "skipped"
                    return rejected
                reviewed_parts = None
                if review is not None:
                    reviewed_parts, rejected = JobExecutorService._bind_review(
                        schedule, schedule_id, locked_target, review, kind
                    )
                    if rejected is not None:
                        return rejected
                # Error handling and any compensation remain inside this lock.
                try:
                    execution = JobExecutorService._create_execution_record(schedule_id)
                    identity.update(
                        execution_id=execution.id,
                        started_at=execution.started_at.isoformat(),
                        execution_state="recorded",
                    )
                    from shuffify.services.playlist_snapshot_service import set_current_job_execution

                    snapshot_token = set_current_job_execution(execution.id)
                    user = db.session.get(User, schedule.user_id)
                    if not user:
                        raise JobExecutionError("User is unavailable")
                    api = JobExecutorService._get_spotify_api(user)
                    operation = LiveOperation(kind, api, user)
                    operation.execution_id = execution.id
                    operation.triggered_by = triggered_by
                    operation.locked_target = locked_target
                    operation.reviewed_parts = reviewed_parts
                    operation_token = operation.activate()
                    result = JobExecutorService._execute_job_type(schedule, api)
                    try:
                        JobExecutorService._record_success(execution, schedule, result)
                        operation.book("execution_record", "complete")
                    except Exception:
                        db.session.rollback()
                        operation.book("execution_record", "unknown", error_code="execution_record_failed")
                        operation.error_code = "execution_record_failed"
                    outcome = operation.outcome()
                    detail = JobExecutorService._result_detail(execution, operation, schedule, identity)
                    complete = outcome["aggregate_status"] == "complete"
                    gate_closed = result.get("skipped_reason") == "drip_disabled"
                    skipped = gate_closed and not operation.has_effects() and outcome["aggregate_status"] != "unknown"
                    return {
                        **result,
                        **identity,
                        "status": "success" if complete else "skipped" if skipped else "failed",
                        "operation": outcome,
                        "result_detail": detail,
                        "http_status": 200 if complete else 409 if skipped else 502,
                        "message": (
                            "Run completed."
                            if complete
                            else "Drip is paused for this collection. This step did not run."
                            if skipped
                            else "Drip is paused. Completed changes from earlier steps are retained."
                            if gate_closed and outcome["aggregate_status"] != "unknown"
                            else "Playlist results are retained; recording this run needs attention."
                        ),
                    }
                except Exception as error:
                    restored = False
                    if operation:
                        operation.error_code = operation.error_code or "execution_failed"
                        if isinstance(error, (PlaylistVerificationError, SpotifyPartialBatchError)):
                            restored = JobExecutorService._record_rollback(
                                execution,
                                schedule,
                                operation.api,
                                error,
                                schedule_id,
                            )
                        else:
                            persisted = JobExecutorService._record_failure(execution, schedule, error, schedule_id)
                            record = next((b for b in operation.bookkeeping
                                           if b["kind"] == "execution_record"), None)
                            if not record or record["status"] != "unknown":
                                operation.book(
                                    "execution_record",
                                    "complete" if persisted else "unknown",
                                    error_code=None if persisted else "execution_record_failed",
                                )
                        if not isinstance(error, ExecutionDetailUnavailable):
                            operation.refresh_failed_step()
                        outcome = operation.outcome()
                        detail = JobExecutorService._result_detail(execution, operation, schedule, identity)
                        # Preserve completed composite counts; unknown work is not zero.
                        previous = operation.counts
                        result = {
                            **previous,
                            "tracks_added": previous.get("tracks_added"),
                            "tracks_total": previous.get("tracks_total"),
                        }
                        if "tracks_added" not in previous and not any(r["dispatched"] for r in operation.resources):
                            result["tracks_added"] = 0
                    else:
                        JobExecutorService._record_failure(execution, schedule, error, schedule_id)
                        outcome = rejected_result(kind, error_code="preflight_failed", retryable=True)["operation"]
                    message = (
                        "The run did not complete. Changes from the failed step were verified as restored; "
                        "earlier completed steps are retained."
                        if restored
                        else "The run did not complete. Review the resource results before another run."
                    )
                    return {
                        **result,
                        **identity,
                        "status": "failed_rolled_back" if restored else "failed",
                        "operation": outcome,
                        "result_detail": detail if operation else serialize_execution_detail(None),
                        "http_status": 502,
                        "message": message,
                        "error": message,
                    }
        except Exception:
            if operation is not None:
                operation.book("coordination", "unknown", error_code="coordination_failed")
                operation.error_code = "coordination_failed"
                detail = JobExecutorService._result_detail(execution, operation, schedule, identity)
                return {
                    **result,
                    **identity,
                    "status": "failed",
                    "operation": operation.outcome(),
                    "result_detail": detail,
                    "http_status": 502,
                    "message": "Playlist results are retained; coordination could not be confirmed.",
                }
            rejected = rejected_result(kind, error_code="coordination_failed", retryable=True, http_status=502)
            rejected.update(identity)
            return rejected
        finally:
            if operation is not None:
                JobExecutorService._log_live_activity(schedule, operation, identity, detail, execution)
            if operation_token is not None:
                LiveOperation.reset(operation_token)
            if snapshot_token is not None:
                from shuffify.services.playlist_snapshot_service import reset_current_job_execution

                reset_current_job_execution(snapshot_token)

    @staticmethod
    def _bind_review(schedule, schedule_id, locked_target, review, kind):
        """Compare a reviewed run inside its target lock, before any record or dispatch.

        Returns ``(parts, None)`` when the recomputed scope is the reviewed one.
        Otherwise ``(None, rejection)``: no execution row, schedule change,
        Activity or dispatch. A saved routine is compared as saved now, not as
        loaded before the lock wait, and must still target the locked playlist.
        """
        try:
            if schedule_id is not None and (
                db.session.get(Schedule, schedule_id, populate_existing=True) is not schedule
            ):
                raise ReviewChanged()
            if schedule.target_playlist_id != locked_target:
                raise ReviewChanged()
            return review.bind(schedule), None
        except (ReviewChanged, InvalidSettings):
            # Settings that no longer compute or fingerprint were changed after their review.
            return None, rejected_result(kind, error_code="review_changed", http_status=409, message=REVIEW_CHANGED)
        except SQLAlchemyError:
            db.session.rollback()
            return None, rejected_result(
                kind,
                error_code="database_read_failed",
                retryable=True,
                http_status=502,
                message="The reviewed settings could not be read. This attempt made no changes.",
            )

    @staticmethod
    def execute_raid_for_user(
        user_id: int,
        target_playlist_id: str,
        source_playlist_ids=None,
        target_playlist_name: str = None,
        review=None,
    ) -> dict:
        """Run an inline (schedule-less) raid through the full executor safety
        rails, so a manual "Raid Now" behaves identically to a scheduled raid:
        per-playlist lock, JobExecution record, auto-snapshots, rollback, and
        activity logging.

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
        return JobExecutorService._run_job(schedule, None, triggered_by="manual", review=review)

    @staticmethod
    def execute_drip_for_user(
        user_id: int,
        target_playlist_id: str,
        target_playlist_name: str = None,
        drip_count: int = None,
        review=None,
    ) -> dict:
        """Run a manual drip with locking, execution recording, and rollback.

        Uses a *transient* Schedule (never persisted); the JobExecution is
        recorded with a null ``schedule_id``.

        A manual drip is forced: it runs regardless of the raid link's
        ``drip_enabled`` setting. That intent travels in ``algorithm_params``
        alongside ``drip_count`` rather than by pre-setting the flag on the
        link, which would leave a pending ORM mutation that the next commit on
        the session persists -- turning a one-off action into a stored setting.
        """
        params = {"force": True}
        if drip_count:
            params["drip_count"] = drip_count
        schedule = Schedule(
            user_id=user_id,
            job_type=JobType.DRIP,
            target_playlist_id=target_playlist_id,
            target_playlist_name=(target_playlist_name or target_playlist_id),
            algorithm_params=params,
            is_enabled=True,
        )
        return JobExecutorService._run_job(schedule, None, triggered_by="manual", review=review)

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
    def _result_detail(execution, operation, schedule, identity):
        """Expose saved detail for the exact row; a failed record is explicit."""
        if execution is None or identity.get("execution_id") is None:
            return serialize_execution_detail(None)
        record_unknown = any(
            item.get("kind") == "execution_record" and item.get("status") == "unknown"
            for item in operation.bookkeeping
        )
        if record_unknown:
            try:
                live = build_live_execution_detail(
                    operation, identity["execution_id"], schedule.target_playlist_id,
                    execution_record="unknown",
                )
                return serialize_execution_detail(live)
            except Exception:
                return serialize_execution_detail(None)
        try:
            if execution.result_json:
                return serialize_execution_detail(execution.result_json)
        except Exception:
            db.session.rollback()
        try:
            live = build_live_execution_detail(
                operation, identity["execution_id"], schedule.target_playlist_id,
                execution_record="unknown",
            )
            return serialize_execution_detail(live)
        except Exception:
            return serialize_execution_detail(None)

    @staticmethod
    def _persist_step_detail(operation):
        """Commit a verified completed step before dispatching the next one."""
        execution_id = getattr(operation, "execution_id", None)
        if execution_id is None:
            return
        try:
            execution = db.session.get(JobExecution, execution_id)
            execution.result_json = build_live_execution_detail(
                operation, execution_id, operation.locked_target, run_state="running"
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            operation.book("execution_record", "unknown", error_code="execution_record_failed")
            operation.error_code = "execution_record_failed"
            raise ExecutionDetailUnavailable("Execution detail could not be saved before the next step")

    @staticmethod
    def _record_lock_timeout(schedule, schedule_id):
        """Record a JobExecution for a run skipped because the per-playlist
        lock did not release within the timeout.

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
            return execution
        except Exception as e:
            logger.warning(
                "Failed to record lock-timeout execution for schedule " "%s: %s",
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
        """Record a completed invocation, including an unmet Drip prerequisite."""
        operation = current_operation()
        gate_closed = result.get("skipped_reason") == "drip_disabled"
        applied = operation is not None and operation.has_effects()
        execution.status = ("failed" if applied else "skipped") if gate_closed else "success"
        execution.completed_at = datetime.now(timezone.utc)
        execution.tracks_added = result.get("tracks_added", 0)
        execution.tracks_total = result.get("tracks_total", 0)
        if operation is not None:
            execution.result_json = build_live_execution_detail(
                operation, execution.id, schedule.target_playlist_id
            )

        schedule.last_run_at = datetime.now(timezone.utc)
        schedule.last_status = execution.status
        schedule.last_error = (
            "Run partly completed; Drip is paused for this collection."
            if gate_closed and applied
            else "Drip is paused for this collection." if gate_closed else None
        )
        execution.error_message = schedule.last_error

        db.session.commit()

        # No mutation Activity for an observed no-op; the execution row remains.
        if operation is not None:
            # The runner logs confirmed effects once, including partial runs.
            return
        # Log activity (non-blocking)
        try:
            from shuffify.services.activity_log_service import (  # noqa: E501
                ActivityLogService,
            )

            ActivityLogService.log(
                user_id=schedule.user_id,
                activity_type=(ActivityType.SCHEDULE_RUN),
                description=(
                    f"Scheduled " f"{schedule.job_type} on " f"'{schedule.target_playlist_name}'" f" completed"
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
    def _log_live_activity(schedule, operation, identity, result_detail=None, execution=None):
        """Log confirmed effects once; no mutation log for no-op/unknown-only."""
        effects = [r for r in operation.resources if r["dispatched"] and r["status"] in ("complete", "restored")]
        database_effect = any(
            b["kind"] in ("pending_stage", "pending_promotion") and b["status"] == "complete"
            for b in operation.bookkeeping
        )
        if not effects and not database_effect:
            return
        if result_detail is None:
            result_detail = JobExecutorService._result_detail(execution, operation, schedule, identity)
        try:
            from shuffify.services.activity_log_service import ActivityLogService

            restored = any(r["status"] == "restored" for r in effects)
            origin = "Manual" if operation.triggered_by == "manual" else "Scheduled"
            ActivityLogService.log(
                user_id=schedule.user_id,
                activity_type=ActivityType.SCHEDULE_RUN_ROLLED_BACK if restored else ActivityType.SCHEDULE_RUN,
                description=f"{origin} {operation.kind}: "
                + ("playlist restoration verified" if restored else "confirmed changes recorded"),
                playlist_id=schedule.target_playlist_id,
                playlist_name=next(
                    (r["resource_name"] for r in effects if r["resource"] == schedule.target_playlist_id),
                    schedule.target_playlist_name,
                ),
                metadata={
                    "schedule_id": schedule.id,
                    **identity,
                    "triggered_by": operation.triggered_by,
                    "aggregate_status": operation.outcome()["aggregate_status"],
                    "result_detail": result_detail,
                    "resources": [
                        {key: r[key] for key in ("resource", "resource_name", "step", "status", "affected_count")}
                        for r in effects
                    ],
                },
            )
        except Exception:
            logger.warning("Could not record confirmed run activity", exc_info=True)

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
                execution.error_message = "Run failed; review its resource results."
                operation = current_operation()
                if operation:
                    if not isinstance(error, ExecutionDetailUnavailable):
                        operation.refresh_failed_step()
                    execution.result_json = build_live_execution_detail(
                        operation, execution.id, schedule.target_playlist_id,
                        execution_record=(
                            "unknown" if any(
                                b.get("kind") == "execution_record" and b.get("status") == "unknown"
                                for b in operation.bookkeeping
                            ) else "complete"
                        ),
                    )

            if schedule:
                schedule.last_run_at = datetime.now(timezone.utc)
                schedule.last_status = "failed"
                schedule.last_error = "Run failed; review its resource results."

            db.session.commit()
            return True
        except Exception as db_err:
            logger.error(f"Failed to record execution failure: {db_err}")
            db.session.rollback()
            return False

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

        Accepts either a `PlaylistVerificationError` or a
        `SpotifyPartialBatchError`. The metadata payload
        is shaped to the error type.

        If restoration itself fails (Spotify down, snapshot
        missing), falls through to `_record_failure` so the
        execution is still recorded as failed.
        """

        operation = current_operation()
        if operation is not None:
            restored = operation.compensate()
            if restored:
                # Revert only exact rows changed by the failing step. Earlier
                # completed Raid staging belongs to retained composite work.
                try:
                    from shuffify.enums import PendingRaidStatus
                    from shuffify.models.db import PendingRaidTrack

                    if operation.step == "drip" and operation.promoted_ids:
                        PendingRaidTrack.query.filter(
                            PendingRaidTrack.id.in_(operation.promoted_ids),
                            PendingRaidTrack.user_id == schedule.user_id,
                        ).update({"status": PendingRaidStatus.PENDING, "resolved_at": None}, synchronize_session=False)
                    if operation.step == "raid" and operation.staged_ids:
                        PendingRaidTrack.query.filter(
                            PendingRaidTrack.id.in_(operation.staged_ids),
                            PendingRaidTrack.user_id == schedule.user_id,
                        ).delete(synchronize_session=False)
                    db.session.commit()
                    for item in operation.bookkeeping:
                        if (
                            item["step"] == operation.step
                            and item["kind"] in ("pending_stage", "pending_promotion")
                            and item["status"] == "complete"
                        ):
                            item["status"] = "restored"
                except Exception:
                    db.session.rollback()
                    operation.book("pending_restore", "unknown", error_code="database_restore_failed")
                    restored = False
            if restored:
                persisted = _persist_rollback_status(execution, schedule, ve, schedule_id)
            else:
                persisted = JobExecutorService._record_failure(execution, schedule, ve, schedule_id)
            operation.book(
                "execution_record",
                "complete" if persisted else "unknown",
                error_code=None if persisted else "execution_record_failed",
            )
            return restored

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
        # revert that staging so the whole job is undone.
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
    def execute_now(schedule_id: int, user_id: int, review=None) -> dict:
        """
        Manually trigger a schedule execution (from the UI).

        Raises:
            JobExecutionError: If execution fails.
        """
        from shuffify.services.scheduler_service import (
            ScheduleNotFoundError,
            SchedulerService,
        )

        try:
            schedule = SchedulerService.get_schedule(schedule_id, user_id)
        except ScheduleNotFoundError:
            return rejected_result(
                "schedule", error_code="schedule_not_found", http_status=404, message="Schedule not found."
            )

        return JobExecutorService._run_job(schedule, schedule_id, triggered_by="manual", review=review)

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
            raise JobExecutionError(f"Failed to decrypt refresh token for user {user.spotify_id}: {e}")

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
            # background jobs share the cached playlist/user data.
            from shuffify import get_spotify_cache

            def store_rotated_refresh_token(new_token: TokenInfo) -> None:
                # Spotify may issue a new refresh token on any refresh -- the
                # one at construction, a 401 retry, an expiry mid-job -- and
                # the old one can stop working. Store each the moment it
                # arrives, or the user's next job fails until they log in.
                nonlocal refresh_token
                if not new_token.refresh_token or new_token.refresh_token == refresh_token:
                    return
                user.encrypted_refresh_token = TokenService.encrypt_token(new_token.refresh_token)
                # On failure safe_commit rolls back, logs and raises. SpotifyAPI
                # swallows that, so the job runs on, and the next refresh,
                # still differing from the stored token, retries the store.
                safe_commit(f"store the rotated refresh token for user {user.spotify_id}")
                refresh_token = new_token.refresh_token

            return SpotifyAPI(
                token_info,
                auth_manager,
                auto_refresh=True,
                cache=get_spotify_cache(),
                on_token_refresh=store_rotated_refresh_token,
            )

        except SpotifyTokenError as e:
            raise JobExecutionError(f"Failed to refresh Spotify token for user {user.spotify_id}: {e}")

    @staticmethod
    def _execute_job_type(schedule: Schedule, api: SpotifyAPI) -> dict:
        """Execute the appropriate operation based on job type."""
        from shuffify.services.executors.drip_executor import (  # noqa: E501
            execute_drip,
        )
        from shuffify.services.executors.raid_executor import (
            execute_raid,
        )
        from shuffify.services.executors.rotate_executor import (  # noqa: E501
            execute_rotate,
        )
        from shuffify.services.executors.shuffle_executor import (  # noqa: E501
            execute_shuffle,
        )

        executors = {"raid": execute_raid, "shuffle": execute_shuffle, "drip": execute_drip, "rotate": execute_rotate}
        if schedule.job_type not in JOB_STEPS:
            raise JobExecutionError("Unknown job type")
        operation = current_operation()
        result = {}
        job_steps = [(name, executors[name]) for name in JOB_STEPS[schedule.job_type]]
        for index, (name, execute_step) in enumerate(job_steps):
            if operation:
                operation.step = name
            started = False
            try:
                if operation:
                    JobExecutorService._check_step(schedule, operation, name)
                started = True
                step_result = execute_step(schedule, api)
            except Exception as error:
                if operation:
                    # A step refused by its check, or by a reviewed input before it consumed it,
                    # never started; neither did any later step. Facts name the locked target.
                    started = started and not isinstance(error, ReviewedInputChanged)
                    if started:
                        operation.record_failed_step(name)
                    operation.record_not_started([step for step, _ in job_steps[index + int(started):]])
                if operation and len(job_steps) > 1:
                    # A refused step never started, matching its not-started facts.
                    status = operation.step_status(name) if started else "not_started"
                    operation.steps.append({"step": name, "status": status})
                    operation.steps.extend(
                        {"step": remaining_name, "status": "not_started"}
                        for remaining_name, _ in job_steps[index + 1 :]
                    )
                raise
            if name == "drip" and result:
                result["tracks_dripped"] = step_result.get("tracks_added")
                result["tracks_total"] = step_result.get("tracks_total")
            elif name == "shuffle" and result:
                result["tracks_total"] = step_result.get("tracks_total")
            else:
                result.update(step_result)
            if operation:
                operation.counts = dict(result)
                operation.steps.append({
                    "step": name,
                    "status": "not_started" if step_result.get("skipped_reason") == "drip_disabled" else "complete",
                    **step_result,
                })
                if len(job_steps) > 1:
                    try:
                        JobExecutorService._persist_step_detail(operation)
                    except ExecutionDetailUnavailable:
                        remaining = [step for step, _ in job_steps[index + 1:]]
                        operation.record_not_started(remaining)
                        operation.steps.extend({"step": step, "status": "not_started"} for step in remaining)
                        raise
            if step_result.get("skipped_reason") == "drip_disabled":
                result["skipped_reason"] = step_result["skipped_reason"]
                if operation:
                    operation.error_code = step_result["skipped_reason"]
                    operation.retryable = not operation.has_effects()
                    operation.record_not_started([step for step, _ in job_steps[index + 1:]])
                    operation.steps.extend(
                        {"step": remaining_name, "status": "not_started"}
                        for remaining_name, _ in job_steps[index + 1 :]
                    )
                break
        return result

    @staticmethod
    def _check_step(schedule, operation, step):
        """Before a step dispatches anything, fail it if its scope moved.

        The record commit reloads a saved routine, so every run checks that it
        still targets the locked playlist; a reviewed run also recomputes the
        step's inputs. Within-step reads after this check remain a residual
        window: this is a comparison, not a configuration lock.
        """
        if schedule.target_playlist_id != operation.locked_target:
            operation.error_code = "resource_changed"
            raise JobExecutionError("The routine's playlist changed after this run locked its target")
        reviewed = operation.reviewed_parts
        if reviewed is None:
            return
        try:
            changed = tuple(reviewed) != JOB_STEPS.get(schedule.job_type) or part_digest(
                step, step_part(step, schedule)
            ) != part_digest(step, reviewed[step])
        except SQLAlchemyError:
            db.session.rollback()
            operation.error_code = "database_read_failed"
            raise JobExecutionError("The reviewed settings could not be read")
        except InvalidSettings:  # Settings that no longer compute or fingerprint changed after the review.
            changed = True
        if changed:
            operation.error_code = "review_changed"
            raise JobExecutionError("The reviewed action changed before this step ran")
