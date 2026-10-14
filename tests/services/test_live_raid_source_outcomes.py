"""Source failures retain healthy Raid effects under real runner invocations."""

from unittest.mock import patch

import pytest

from shuffify.enums import ActivityType, JobType, PendingRaidStatus
from shuffify.models.db import (
    ActivityLog,
    JobExecution,
    PendingRaidTrack,
    RaidPlaylistLink,
    Schedule,
    UpstreamSource,
    db,
)
from shuffify.services.executors import JobExecutorService
from shuffify.services.executors.raid_executor import execute_raid
from shuffify.services.source_resolver.base import ResolveResult
from shuffify.services.source_resolver.direct_api_pathway import DirectAPIPathway
from shuffify.services.user_service import UserService
from shuffify.spotify.exceptions import SpotifyAPIError
from tests.services.test_live_executor_outcomes import MutableSpotify, uri


class SourceFaultSpotify(MutableSpotify):
    def __init__(self, owner):
        super().__init__(owner, target=[uri(1)], collection=[], healthy=[uri(2)], dead=[uri(3)])
        self.failed_sources = {"dead"}
        self.source_reads = []

    def get_playlist_tracks(self, playlist_id, **kwargs):
        if playlist_id in {"healthy", "dead"}:
            self.source_reads.append(playlist_id)
        if playlist_id in self.failed_sources:
            raise SpotifyAPIError("private source transport details")
        return super().get_playlist_tracks(playlist_id, **kwargs)


@pytest.fixture
def raid_sources(db_app, monkeypatch):
    owner = UserService.upsert_from_spotify({"id": "source-owner", "display_name": "Owner", "images": []}).user
    routine = Schedule(
        user_id=owner.id, target_playlist_id="target", target_playlist_name="Target",
        job_type=JobType.RAID, schedule_type="interval", schedule_value="daily",
        is_enabled=True, source_playlist_ids=[],
        algorithm_name="BasicShuffle", algorithm_params={},
    )
    # Put the dead source first: collecting healthy contributions must not
    # depend on the order in which configured sources are resolved.
    sources = [UpstreamSource(
        user_id=owner.id, target_playlist_id="target", source_playlist_id=name,
        source_name=f"Source {name}", source_type="own", raid_count=5,
    ) for name in ("dead", "healthy")]
    db.session.add_all([routine, *sources])
    db.session.add(RaidPlaylistLink(
        user_id=owner.id, target_playlist_id="target", target_playlist_name="Target",
        raid_playlist_id="collection", raid_playlist_name="Collection", drip_enabled=True,
    ))
    db.session.commit()
    api = SourceFaultSpotify(owner)
    monkeypatch.setattr(JobExecutorService, "_get_spotify_api", lambda _owner: api)
    monkeypatch.setattr(
        "shuffify.services.source_resolver.SourceResolver._default_pathways",
        staticmethod(lambda: [DirectAPIPathway()]),
    )
    monkeypatch.setattr(
        "shuffify.services.playlist_snapshot_service.PlaylistSnapshotService.auto_snapshot_if_enabled",
        lambda **kwargs: None,
    )
    return routine, api, sources


def run_routine(routine, entrypoint):
    if entrypoint == "manual":
        return JobExecutorService.execute_now(routine.id, routine.user_id)
    captured = []
    runner = JobExecutorService._run_job

    def record_result(*args, **kwargs):
        result = runner(*args, **kwargs)
        captured.append(result)
        return result

    with patch.object(JobExecutorService, "_run_job", side_effect=record_result):
        assert JobExecutorService.execute(routine.id) is None
    assert len(captured) == 1
    return captured[0]


class TestMixedRaidSourceOutcomes:
    @pytest.mark.parametrize("entrypoint", ["manual", "scheduled"])
    @pytest.mark.parametrize("job_type", [JobType.RAID, JobType.RAID_AND_DRIP, JobType.RAID_AND_SHUFFLE])
    def test_healthy_sources_collect_before_partial_stops_dependents(self, raid_sources, entrypoint, job_type):
        routine, api, sources = raid_sources
        routine.job_type = job_type
        db.session.commit()
        with patch("shuffify.services.executors.drip_executor.execute_drip") as drip, patch(
            "shuffify.services.executors.shuffle_executor.execute_shuffle",
        ) as shuffle:
            result = run_routine(routine, entrypoint)
        drip.assert_not_called()
        shuffle.assert_not_called()
        assert api.source_reads == ["dead", "healthy"]
        assert result["status"] == "failed"
        assert result["tracks_added"] == 1
        assert result["tracks_total"] == 1
        assert result["execution_state"] == "recorded"
        assert db.session.get(JobExecution, result["execution_id"]).status == "failed"
        operation = result["operation"]
        assert operation["aggregate_status"] == "partial"
        assert operation["retryable"] is False
        assert operation["error_code"] == "source_resolution_failed"
        failure = next(b for b in operation["bookkeeping"] if b["kind"] == "source_resolution")
        assert failure["status"] == "failed"
        assert failure["failed_sources"] == [{
            "source_id": sources[0].id, "source_playlist_id": "dead",
            "status": "failed", "error_code": "source_resolution_failed",
        }]
        staged = next(b for b in operation["bookkeeping"] if b["kind"] == "pending_stage")
        assert staged["status"] == "complete"
        assert staged["affected_count"] == 1
        assert operation["resources"][0]["status"] == "complete"
        assert api.playlists["collection"] == [uri(2)]
        assert api.playlists["target"] == [uri(1)]
        assert api.writes == [("add", "collection", [uri(2)])]
        pending = PendingRaidTrack.query.one()
        assert pending.track_uri == uri(2)
        assert pending.source_playlist_id == "healthy"
        assert pending.status == PendingRaidStatus.PENDING
        for source, status in zip(sources, ("failed", "success")):
            db.session.refresh(source)
            assert source.last_resolve_status == status
            assert source.last_resolved_at is not None
        activity = ActivityLog.query.filter_by(activity_type=ActivityType.RAID_SOURCE_RESOLVE_FAILED).one()
        assert activity.metadata_json["source_id"] == sources[0].id
        if job_type != JobType.RAID:
            first, dependent = operation["steps"]
            assert first["step"] == "raid"
            assert first["status"] == "partial"
            assert dependent == {
                "step": "drip" if job_type == JobType.RAID_AND_DRIP else "shuffle", "status": "not_started",
            }
        assert "private source transport details" not in str(result)

    def test_no_collection_link_keeps_healthy_database_staging(self, raid_sources):
        routine, api, _sources = raid_sources
        db.session.delete(RaidPlaylistLink.query.one())
        db.session.commit()
        result = run_routine(routine, "scheduled")
        assert result["operation"]["aggregate_status"] == "partial"
        assert result["tracks_added"] == 1
        assert PendingRaidTrack.query.one().track_uri == uri(2)
        assert result["operation"]["resources"] == []
        assert api.writes == []

    def test_all_failed_is_known_partial_with_tracking_and_no_writes(self, raid_sources):
        routine, api, sources = raid_sources
        api.failed_sources.add("healthy")
        result = run_routine(routine, "scheduled")
        assert result["status"] == "failed"
        assert result["tracks_added"] == 0
        assert result["tracks_total"] == 1
        assert result["operation"]["aggregate_status"] == "partial"
        assert result["operation"]["resources"] == []
        failure = next(b for b in result["operation"]["bookkeeping"] if b["kind"] == "source_resolution")
        assert {source["source_id"] for source in failure["failed_sources"]} == {source.id for source in sources}
        for source in sources:
            db.session.refresh(source)
            assert source.last_resolve_status == "failed"
            assert source.last_resolved_at is not None
        assert ActivityLog.query.filter_by(activity_type=ActivityType.RAID_SOURCE_RESOLVE_FAILED).count() == 2
        assert PendingRaidTrack.query.count() == 0
        assert api.writes == []

    def test_legacy_direct_caller_still_collects_healthy_sources(self, raid_sources):
        routine, api, sources = raid_sources
        result = execute_raid(routine, api)
        assert result == {"tracks_added": 1, "tracks_total": 1}
        assert api.playlists["collection"] == [uri(2)]
        assert PendingRaidTrack.query.one().track_uri == uri(2)
        db.session.refresh(sources[0])
        assert sources[0].last_resolve_status == "failed"
        assert ActivityLog.query.filter_by(activity_type=ActivityType.RAID_SOURCE_RESOLVE_FAILED).count() == 1

    def test_partial_source_keeps_usable_tracks_and_partial_tracking(self, raid_sources, monkeypatch):
        routine, api, sources = raid_sources

        class PartialPathway(DirectAPIPathway):
            def resolve(self, source, api=None):
                if source.source_playlist_id == "dead":
                    return ResolveResult([uri(3)], "partial_test", success=False, partial=True)
                return super().resolve(source, api)

        monkeypatch.setattr(
            "shuffify.services.source_resolver.SourceResolver._default_pathways",
            staticmethod(lambda: [PartialPathway()]),
        )
        result = run_routine(routine, "manual")
        assert result["operation"]["aggregate_status"] == "partial"
        assert result["tracks_added"] == 2
        assert api.playlists["collection"] == [uri(3), uri(2)]
        assert {track.track_uri for track in PendingRaidTrack.query.all()} == {uri(2), uri(3)}
        failure = next(b for b in result["operation"]["bookkeeping"] if b["kind"] == "source_resolution")
        assert failure["failed_sources"][0]["error_code"] == "source_resolution_incomplete"
        db.session.refresh(sources[0])
        assert sources[0].last_resolve_status == "partial"
        assert sources[0].last_resolve_pathway == "partial_test"
