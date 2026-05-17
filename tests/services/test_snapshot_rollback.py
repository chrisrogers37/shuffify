"""
Tests for the snapshot rollback path triggered when an
executor raises PlaylistVerificationError.

Covers _record_rollback behavior on
JobExecutorService.execute:
    - Pre-snapshots created during the job are restored
    - JobExecution.status set to failed_rolled_back
    - Schedule.last_status set to failed_rolled_back
    - ActivityLog entry has structured diff payload
    - Drip's two pre-snapshots (target + raid) both restored
    - When snapshot restoration itself fails, fall through to
      _record_failure (status=failed, broken state preserved)
    - Non-PlaylistVerificationError exceptions still go to
      _record_failure unchanged
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from shuffify.enums import (
    ActivityType,
    IntervalValue,
    JobType,
    ScheduleType,
    SnapshotType,
)
from shuffify.models.db import (
    ActivityLog,
    JobExecution,
    PlaylistSnapshot,
    Schedule,
    db,
)
from shuffify.services.executors import (
    JobExecutorService,
    PlaylistVerificationError,
)
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)
from shuffify.services.user_service import UserService


@pytest.fixture
def user(db_app):
    """Provide a test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify(
            {
                "id": "rollbackuser",
                "display_name": "Rollback User",
                "images": [],
            }
        )
        yield result.user


@pytest.fixture
def schedule(user):
    sched = Schedule(
        user_id=user.id,
        job_type=JobType.SHUFFLE,
        target_playlist_id="target_rb",
        target_playlist_name="My Playlist",
        schedule_type=ScheduleType.INTERVAL,
        schedule_value=IntervalValue.DAILY,
        algorithm_name="BasicShuffle",
        is_enabled=True,
    )
    db.session.add(sched)
    db.session.commit()
    return sched


def _make_pve(playlist_id="target_rb", schedule_id=1):
    """Build a PlaylistVerificationError with a known
    missing/extra diff."""
    return PlaylistVerificationError(
        playlist_id=playlist_id,
        expected=["u1", "u2", "u3"],
        actual=["u1", "u2", "u4"],
        schedule_id=schedule_id,
        phase="shuffle",
    )


def _make_api():
    """Mock SpotifyAPI for rollback calls."""
    api = MagicMock()
    api.update_playlist_tracks.return_value = True
    return api


def _seed_snapshot(
    user_id, playlist_id, uris,
    snapshot_type=SnapshotType.SCHEDULED_PRE_EXECUTION,
):
    """Seed a PlaylistSnapshot row directly (bypassing
    create_snapshot's retention enforcement which is
    irrelevant here)."""
    snap = PlaylistSnapshot(
        user_id=user_id,
        playlist_id=playlist_id,
        playlist_name=playlist_id,
        track_count=len(uris),
        snapshot_type=snapshot_type,
        trigger_description="test seed",
    )
    snap.track_uris = uris
    db.session.add(snap)
    db.session.commit()
    return snap


class TestRollbackRestoresPreSnapshot:
    """Happy path: PVE → snapshot restored → status set."""

    def test_rollback_restores_pre_snapshot_uris(
        self, user, schedule,
    ):
        api = _make_api()
        execution = JobExecution(
            schedule_id=schedule.id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        db.session.commit()

        snap = _seed_snapshot(
            user.id, "target_rb",
            ["u1", "u2", "u3"],
        )

        ve = _make_pve(schedule_id=schedule.id)
        JobExecutorService._record_rollback(
            execution, schedule, api, ve, schedule.id,
        )

        api.update_playlist_tracks.assert_called_once_with(
            "target_rb", ["u1", "u2", "u3"],
        )
        assert snap.id is not None

    def test_status_set_to_failed_rolled_back(
        self, user, schedule,
    ):
        api = _make_api()
        execution = JobExecution(
            schedule_id=schedule.id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        db.session.commit()

        _seed_snapshot(
            user.id, "target_rb",
            ["u1", "u2", "u3"],
        )

        ve = _make_pve(schedule_id=schedule.id)
        JobExecutorService._record_rollback(
            execution, schedule, api, ve, schedule.id,
        )

        db.session.refresh(execution)
        db.session.refresh(schedule)
        assert execution.status == "failed_rolled_back"
        assert schedule.last_status == (
            "failed_rolled_back"
        )
        assert execution.error_message is not None

    def test_activity_log_payload(self, user, schedule):
        api = _make_api()
        execution = JobExecution(
            schedule_id=schedule.id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        db.session.commit()

        snap = _seed_snapshot(
            user.id, "target_rb",
            ["u1", "u2", "u3"],
        )

        ve = _make_pve(schedule_id=schedule.id)
        JobExecutorService._record_rollback(
            execution, schedule, api, ve, schedule.id,
        )

        log = (
            ActivityLog.query
            .filter_by(
                user_id=user.id,
                activity_type=(
                    ActivityType.SCHEDULE_RUN_ROLLED_BACK
                ),
            )
            .first()
        )
        assert log is not None
        meta = log.metadata_json
        assert meta["phase"] == "shuffle"
        assert meta["failing_playlist_id"] == "target_rb"
        assert meta["expected_count"] == 3
        assert meta["actual_count"] == 3
        assert meta["missing_total"] == 1
        assert meta["extra_total"] == 1
        assert meta["missing_uris"] == ["u3"]
        assert meta["extra_uris"] == ["u4"]
        assert meta["restored"] == [
            {
                "playlist_id": "target_rb",
                "snapshot_id": snap.id,
                "track_count": 3,
            }
        ]


class TestRollbackSnapshotLookup:
    """The lookup uses (user_id, created_at >=
    execution.started_at)."""

    def test_only_snapshots_after_job_start_restored(
        self, user, schedule,
    ):
        """A snapshot older than the job start is NOT
        treated as a pre-state for this run."""
        # Pre-existing snapshot from a previous run.
        old = _seed_snapshot(
            user.id, "target_rb", ["old1", "old2"],
        )
        # Backdate it.
        old.created_at = datetime(
            2020, 1, 1, tzinfo=timezone.utc,
        )
        db.session.commit()

        api = _make_api()
        execution = JobExecution(
            schedule_id=schedule.id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        db.session.commit()

        # In-run snapshot.
        recent = _seed_snapshot(
            user.id, "target_rb",
            ["u1", "u2", "u3"],
        )

        ve = _make_pve(schedule_id=schedule.id)
        JobExecutorService._record_rollback(
            execution, schedule, api, ve, schedule.id,
        )

        api.update_playlist_tracks.assert_called_once_with(
            "target_rb", ["u1", "u2", "u3"],
        )
        assert recent.id != old.id

    def test_no_snapshot_falls_through_to_failure(
        self, user, schedule,
    ):
        """No pre-snapshot in scope → _record_failure
        path → status=failed, broken state preserved."""
        api = _make_api()
        execution = JobExecution(
            schedule_id=schedule.id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        db.session.commit()

        ve = _make_pve(schedule_id=schedule.id)
        JobExecutorService._record_rollback(
            execution, schedule, api, ve, schedule.id,
        )

        api.update_playlist_tracks.assert_not_called()
        db.session.refresh(execution)
        assert execution.status == "failed"
        # No activity-log entry for the rolled-back path.
        log = (
            ActivityLog.query
            .filter_by(
                activity_type=(
                    ActivityType.SCHEDULE_RUN_ROLLED_BACK
                ),
            )
            .first()
        )
        assert log is None


class TestRollbackMultiPlaylist:
    """Drip / rotate take two pre-snapshots — both restore."""

    def test_drip_restores_both_target_and_raid(
        self, user, schedule,
    ):
        api = _make_api()
        execution = JobExecution(
            schedule_id=schedule.id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        db.session.commit()

        _seed_snapshot(
            user.id, "target_rb",
            ["t1", "t2"],
            snapshot_type=SnapshotType.AUTO_PRE_DRIP,
        )
        _seed_snapshot(
            user.id, "raid_rb",
            ["r1", "r2", "r3"],
            snapshot_type=SnapshotType.AUTO_PRE_DRIP,
        )

        ve = _make_pve(
            playlist_id="raid_rb",
            schedule_id=schedule.id,
        )
        JobExecutorService._record_rollback(
            execution, schedule, api, ve, schedule.id,
        )

        # Both playlists restored.
        calls = api.update_playlist_tracks.call_args_list
        applied = {c[0][0]: list(c[0][1]) for c in calls}
        assert applied == {
            "target_rb": ["t1", "t2"],
            "raid_rb": ["r1", "r2", "r3"],
        }

    def test_latest_snapshot_per_playlist_wins(
        self, user, schedule,
    ):
        """If two snapshots exist for the same playlist
        (e.g., create_snapshot called twice in a single
        run), restore the latest."""
        api = _make_api()
        execution = JobExecution(
            schedule_id=schedule.id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        db.session.commit()

        _seed_snapshot(
            user.id, "target_rb", ["v1"],
        )
        latest = _seed_snapshot(
            user.id, "target_rb",
            ["v2", "v3"],
        )

        ve = _make_pve(schedule_id=schedule.id)
        JobExecutorService._record_rollback(
            execution, schedule, api, ve, schedule.id,
        )

        api.update_playlist_tracks.assert_called_once_with(
            "target_rb", ["v2", "v3"],
        )
        assert latest.track_count == 2


class TestRollbackFailureFallthrough:
    """If restoration itself fails, fall back to plain
    failure (don't silently swallow the verify error)."""

    def test_restore_api_failure_marks_failed(
        self, user, schedule,
    ):
        api = _make_api()
        api.update_playlist_tracks.side_effect = (
            RuntimeError("spotify down")
        )

        execution = JobExecution(
            schedule_id=schedule.id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        db.session.commit()

        _seed_snapshot(
            user.id, "target_rb",
            ["u1", "u2", "u3"],
        )

        ve = _make_pve(schedule_id=schedule.id)
        JobExecutorService._record_rollback(
            execution, schedule, api, ve, schedule.id,
        )

        db.session.refresh(execution)
        assert execution.status == "failed"


class TestExecuteDispatchOnPVE:
    """End-to-end: execute() must catch PVE and route
    through _record_rollback, not _record_failure."""

    def test_execute_routes_pve_to_rollback(
        self, user, schedule,
    ):
        """A PVE raised by the executor lands in
        _record_rollback (not _record_failure)."""
        with patch(
            "shuffify.services.executors.base_executor"
            ".JobExecutorService._get_spotify_api"
        ) as mock_get_api, patch(
            "shuffify.services.executors.base_executor"
            ".JobExecutorService._execute_job_type"
        ) as mock_exec, patch(
            "shuffify.services.executors.base_executor"
            ".JobExecutorService._record_rollback"
        ) as mock_rb, patch(
            "shuffify.services.executors.base_executor"
            ".JobExecutorService._record_failure"
        ) as mock_fail:
            mock_get_api.return_value = _make_api()
            ve = _make_pve(schedule_id=schedule.id)
            mock_exec.side_effect = ve

            JobExecutorService.execute(schedule.id)

            mock_rb.assert_called_once()
            mock_fail.assert_not_called()

    def test_execute_routes_other_errors_to_failure(
        self, user, schedule,
    ):
        """A non-PVE exception still goes to
        _record_failure."""
        with patch(
            "shuffify.services.executors.base_executor"
            ".JobExecutorService._get_spotify_api"
        ) as mock_get_api, patch(
            "shuffify.services.executors.base_executor"
            ".JobExecutorService._execute_job_type"
        ) as mock_exec, patch(
            "shuffify.services.executors.base_executor"
            ".JobExecutorService._record_rollback"
        ) as mock_rb, patch(
            "shuffify.services.executors.base_executor"
            ".JobExecutorService._record_failure"
        ) as mock_fail:
            mock_get_api.return_value = _make_api()
            mock_exec.side_effect = RuntimeError("boom")

            JobExecutorService.execute(schedule.id)

            mock_fail.assert_called_once()
            mock_rb.assert_not_called()


class TestRestoreToPlaylistConvenience:
    """The new PlaylistSnapshotService.restore_to_playlist
    method (used by _record_rollback)."""

    def test_restore_to_playlist_applies_and_returns(
        self, user,
    ):
        api = _make_api()
        snap = _seed_snapshot(
            user.id, "p_restore",
            ["a", "b", "c"],
        )

        applied = (
            PlaylistSnapshotService.restore_to_playlist(
                snap.id, user.id, api,
            )
        )

        api.update_playlist_tracks.assert_called_once_with(
            "p_restore", ["a", "b", "c"],
        )
        assert applied.id == snap.id
        assert applied.playlist_id == "p_restore"
        assert applied.track_count == 3
