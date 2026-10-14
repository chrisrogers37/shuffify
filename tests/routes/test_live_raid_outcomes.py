"""HTTP outcomes from real Raid/Drip execution against mutable local state."""

from unittest.mock import patch

import pytest

from shuffify.enums import JobType, PendingRaidStatus
from shuffify.models.db import (
    ActivityLog,
    JobExecution,
    PendingRaidTrack,
    Schedule,
    db,
)
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyWriteRejectedError,
)
from tests.routes.live_review_helpers import serving


def uri(number):
    return "spotify:track:" + str(number).zfill(22)


class MutableSpotify:
    """Writes change real fake state before any injected transport failure."""

    def __init__(self):
        self.playlists = {
            "target": [uri(1)], "collection": [],
            "persisted": [uri(2)], "extra": [uri(3)],
        }
        self.writes = []
        self.reject_target = False
        self.lose_collection_add = False
        self.lose_collection_remove = False
        self.fail_read_after_remove = False
        self.removed = False

    @staticmethod
    def track(track_uri):
        return {"id": track_uri.rsplit(":", 1)[1], "uri": track_uri,
                "name": track_uri, "artists": [{"name": "Artist"}],
                "album": {"name": "Album", "images": []}, "type": "track"}

    def get_playlist(self, playlist_id, **_kwargs):
        return {"id": playlist_id, "name": f"Fresh {playlist_id}",
                "owner": {"id": "user123"}, "collaborative": False,
                "snapshot_id": repr(self.playlists[playlist_id]),
                "tracks": {"total": len(self.playlists[playlist_id])}}

    def get_playlist_tracks(self, playlist_id, **_kwargs):
        return [self.track(item) for item in self.playlists[playlist_id]]

    def get_playlist_items_raw(self, playlist_id, limit=50, offset=0):
        if self.fail_read_after_remove and self.removed and playlist_id == "collection":
            raise SpotifyAPIError("private verification detail")
        tracks = self.get_playlist_tracks(playlist_id)
        end = offset + limit
        return {"items": [{"track": t} for t in tracks[offset:end]],
                "total": len(tracks), "offset": offset,
                "next": f"http://local/items?offset={end}" if end < len(tracks) else None}

    def get_tracks(self, uris):
        return [self.track(item) for item in uris]

    def playlist_add_items(self, playlist_id, uris, position=None):
        self.writes.append(("add", playlist_id, list(uris)))
        if playlist_id == "target" and self.reject_target:
            raise SpotifyWriteRejectedError("private rejected detail")
        index = len(self.playlists[playlist_id]) if position is None else position
        self.playlists[playlist_id][index:index] = list(uris)
        if playlist_id == "collection" and self.lose_collection_add:
            raise SpotifyAPIError("private lost add response")

    def playlist_remove_items(self, playlist_id, uris):
        self.writes.append(("remove", playlist_id, list(uris)))
        self.playlists[playlist_id] = [u for u in self.playlists[playlist_id] if u not in uris]
        self.removed = True
        if self.lose_collection_remove:
            raise SpotifyAPIError("private lost remove response")

    def update_playlist_tracks(self, playlist_id, uris):
        self.writes.append(("replace", playlist_id, list(uris)))
        self.playlists[playlist_id] = list(uris)
        return True


@pytest.fixture
def live_raid(raid_target):
    api = MutableSpotify()
    with serving(api):
        yield raid_target.client, api, raid_target.user, raid_target.link


def test_collect_keeps_saved_composite_paused_and_target_untouched(live_raid):
    client, api, user, _link = live_raid
    routine = Schedule(
        user_id=user.id, target_playlist_id="target", target_playlist_name="Target",
        job_type=JobType.RAID_AND_DRIP, is_enabled=False,
        schedule_type="interval", schedule_value="daily", source_playlist_ids=["persisted"],
    )
    db.session.add(routine)
    db.session.commit()
    with patch("shuffify.scheduler.add_job_for_schedule") as register:
        response = client.post("/playlist/target/raid-now", json={"source_playlist_ids": ["extra"]})
    data = response.get_json()
    assert response.status_code == 200, data
    assert data["success"] is True
    assert data["tracks_added"] == 2  # persisted sources plus supplied extras
    assert api.playlists["target"] == [uri(1)]
    assert set(api.playlists["collection"]) == {uri(2), uri(3)}
    assert {playlist for _method, playlist, _uris in api.writes} == {"collection"}
    row = db.session.get(JobExecution, data["execution_id"])
    assert row.schedule_id is None
    assert data["started_at"] == row.started_at.isoformat()
    assert data["operation"]["kind"] == "raid"
    assert data["operation"]["resources"][0]["resource_name"] == "Fresh collection"
    db.session.refresh(routine)
    assert routine.is_enabled is False
    assert routine.last_run_at is None
    register.assert_not_called()
    assert ActivityLog.query.count() == 1


def test_combined_retains_collection_when_drip_first_write_rejected(live_raid):
    client, api, user, link = live_raid
    # An existing enabled composite must not add an undeclared extra Drip.
    db.session.add(Schedule(
        user_id=user.id, target_playlist_id="target", target_playlist_name="Target",
        job_type=JobType.RAID_AND_DRIP, is_enabled=True,
        schedule_type="interval", schedule_value="daily", source_playlist_ids=["persisted"],
    ))
    db.session.commit()
    api.reject_target = True
    response = client.post("/playlist/target/raid-and-drip", json={})
    data = response.get_json()
    assert response.status_code == 502, data
    assert data["success"] is False
    assert data["tracks_raided"] == 1
    assert data["operation"]["aggregate_status"] == "partial"
    assert data["operation"]["retryable"] is False
    assert "execution_id" not in data  # no fabricated parent execution
    raid, drip = data["operation"]["steps"]
    assert raid["aggregate_status"] == "complete"
    assert drip["aggregate_status"] == "partial"
    assert raid["execution_id"] != drip["execution_id"]
    assert all(db.session.get(JobExecution, s["execution_id"]) for s in (raid, drip))
    assert api.playlists["target"] == [uri(1)]
    assert api.playlists["collection"] == [uri(2)]
    assert [item[:2] for item in api.writes] == [("add", "collection"), ("add", "target")]
    resources = {(r["step"], r["resource"]): r for r in data["operation"]["resources"]}
    assert resources[("raid", "collection")]["status"] == "complete"
    assert resources[("drip", "target")]["status"] == "failed"
    assert PendingRaidTrack.query.one().status == PendingRaidStatus.PENDING
    db.session.refresh(link)
    assert link.drip_enabled is False
    assert "private" not in response.get_data(as_text=True)


def test_collection_db_failure_keeps_verified_spotify_effect(live_raid):
    client, api, _user, _link = live_raid
    with patch(
        "shuffify.services.pending_raid_service.PendingRaidService.stage_tracks",
        side_effect=RuntimeError("private staging detail"),
    ):
        response = client.post("/playlist/target/raid-now", json={})
    data = response.get_json()
    assert response.status_code == 502, data
    assert data["success"] is False
    assert data["operation"]["aggregate_status"] == "partial"
    assert data["operation"]["retryable"] is False
    resource = data["operation"]["resources"][0]
    assert resource["status"] == "complete"
    assert resource["current_track_uris"] == [uri(2)]
    staging = next(b for b in data["operation"]["bookkeeping"] if b["kind"] == "pending_stage")
    assert staging["status"] == "failed"
    assert api.playlists["target"] == [uri(1)]
    assert api.playlists["collection"] == [uri(2)]
    assert len(api.writes) == 1
    assert PendingRaidTrack.query.count() == 0
    assert "private" not in response.get_data(as_text=True)


@pytest.mark.parametrize("failure", ["lose_collection_remove", "fail_read_after_remove"])
def test_drip_retains_target_success_and_unknown_collection(live_raid, failure):
    client, api, _user, _link = live_raid
    api.playlists["collection"] = [uri(2)]
    setattr(api, failure, True)
    response = client.post("/playlist/target/drip-now")  # legacy empty body
    data = response.get_json()
    assert response.status_code == 502, data
    assert data["success"] is False
    assert data["operation"]["aggregate_status"] == "unknown"
    assert data["operation"]["retryable"] is False
    by_id = {r["resource"]: r for r in data["operation"]["resources"]}
    assert by_id["target"]["status"] == "complete"
    assert by_id["collection"]["status"] == "unknown"
    assert by_id["target"]["desired_track_uris"] == [uri(2), uri(1)]
    assert data["execution_id"] is not None
    assert api.playlists["target"] == [uri(2), uri(1)]
    assert api.playlists["collection"] == []
    assert len(api.writes) == 2
    assert "private" not in response.get_data(as_text=True)


def test_combined_unknown_collection_does_not_dispatch_drip(live_raid):
    client, api, _user, _link = live_raid
    api.lose_collection_add = True
    response = client.post("/playlist/target/raid-and-drip")
    data = response.get_json()
    assert response.status_code == 502, data
    assert data["operation"]["aggregate_status"] == "unknown"
    raid, drip = data["operation"]["steps"]
    assert raid["execution_id"] is not None
    assert drip["execution_id"] is None
    assert drip["execution_state"] == "not_started"
    assert drip["status"] == "skipped"
    assert drip["tracks_added"] == 0
    # The skipped Drip promoted nothing; its inbox fact says so instead of going missing.
    promotion = [b for b in data["operation"]["bookkeeping"] if b["kind"] == "pending_promotion"]
    assert [(b["step"], b["status"]) for b in promotion] == [("drip", "not_required")]
    assert JobExecution.query.count() == 1
    assert api.playlists["target"] == [uri(1)]
    assert api.playlists["collection"] == [uri(2)]
    assert len(api.writes) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_direct_drip_runs_once_without_changing_saved_routine(live_raid, enabled):
    client, api, user, link = live_raid
    api.playlists["collection"] = [uri(2)]
    routine = Schedule(
        user_id=user.id, target_playlist_id="target", target_playlist_name="Target",
        job_type=JobType.DRIP, is_enabled=enabled,
        schedule_type="interval", schedule_value="daily", algorithm_params={"drip_count": 1},
    )
    db.session.add(routine)
    db.session.commit()
    with patch("shuffify.scheduler.add_job_for_schedule") as register:
        response = client.post("/playlist/target/drip-now", json={})
    data = response.get_json()
    assert response.status_code == 200, data
    assert data["tracks_added"] == 1
    assert JobExecution.query.count() == 1
    assert db.session.get(JobExecution, data["execution_id"]).schedule_id is None
    assert JobExecution.query.filter_by(schedule_id=routine.id).count() == 0
    db.session.refresh(routine)
    db.session.refresh(link)
    assert routine.is_enabled is enabled
    assert routine.last_run_at is None
    assert routine.last_status is None
    assert routine.algorithm_params == {"drip_count": 1}
    assert link.drip_enabled is False
    register.assert_not_called()


def test_schedule_run_returns_exact_failed_child_in_legacy_envelope(live_raid):
    client, api, user, link = live_raid
    from datetime import datetime, timedelta, timezone

    api.playlists["collection"] = [uri(2)]
    api.lose_collection_remove = True
    # A saved routine honors this gate; the test intentionally runs Drip.
    link.drip_enabled = True
    routine = Schedule(
        user_id=user.id, target_playlist_id="target", target_playlist_name="Target",
        job_type=JobType.DRIP, is_enabled=False,
        schedule_type="interval", schedule_value="daily", algorithm_params={"drip_count": 1},
    )
    db.session.add(routine)
    db.session.commit()
    # This unrelated row sorts later than our current invocation. A route must
    # forward the runner's identity rather than query latest history.
    unrelated = JobExecution(
        schedule_id=routine.id, status="success", tracks_added=99,
        started_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.session.add(unrelated)
    db.session.commit()
    response = client.post(f"/schedules/{routine.id}/run")
    data = response.get_json()
    assert response.status_code == 502, data
    assert data["success"] is False
    assert data["operation"]["aggregate_status"] == "unknown"
    assert "operation" not in data["result"]
    assert data["result"]["execution_id"] != unrelated.id
    row = db.session.get(JobExecution, data["result"]["execution_id"])
    assert row.status == "failed"
    assert data["result"]["started_at"] == row.started_at.isoformat()
    assert len(api.writes) == 2
    assert routine.is_enabled is False


def test_schedule_missing_is_preflight_and_makes_no_writes(live_raid):
    client, api, _user, _link = live_raid
    response = client.post("/schedules/999/run")
    data = response.get_json()
    assert response.status_code == 404, data
    assert data["success"] is False
    assert data["operation"]["aggregate_status"] == "partial"
    assert data["result"]["execution_state"] == "not_started"
    assert data["result"]["execution_id"] is None
    assert JobExecution.query.count() == 0
    assert api.writes == []


@pytest.mark.parametrize("endpoint", ["raid-now", "raid-and-drip"])
@pytest.mark.parametrize("body", [[], {"source_playlist_ids": []}, {"source_playlist_ids": [""]}])
def test_invalid_legacy_body_is_structured_preflight_without_execution(live_raid, endpoint, body):
    client, api, _user, _link = live_raid
    response = client.post(f"/playlist/target/{endpoint}", json=body)
    data = response.get_json()
    assert response.status_code == 400
    assert data["success"] is False
    assert data["operation"]["aggregate_status"] == "partial"
    assert data["execution_state"] == "not_started"
    assert JobExecution.query.count() == 0
    assert api.writes == []


@pytest.mark.parametrize("status", ["failed", "failed_rolled_back", "skipped"])
@pytest.mark.parametrize("action", ["raid", "schedule", "combined"])
def test_contradictory_legacy_label_cannot_report_success_or_start_drip(
    live_raid, action, status,
):
    client, api, _user, _link = live_raid
    # Deliberately inconsistent producer, with no error field to warn the
    # adapter. It must retain facts but cannot turn the label into success.
    result = {
        "status": status, "execution_id": 91, "execution_state": "recorded",
        "tracks_added": 4, "tracks_total": 9,
        "operation": {"kind": "raid", "aggregate_status": "complete",
                      "resources": [], "bookkeeping": [], "retryable": False},
    }
    target = (
        "shuffify.services.executors.JobExecutorService.execute_now"
        if action == "schedule"
        else "shuffify.services.raid_sync_service.RaidSyncService.raid_now"
    )
    endpoint = {
        "raid": "/playlist/target/raid-now",
        "schedule": "/schedules/1/run",
        "combined": "/playlist/target/raid-and-drip",
    }[action]
    with patch(target, return_value=result), patch(
        "shuffify.services.raid_sync_service.RaidSyncService.drip_now",
    ) as drip:
        response = client.post(endpoint, json={})
    data = response.get_json()
    assert response.status_code == 502
    assert data["success"] is False
    assert data["operation"]["aggregate_status"] == "unknown"
    assert data["operation"]["retryable"] is False
    drip.assert_not_called()
    if action == "combined":
        assert data["tracks_raided"] == 4
        assert data["operation"]["steps"][0]["execution_id"] == 91
    else:
        summary = data["result"] if action == "schedule" else data
        assert summary["tracks_added"] == 4
        assert summary["execution_id"] == 91
    assert api.writes == []
