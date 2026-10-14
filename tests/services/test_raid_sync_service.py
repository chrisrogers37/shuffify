"""
Tests for RaidSyncService.

Tests cover watch/unwatch operations, raid status retrieval,
schedule management, and the _find_raid_schedule helper.
"""

from unittest.mock import patch

import pytest

from shuffify.enums import JobType
from shuffify.models.db import Schedule, UpstreamSource, db
from shuffify.services.raid_sync_service import (
    RaidSyncError,
    RaidSyncService,
)
from shuffify.services.scheduler_service import (
    SchedulerService,
)
from shuffify.services.upstream_source_service import (
    UpstreamSourceNotFoundError,
    UpstreamSourceService,
)
from shuffify.services.user_service import UserService


@pytest.fixture
def user(db_app):
    """Provide a test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield result.user


# =============================================================================
# Watch Playlist
# =============================================================================


class TestWatchPlaylist:
    """Tests for RaidSyncService.watch_playlist."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_creates_source_and_schedule(
        self, mock_add_job, user
    ):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
            source_playlist_name="Source One",
        )
        assert result["source"] is not None
        assert result["schedule"] is not None
        assert result["source"]["source_playlist_id"] == "source1"

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_no_schedule(self, mock_add_job, user):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
            auto_schedule=False,
        )
        assert result["source"] is not None
        assert result["schedule"] is None

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_second_source_appends(
        self, mock_add_job, user
    ):
        RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
        )
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source2",
        )
        schedule = result["schedule"]
        assert "source1" in schedule["source_playlist_ids"]
        assert "source2" in schedule["source_playlist_ids"]

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_same_source_idempotent(
        self, mock_add_job, user
    ):
        RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
        )
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
        )
        schedule = result["schedule"]
        ids = schedule["source_playlist_ids"]
        assert ids.count("source1") == 1

    def test_watch_invalid_user_raises(self, db_app):
        with db_app.app_context():
            with pytest.raises(RaidSyncError, match="User not found"):
                RaidSyncService.watch_playlist(
                    spotify_id="nonexistent",
                    target_playlist_id="t1",
                    target_playlist_name="T",
                    source_playlist_id="s1",
                )

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_custom_schedule_value(
        self, mock_add_job, user
    ):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
            schedule_value="weekly",
        )
        assert (
            result["schedule"]["schedule_value"] == "weekly"
        )

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_returns_expected_keys(
        self, mock_add_job, user
    ):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
        )
        assert "source" in result
        assert "schedule" in result


# =============================================================================
# Unwatch Playlist
# =============================================================================


class TestUnwatchPlaylist:
    """Tests for RaidSyncService.unwatch_playlist."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_unwatch_removes_source(
        self, mock_add_job, user
    ):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="T",
            source_playlist_id="source1",
        )
        source_id = result["source"]["id"]

        RaidSyncService.unwatch_playlist(
            spotify_id="user123",
            source_id=source_id,
            target_playlist_id="target1",
        )

        sources = UpstreamSourceService.list_sources(
            "user123", "target1"
        )
        assert len(sources) == 0

    @patch("shuffify.scheduler.add_job_for_schedule")
    @patch("shuffify.scheduler.remove_job_for_schedule")
    def test_unwatch_last_source_deletes_schedule(
        self, mock_remove, mock_add, user
    ):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="T",
            source_playlist_id="source1",
        )
        source_id = result["source"]["id"]

        RaidSyncService.unwatch_playlist(
            spotify_id="user123",
            source_id=source_id,
            target_playlist_id="target1",
        )

        schedule = RaidSyncService._find_raid_schedule(
            user.id, "target1"
        )
        assert schedule is None

    def test_unwatch_nonexistent_raises(self, user):
        with pytest.raises(UpstreamSourceNotFoundError):
            RaidSyncService.unwatch_playlist(
                spotify_id="user123",
                source_id=9999,
                target_playlist_id="target1",
            )

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_unwatch_updates_remaining(
        self, mock_add_job, user
    ):
        r1 = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="T",
            source_playlist_id="source1",
        )
        RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="T",
            source_playlist_id="source2",
        )

        RaidSyncService.unwatch_playlist(
            spotify_id="user123",
            source_id=r1["source"]["id"],
            target_playlist_id="target1",
        )

        schedule = RaidSyncService._find_raid_schedule(
            user.id, "target1"
        )
        assert schedule is not None
        assert "source2" in schedule.source_playlist_ids
        assert "source1" not in schedule.source_playlist_ids


# =============================================================================
# Get Raid Status
# =============================================================================


class TestGetRaidStatus:
    """Tests for RaidSyncService.get_raid_status."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_status_with_sources_and_schedule(
        self, mock_add_job, user
    ):
        RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="T",
            source_playlist_id="source1",
        )

        status = RaidSyncService.get_raid_status(
            "user123", "target1"
        )
        assert status["source_count"] == 1
        assert status["has_schedule"] is True
        assert status["is_schedule_enabled"] is True
        assert len(status["sources"]) == 1

    def test_status_empty(self, user):
        status = RaidSyncService.get_raid_status(
            "user123", "target1"
        )
        assert status["source_count"] == 0
        assert status["has_schedule"] is False
        assert status["sources"] == []

    def test_status_unknown_user(self, db_app):
        with db_app.app_context():
            status = RaidSyncService.get_raid_status(
                "nonexistent", "target1"
            )
            assert status["source_count"] == 0
            assert status["has_schedule"] is False


# =============================================================================
# Raid Now
# =============================================================================


class TestRaidNow:
    """Tests for RaidSyncService.raid_now."""

    def test_raid_now_no_sources_raises(self, user):
        with pytest.raises(
            RaidSyncError,
            match="No sources configured"
        ):
            RaidSyncService.raid_now(
                "user123", "target1"
            )

    @pytest.mark.parametrize("job_type", [
        JobType.RAID, JobType.RAID_AND_SHUFFLE, JobType.RAID_AND_DRIP,
    ])
    @pytest.mark.parametrize("enabled", [True, False])
    def test_collect_uses_transient_raid_and_preserves_outcome(
        self, user, job_type, enabled,
    ):
        from shuffify.models.db import Schedule, db
        from shuffify.services.executors import JobExecutorService

        saved = Schedule(
            user_id=user.id, job_type=job_type,
            target_playlist_id="target1", target_playlist_name="Target",
            schedule_type="interval", schedule_value="daily",
            source_playlist_ids=["saved-source"], is_enabled=enabled,
        )
        db.session.add(saved)
        db.session.commit()
        outcome = {
            "tracks_added": None, "tracks_total": 20, "status": "failed",
            "execution_id": 42, "started_at": "2026-10-11T12:00:00+00:00",
            "execution_state": "recorded",
            "operation": {"aggregate_status": "unknown", "kind": "raid",
                          "resources": [{"resource": "collection",
                                         "status": "unknown"}]},
        }
        with patch.object(JobExecutorService, "_run_job", return_value=outcome) as run, patch.object(
            JobExecutorService, "execute_now",
        ) as saved_run, patch("shuffify.scheduler.add_job_for_schedule") as register:
            result = RaidSyncService.raid_now(
                "user123", "target1", source_playlist_ids=["extra-source"],
            )
        assert result is outcome
        saved_run.assert_not_called()
        register.assert_not_called()
        run.assert_called_once()
        transient, schedule_id = run.call_args.args
        assert schedule_id is None
        assert transient.job_type == JobType.RAID
        assert transient.source_playlist_ids == ["extra-source"]
        assert transient.id is None
        db.session.refresh(saved)
        assert saved.is_enabled is enabled
        assert saved.source_playlist_ids == ["saved-source"]
        assert Schedule.query.count() == 1

    def test_collect_preserves_observed_zero_counts(self, user):
        outcome = {
            "tracks_added": 0, "tracks_total": 50, "status": "success",
            "execution_id": 43, "execution_state": "recorded",
        }
        with patch(
            "shuffify.services.executors.JobExecutorService.execute_raid_for_user",
            return_value=outcome,
        ):
            result = RaidSyncService.raid_now(
                "user123", "target2", source_playlist_ids=["s1"],
            )
        assert result is outcome
        assert result["tracks_added"] == 0
        assert result["tracks_total"] == 50

    def test_raid_now_search_only_does_not_pass_none_source_ids_inline(
        self, user
    ):
        """A search-only target (no playlist sources, no schedule) must be
        raidable through the executor without passing None placeholders as
        source ids. The no-schedule path runs through
        JobExecutorService.execute_raid_for_user."""
        UpstreamSourceService.add_search_source(
            spotify_id="user123",
            target_playlist_id="tgt_inline",
            search_query="chillhop",
            source_name="Chillhop",
        )

        with patch(
            "shuffify.services.executors."
            "JobExecutorService.execute_raid_for_user",
            return_value={
                "tracks_added": 0,
                "tracks_total": 0,
                "status": "success",
            },
        ) as mock_exec:
            RaidSyncService.raid_now("user123", "tgt_inline")

        mock_exec.assert_called_once()
        passed_ids = mock_exec.call_args.kwargs["source_playlist_ids"]
        assert None not in passed_ids


class TestWatchSearchQuery:
    """Tests for RaidSyncService.watch_search_query."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_registers_apscheduler_job_on_new_schedule(
        self, mock_add_job, user
    ):
        """Creating a raid schedule for a search query must register it with
        APScheduler immediately, not wait for an app restart."""
        RaidSyncService.watch_search_query(
            spotify_id="user123",
            target_playlist_id="target_sq",
            target_playlist_name="Target SQ",
            search_query="deep house",
        )

        mock_add_job.assert_called_once()


class TestWatchTransactions:
    """A failed schedule write must not leave a changed source set behind."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_create_failure_rolls_back_source(self, _job, user):
        with patch.object(SchedulerService, "create_schedule", side_effect=RuntimeError("write failed")):
            with pytest.raises(RuntimeError, match="write failed"):
                RaidSyncService.watch_playlist(
                    "user123", "target_atomic", "Target", "source_new",
                )

        assert UpstreamSource.query.filter_by(user_id=user.id, target_playlist_id="target_atomic").count() == 0
        assert Schedule.query.filter_by(user_id=user.id, target_playlist_id="target_atomic").count() == 0

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_existing_schedule_failure_preserves_source_set(self, _job, user):
        first = RaidSyncService.watch_playlist(
            "user123", "target_atomic", "Target", "source_old",
        )
        with patch.object(SchedulerService, "update_schedule", side_effect=RuntimeError("write failed")):
            with pytest.raises(RuntimeError, match="write failed"):
                RaidSyncService.watch_playlist(
                    "user123", "target_atomic", "Target", "source_new",
                )

        db.session.expire_all()
        sources = UpstreamSource.query.filter_by(user_id=user.id, target_playlist_id="target_atomic").all()
        assert [source.source_playlist_id for source in sources] == ["source_old"]
        schedule = db.session.get(Schedule, first["schedule"]["id"])
        assert schedule.source_playlist_ids == ["source_old"]

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_unwatch_schedule_failure_preserves_source(self, _job, user):
        RaidSyncService.watch_playlist("user123", "target_atomic", "Target", "source_old")
        second = RaidSyncService.watch_playlist("user123", "target_atomic", "Target", "source_new")
        with patch.object(SchedulerService, "update_schedule", side_effect=RuntimeError("write failed")):
            with pytest.raises(RuntimeError, match="write failed"):
                RaidSyncService.unwatch_playlist(
                    "user123", second["source"]["id"], "target_atomic",
                )

        db.session.expire_all()
        sources = UpstreamSource.query.filter_by(user_id=user.id, target_playlist_id="target_atomic").all()
        assert {source.source_playlist_id for source in sources} == {"source_old", "source_new"}
        schedule = RaidSyncService._find_raid_schedule(user.id, "target_atomic")
        assert schedule.source_playlist_ids == ["source_old", "source_new"]

    def test_search_watch_failure_rolls_back_source(self, user):
        with patch.object(SchedulerService, "create_schedule", side_effect=RuntimeError("write failed")):
            with pytest.raises(RuntimeError, match="write failed"):
                RaidSyncService.watch_search_query(
                    "user123", "target_atomic", "Target", "deep house",
                )

        assert UpstreamSource.query.filter_by(user_id=user.id, target_playlist_id="target_atomic").count() == 0


class TestDirectDripNow:
    @pytest.mark.parametrize(("saved_kind", "enabled"), [
        (None, False), (JobType.DRIP, False), (JobType.DRIP, True),
        (JobType.RAID_AND_DRIP, False), (JobType.RAID_AND_DRIP, True),
    ])
    def test_direct_drip_is_transient_and_leaves_saved_routine_untouched(self, user, saved_kind, enabled):
        from shuffify.models.db import JobExecution, RaidPlaylistLink, Schedule, db
        from shuffify.services.executors import JobExecutorService
        from tests.services.test_live_executor_outcomes import MutableSpotify, uri

        link = RaidPlaylistLink(
            user_id=user.id, target_playlist_id="target", target_playlist_name="Target",
            raid_playlist_id="collection", raid_playlist_name="Collection",
            drip_enabled=False, drip_count=2,
        )
        db.session.add(link)
        routine = None
        if saved_kind:
            routine = Schedule(
                user_id=user.id, job_type=saved_kind, target_playlist_id="target", target_playlist_name="Target",
                schedule_type="interval", schedule_value="daily", is_enabled=enabled,
                algorithm_params={"drip_count": 1}, last_status="success",
            )
            db.session.add(routine)
        db.session.commit()
        if routine:
            db.session.add(JobExecution(schedule_id=routine.id, status="success", tracks_added=77))
            db.session.commit()
        api = MutableSpotify(user, target=[uri(1)], collection=[uri(2), uri(3), uri(4)])
        with patch.object(JobExecutorService, "_get_spotify_api", return_value=api), patch.object(
            RaidSyncService, "_find_drip_schedule", side_effect=AssertionError("Direct Drip must not select a routine"),
        ), patch("shuffify.scheduler.add_job_for_schedule") as register, patch(
            "shuffify.services.playlist_snapshot_service.PlaylistSnapshotService.auto_snapshot_if_enabled",
        ):
            result = RaidSyncService.drip_now("user123", "target", user=user)
        assert result["status"] == "success"
        assert result["operation"]["aggregate_status"] == "complete"
        assert result["tracks_added"] == 2  # explicit link count, not saved routine's count
        execution = db.session.get(JobExecution, result["execution_id"])
        assert execution.schedule_id is None
        assert result["execution_state"] == "recorded"
        assert result["started_at"] == execution.started_at.isoformat()
        assert len(api.playlists["target"]) == 3
        assert api.playlists["target"][-1] == uri(1)
        assert len(api.playlists["collection"]) == 1
        assert [write[:2] for write in api.writes] == [("add", "target"), ("remove", "collection")]
        db.session.refresh(link)
        assert link.drip_enabled is False
        assert link.drip_count == 2
        if routine:
            db.session.refresh(routine)
            assert routine.is_enabled is enabled
            assert routine.algorithm_params == {"drip_count": 1}
            assert routine.last_run_at is None
            assert routine.last_status == "success"
            history = JobExecution.query.filter_by(schedule_id=routine.id).all()
            assert len(history) == 1
            assert history[0].tracks_added == 77
        assert Schedule.query.count() == (1 if routine else 0)
        register.assert_not_called()


# =============================================================================
# _find_raid_schedule
# =============================================================================


class TestFindRaidSchedule:
    """Tests for RaidSyncService._find_raid_schedule."""

    def test_finds_raid_schedule(self, user):
        SchedulerService.create_schedule(
            user_id=user.id,
            job_type=JobType.RAID,
            target_playlist_id="target1",
            target_playlist_name="T",
            schedule_type="interval",
            schedule_value="daily",
            source_playlist_ids=["s1"],
        )
        result = RaidSyncService._find_raid_schedule(
            user.id, "target1"
        )
        assert result is not None
        assert result.job_type == JobType.RAID

    def test_returns_none_when_no_schedule(self, user):
        result = RaidSyncService._find_raid_schedule(
            user.id, "nonexistent"
        )
        assert result is None
