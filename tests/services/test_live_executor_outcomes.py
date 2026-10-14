"""Invocation-local facts exercised against a mutable, complete Spotify fake."""

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from shuffify.enums import IntervalValue, JobType, ScheduleType
from shuffify.models.db import JobExecution, Schedule, db
from shuffify.services.executors import JobExecutorService
from shuffify.services.user_service import UserService


@pytest.fixture
def owner(db_app):
    with db_app.app_context():
        yield UserService.upsert_from_spotify(
            {
                "id": "live-owner",
                "display_name": "Owner",
                "images": [],
            }
        ).user


@pytest.fixture
def routine(owner):
    row = Schedule(
        user_id=owner.id,
        job_type=JobType.SHUFFLE,
        target_playlist_id="target",
        target_playlist_name="Old name",
        algorithm_name="BasicShuffle",
        algorithm_params={},
        schedule_type=ScheduleType.INTERVAL,
        schedule_value=IntervalValue.DAILY,
        is_enabled=False,
    )
    db.session.add(row)
    db.session.commit()
    return row


@contextmanager
def unavailable_lock(*args, **kwargs):
    yield False


def test_paused_run_returns_this_invocation_without_latest_history(routine):
    sentinel = {"execution_id": 91, "status": "success", "tracks_total": 7}
    routine.last_status = "failed"
    with patch.object(JobExecutorService, "_run_job", return_value=sentinel) as run:
        result = JobExecutorService.execute_now(routine.id, routine.user_id)
    assert result == sentinel
    run.assert_called_once_with(routine, routine.id, triggered_by="manual", review=None)
    assert routine.is_enabled is False


def test_lock_skip_returns_its_own_record_identity(routine):
    with patch("shuffify.services.executors.base_executor.playlist_lock", unavailable_lock):
        result = JobExecutorService._run_job(routine, routine.id)
    row = db.session.get(JobExecution, result["execution_id"])
    assert row.status == "skipped"
    assert result["execution_state"] == "recorded"
    assert result["started_at"] == row.started_at.isoformat()
    assert result["operation"]["aggregate_status"] == "partial"


def test_failed_skip_record_has_explicit_not_started(routine):
    with patch("shuffify.services.executors.base_executor.playlist_lock", unavailable_lock), patch.object(
        db.session, "commit", side_effect=RuntimeError("private database detail")
    ):
        result = JobExecutorService._run_job(routine, routine.id)
    assert result["execution_id"] is None
    assert result["started_at"] is None
    assert result["execution_state"] == "not_started"
    assert "private database detail" not in str(result)


def uri(number):
    return f"spotify:track:{number:022d}"


class MutableSpotify:
    """Real reader pagination and mutating API behavior, including fault points."""

    def __init__(self, owner, **playlists):
        self.owner = owner.spotify_id
        self.playlists = {key: list(value) for key, value in playlists.items()}
        self.revisions = {key: 0 for key in playlists}
        self.writes = []
        self.faults = {}
        self.read_failure = None
        self.on_write = None

    def get_playlist(self, playlist_id, **kwargs):
        if self.read_failure:
            raise self.read_failure
        return {
            "id": playlist_id,
            "name": f"Fresh {playlist_id}",
            "owner": {"id": self.owner},
            "snapshot_id": str(self.revisions[playlist_id]),
            "items": {"total": len(self.playlists[playlist_id])},
        }

    def get_playlist_items_raw(self, playlist_id, limit=50, offset=0):
        tracks = self.playlists[playlist_id]
        return {
            "offset": offset,
            "total": len(tracks),
            "items": [
                {
                    "added_at": f"2026-10-{int(value.rsplit(':', 1)[1]) % 20 + 1:02d}T00:00:00Z",
                    "track": {"uri": value, "name": value, "artists": [{"name": "Artist"}], "type": "track"},
                }
                for value in tracks[offset : offset + limit]
            ],
            "next": f"https://fake.invalid/items?offset={offset + limit}" if offset + limit < len(tracks) else None,
        }

    def get_playlist_tracks(self, playlist_id, **kwargs):
        return [{"uri": u} for u in self.playlists[playlist_id]]

    def get_tracks(self, uris):
        return [{"uri": value} for value in uris]

    def _write(self, method, playlist_id, uris, position=None):
        self.writes.append((method, playlist_id, list(uris)))
        if self.on_write:
            self.on_write()
        fault = self.faults.get((method, playlist_id))
        if fault:
            return fault(self, playlist_id, uris)
        if method == "update":
            self.playlists[playlist_id] = list(uris)
        elif method == "remove":
            self.playlists[playlist_id] = [u for u in self.playlists[playlist_id] if u not in set(uris)]
        else:
            old = self.playlists[playlist_id]
            pos = len(old) if position is None else position
            self.playlists[playlist_id] = old[:pos] + list(uris) + old[pos:]
        self.revisions[playlist_id] += 1
        return True

    def update_playlist_tracks(self, playlist_id, uris):
        return self._write("update", playlist_id, uris)

    def playlist_add_items(self, playlist_id, uris, position=None):
        return self._write("add", playlist_id, uris, position)

    def playlist_remove_items(self, playlist_id, uris):
        return self._write("remove", playlist_id, uris)


@pytest.fixture
def run_live(monkeypatch):
    monkeypatch.setattr(
        "shuffify.services.playlist_snapshot_service.PlaylistSnapshotService.auto_snapshot_if_enabled",
        lambda **kwargs: None,
    )

    def run(routine, api):
        with patch.object(JobExecutorService, "_get_spotify_api", return_value=api):
            return JobExecutorService.execute_now(routine.id, routine.user_id)

    return run


@pytest.fixture
def reverse_shuffle(monkeypatch):
    from shuffify.shuffle_algorithms.registry import ShuffleRegistry

    class Reverse:
        def shuffle(self, tracks, **params):
            return [track["uri"] for track in reversed(tracks)]

    monkeypatch.setattr(ShuffleRegistry, "get_algorithm", lambda name: Reverse)


def test_real_paused_shuffle_reads_complete_order_and_returns_new_identity(owner, routine, run_live, reverse_shuffle):
    api = MutableSpotify(owner, target=[uri(i) for i in range(101)])
    old = JobExecution(schedule_id=routine.id, status="failed")
    db.session.add(old)
    db.session.commit()
    result = run_live(routine, api)
    assert result["status"] == "success"
    assert result["execution_id"] != old.id
    assert routine.is_enabled is False
    resource = result["operation"]["resources"][0]
    assert resource["resource_name"] == "Fresh target"
    assert resource["status"] == "complete"
    assert resource["desired_track_uris"] == [uri(i) for i in reversed(range(101))]
    assert resource["current_track_uris"] == resource["desired_track_uris"]
    assert len(api.writes) == 1


def test_status_commit_failure_keeps_verified_spotify_success(owner, routine, run_live, reverse_shuffle):
    api = MutableSpotify(owner, target=[uri(1), uri(2)])
    with patch.object(JobExecutorService, "_record_success", side_effect=RuntimeError("secret detail")):
        result = run_live(routine, api)
    assert result["operation"]["resources"][0]["status"] == "complete"
    assert result["tracks_total"] == 2
    assert result["execution_id"] is not None
    assert result["operation"]["retryable"] is False
    assert result["operation"]["bookkeeping"][0]["status"] == "unknown"
    assert "secret detail" not in str(result)
    assert len(api.writes) == 1


def test_lost_shuffle_response_keeps_actual_intent_without_replay(owner, routine, run_live, reverse_shuffle):
    api = MutableSpotify(owner, target=[uri(1), uri(2)])

    def lost(fake, playlist_id, uris):
        fake.playlists[playlist_id] = list(uris)
        raise TimeoutError("sensitive transport detail")

    api.faults[("update", "target")] = lost
    result = run_live(routine, api)
    effect = result["operation"]["resources"][0]
    assert effect["status"] == "unknown"
    assert effect["desired_track_uris"] == [uri(2), uri(1)]
    assert result["operation"]["retryable"] is False
    assert result["http_status"] == 502
    assert len(api.writes) == 1
    assert "sensitive transport detail" not in str(result)


def test_first_rejection_does_not_trigger_compensating_write(owner, routine, run_live, reverse_shuffle):
    from shuffify.spotify.exceptions import SpotifyPartialBatchError, SpotifyWriteRejectedError

    api = MutableSpotify(owner, target=[uri(1), uri(2)])

    def rejected(fake, playlist_id, uris):
        raise SpotifyPartialBatchError(playlist_id, "update", 0, 1, [], uris, SpotifyWriteRejectedError("rejected"))

    api.faults[("update", "target")] = rejected
    result = run_live(routine, api)
    assert result["operation"]["resources"][0]["status"] == "failed"
    assert result["status"] == "failed"
    assert len(api.writes) == 1
    assert api.playlists["target"] == [uri(1), uri(2)]


def test_partial_write_compensation_is_verified_under_same_lock(owner, routine, run_live, reverse_shuffle):
    from shuffify.spotify.exceptions import SpotifyAPIError, SpotifyPartialBatchError

    api = MutableSpotify(owner, target=[uri(1), uri(2)])
    lock_state = {"held": False}

    @contextmanager
    def held_lock(*args, **kwargs):
        lock_state["held"] = True
        yield True
        lock_state["held"] = False

    def partial(fake, playlist_id, uris):
        fake.playlists[playlist_id] = list(uris[:1])
        del fake.faults[("update", playlist_id)]
        raise SpotifyPartialBatchError(playlist_id, "update", 1, 2, uris[:1], uris[1:], SpotifyAPIError("lost"))

    api.faults[("update", "target")] = partial
    observed_lock = []
    api.on_write = lambda: observed_lock.append(lock_state["held"])
    with patch("shuffify.services.executors.base_executor.playlist_lock", held_lock):
        result = run_live(routine, api)
    assert result["status"] == "failed_rolled_back"
    assert result["operation"]["resources"][0]["status"] == "restored"
    assert result["operation"]["aggregate_status"] == "partial"
    assert api.playlists["target"] == [uri(1), uri(2)]
    assert observed_lock == [True, True]


def test_bad_compensation_never_claims_rolled_back(owner, routine, run_live, reverse_shuffle):
    from shuffify.spotify.exceptions import SpotifyAPIError, SpotifyPartialBatchError

    api = MutableSpotify(owner, target=[uri(1), uri(2)])

    def partial(fake, playlist_id, uris):
        fake.playlists[playlist_id] = [uri(99)]
        fake.faults[("update", playlist_id)] = lambda *args: True
        raise SpotifyPartialBatchError(playlist_id, "update", 1, 2, uris[:1], uris[1:], SpotifyAPIError("lost"))

    api.faults[("update", "target")] = partial
    result = run_live(routine, api)
    assert result["status"] == "failed"
    assert result["operation"]["resources"][0]["status"] == "unknown"
    assert result["operation"]["resources"][0]["error_code"] == "rollback_unverified"


def link_collection(owner, *, drip_enabled=True):
    from shuffify.services.raid_link_service import RaidLinkService

    return RaidLinkService.create_link(
        user_id=owner.id, target_playlist_id="target", raid_playlist_id="collection",
        drip_enabled=drip_enabled, drip_count=1
    )


def test_saved_manual_drip_honors_link_gate_without_persisting_settings(owner, routine, run_live):
    link = link_collection(owner, drip_enabled=False)
    routine.job_type = JobType.DRIP
    db.session.commit()
    api = MutableSpotify(owner, target=[uri(1)], collection=[uri(2)])
    result = run_live(routine, api)
    assert result["status"] == "skipped"
    assert result["http_status"] == 409
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["error_code"] == "drip_disabled"
    # The closed gate wrote nothing; its target entry says the step did not run.
    assert result["operation"]["resources"] == [{
        "resource": "target", "resource_name": "Fresh target", "step": "drip", "mutation_type": "track_membership",
        "status": "not_required", "dispatched": False, "affected_count": 0, "retryable": False,
        "error_code": None, "verification": None, "started": False,
    }]
    # The closed gate promoted nothing, and says so for a review's required inbox fact.
    promotion = next(b for b in result["operation"]["bookkeeping"] if b["kind"] == "pending_promotion")
    assert (promotion["status"], promotion["affected_count"]) == ("not_required", 0)
    assert result["operation"]["retryable"] is True
    assert result["tracks_added"] == 0
    assert result["tracks_total"] == 1
    assert result["execution_state"] == "recorded"
    row = db.session.get(JobExecution, result["execution_id"])
    assert row.status == routine.last_status == "skipped"
    assert row.started_at.isoformat() == result["started_at"]
    assert api.writes == []
    assert api.playlists == {"target": [uri(1)], "collection": [uri(2)]}
    assert link.drip_enabled is False
    assert routine.is_enabled is False
    assert routine.algorithm_params == {}


def test_direct_manual_drip_is_forced_without_persisting_settings(owner, routine, run_live):
    link = link_collection(owner, drip_enabled=False)
    api = MutableSpotify(owner, target=[uri(1)], collection=[uri(2)])
    with patch.object(JobExecutorService, "_get_spotify_api", return_value=api):
        result = JobExecutorService.execute_drip_for_user(owner.id, "target")
    assert result["status"] == "success"
    assert api.playlists == {"target": [uri(2), uri(1)], "collection": []}
    assert link.drip_enabled is False
    assert routine.is_enabled is False
    assert routine.algorithm_params == {}
    assert db.session.get(JobExecution, result["execution_id"]).schedule_id is None
    assert {r["status"] for r in result["operation"]["resources"]} == {"complete"}


def test_drip_collection_failure_retains_verified_target(owner, routine, run_live):
    link_collection(owner)
    routine.job_type = JobType.DRIP
    api = MutableSpotify(owner, target=[uri(1)], collection=[uri(2)])

    def lost(*args):
        raise TimeoutError("lost delete")

    api.faults[("remove", "collection")] = lost
    result = run_live(routine, api)
    effects = {r["resource"]: r for r in result["operation"]["resources"]}
    assert effects["target"]["status"] == "complete"
    assert effects["collection"]["status"] == "unknown"
    assert result["operation"]["aggregate_status"] == "unknown"
    assert len(api.writes) == 2


def test_noop_shuffle_has_no_mutation_activity(owner, routine, run_live):
    from shuffify.models.db import ActivityLog

    api = MutableSpotify(owner, target=[uri(1)])
    result = run_live(routine, api)
    assert result["status"] == "success"
    assert result["operation"]["resources"][0]["status"] == "not_required"
    assert api.writes == []
    assert ActivityLog.query.count() == 0


def test_newest_first_receives_added_at_from_complete_reader(owner, routine, run_live):
    routine.algorithm_name = "NewestFirstShuffle"
    routine.algorithm_params = {"jitter": 0}
    api = MutableSpotify(owner, target=[uri(1), uri(3), uri(2)])
    result = run_live(routine, api)
    assert result["status"] == "success"
    assert api.playlists["target"] == [uri(3), uri(2), uri(1)]


def test_lock_release_failure_retains_identity_and_verified_effect(owner, routine, run_live, reverse_shuffle):
    @contextmanager
    def broken_release(*args, **kwargs):
        yield True
        raise RuntimeError("private lock-release detail")

    api = MutableSpotify(owner, target=[uri(1), uri(2)])
    with patch("shuffify.services.executors.base_executor.playlist_lock", broken_release):
        result = run_live(routine, api)
    assert result["execution_id"] is not None
    assert result["execution_state"] == "recorded"
    assert result["operation"]["resources"][0]["status"] == "complete"
    assert result["operation"]["aggregate_status"] == "unknown"
    assert result["operation"]["retryable"] is False
    assert "private lock-release detail" not in str(result)


def test_membership_count_is_occurrences_not_shifted_positions(owner, routine, run_live):
    link_collection(owner)
    routine.job_type = JobType.DRIP
    api = MutableSpotify(owner, target=[uri(i) for i in range(100)], collection=[uri(101)])
    result = run_live(routine, api)
    assert [r["affected_count"] for r in result["operation"]["resources"]] == [1, 1]


def test_partial_known_effect_logs_once_with_manual_origin(owner, routine, run_live):
    from shuffify.models.db import ActivityLog
    from shuffify.spotify.exceptions import SpotifyWriteRejectedError

    link_collection(owner)
    routine.job_type = JobType.DRIP
    api = MutableSpotify(owner, target=[uri(1)], collection=[uri(2)])

    def rejected(*args):
        raise SpotifyWriteRejectedError("quota")

    api.faults[("remove", "collection")] = rejected
    result = run_live(routine, api)
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["retryable"] is False
    assert result["tracks_added"] == 1
    log = ActivityLog.query.one()
    assert log.description.startswith("Manual")
    assert log.metadata_json["triggered_by"] == "manual"
    assert log.metadata_json["resources"][0]["resource"] == "target"


def prepare_raid(monkeypatch, owner, routine):
    from types import SimpleNamespace

    link_collection(owner)
    routine.job_type = JobType.RAID
    monkeypatch.setattr("shuffify.services.executors.raid_executor._load_sources", lambda *args: [SimpleNamespace()])
    monkeypatch.setattr(
        "shuffify.services.executors.raid_executor._fetch_raid_sources_with_limits",
        lambda *args, **kwargs: ([uri(2)], {}),
    )


def test_collect_compensation_never_writes_target(owner, routine, run_live, monkeypatch):
    from shuffify.spotify.exceptions import SpotifyAPIError, SpotifyPartialBatchError

    prepare_raid(monkeypatch, owner, routine)
    api = MutableSpotify(owner, target=[uri(1)], collection=[])

    def partial(fake, playlist_id, uris):
        fake.playlists[playlist_id] = list(uris)
        del fake.faults[("add", playlist_id)]
        raise SpotifyPartialBatchError(playlist_id, "add", 1, 2, uris, [], SpotifyAPIError("lost"))

    api.faults[("add", "collection")] = partial
    result = run_live(routine, api)
    assert result["status"] == "failed_rolled_back"
    assert api.playlists == {"target": [uri(1)], "collection": []}
    assert [w[1] for w in api.writes] == ["collection", "collection"]


def test_composite_compensation_retains_completed_raid_and_exact_staged_rows(owner, routine, run_live, monkeypatch):
    from shuffify.models.db import PendingRaidTrack
    from shuffify.spotify.exceptions import SpotifyAPIError, SpotifyPartialBatchError

    prepare_raid(monkeypatch, owner, routine)
    routine.job_type = JobType.RAID_AND_DRIP
    monkeypatch.setattr("shuffify.services.executors.drip_executor._select_drip_tracks", lambda *args: [uri(2)])
    api = MutableSpotify(owner, target=[uri(1)], collection=[uri(3)])

    def partial(fake, playlist_id, uris):
        # A concurrent pending row for the same target is outside this invocation.
        db.session.add(
            PendingRaidTrack(
                user_id=owner.id,
                target_playlist_id="target",
                track_uri=uri(9),
                track_name="Concurrent",
                status="pending",
            )
        )
        db.session.commit()
        fake.playlists[playlist_id] = list(uris)
        del fake.faults[("add", playlist_id)]
        raise SpotifyPartialBatchError(playlist_id, "add", 1, 2, uris, [], SpotifyAPIError("lost"))

    api.faults[("add", "target")] = partial
    result = run_live(routine, api)
    assert result["status"] == "failed_rolled_back"
    assert api.playlists == {"target": [uri(1)], "collection": [uri(3), uri(2)]}
    assert {row.track_uri for row in PendingRaidTrack.query.all()} == {uri(2), uri(9)}
    assert [w[:2] for w in api.writes] == [("add", "collection"), ("add", "target"), ("update", "target")]
    assert result["operation"]["steps"][1]["status"] == "restored"
    assert result["operation"]["resources"][0]["status"] == "complete"
    assert next(b for b in result["operation"]["bookkeeping"] if b["kind"] == "pending_stage")["status"] == "complete"
    assert result["operation"]["steps"][0]["step"] == "raid"
    assert result["operation"]["steps"][0]["status"] == "complete"


def test_raid_staging_failure_keeps_collection_effect_and_logs_once(owner, routine, run_live, monkeypatch):
    from shuffify.models.db import ActivityLog

    prepare_raid(monkeypatch, owner, routine)
    api = MutableSpotify(owner, target=[uri(1)], collection=[])
    with patch(
        "shuffify.services.executors.raid_executor.PendingRaidService.stage_tracks",
        side_effect=RuntimeError("database down"),
    ):
        result = run_live(routine, api)
    assert result["operation"]["resources"][0]["status"] == "complete"
    assert result["operation"]["bookkeeping"][0]["status"] == "failed"
    assert result["operation"]["aggregate_status"] == "partial"
    assert api.playlists["collection"] == [uri(2)]
    assert len(api.writes) == 1
    assert ActivityLog.query.count() == 1


def lose_first_rollback():
    """Patch the session so its next rollback fails, as when the connection drops; later ones work."""
    from sqlalchemy.exc import OperationalError

    real_rollback, lost = db.session.rollback, []

    def rollback():
        if not lost:
            lost.append(True)
            raise OperationalError("ROLLBACK", {}, Exception("connection lost"))
        return real_rollback()

    return patch.object(db.session, "rollback", side_effect=rollback), lost


def test_attempted_staging_stays_unknown_when_its_rollback_fails(owner, routine, run_live, monkeypatch):
    prepare_raid(monkeypatch, owner, routine)
    api = MutableSpotify(owner, target=[uri(1)], collection=[])
    losing, lost = lose_first_rollback()
    with patch(
        "shuffify.services.executors.raid_executor.PendingRaidService.stage_tracks",
        side_effect=RuntimeError("database down"),
    ), losing:
        result = run_live(routine, api)
    assert lost
    # Staging was attempted, so its outcome stays unknown: never reported as a skipped no-op.
    staging = [
        (b["status"], b["error_code"]) for b in result["operation"]["bookkeeping"] if b["kind"] == "pending_stage"
    ]
    assert staging == [("unknown", "pending_stage_failed")]


def test_attempted_promotion_stays_unknown_when_its_rollback_fails(owner, routine, run_live):
    link_collection(owner)
    routine.job_type = JobType.DRIP
    db.session.commit()
    api = MutableSpotify(owner, target=[uri(1)], collection=[uri(2)])
    losing, lost = lose_first_rollback()
    with patch("shuffify.services.base.safe_commit", side_effect=RuntimeError("commit lost")), losing:
        result = run_live(routine, api)
    assert lost
    assert [r["status"] for r in result["operation"]["resources"]] == ["complete", "complete"]
    promotion = [
        (b["status"], b["error_code"]) for b in result["operation"]["bookkeeping"] if b["kind"] == "pending_promotion"
    ]
    assert promotion == [("unknown", "pending_promotion_failed")]


def test_rotate_purge_before_all_protected_still_reports_archive_effect(owner, routine, run_live):
    from types import SimpleNamespace

    routine.job_type = JobType.ROTATE
    routine.algorithm_params = {"target_size": 2, "protect_count": 2}
    api = MutableSpotify(owner, target=[uri(1), uri(2)], archive=[uri(1), uri(3)])
    with patch(
        "shuffify.services.playlist_pair_service.PlaylistPairService.get_pair_for_playlist",
        return_value=SimpleNamespace(archive_playlist_id="archive"),
    ):
        result = run_live(routine, api)
    assert result["status"] == "success"
    assert result["tracks_added"] == 0
    assert result["skipped_reason"] == "all_tracks_protected"
    assert result["operation"]["resources"][0]["resource"] == "archive"
    assert result["operation"]["resources"][0]["status"] == "complete"
    assert result["operation"]["resources"][0]["affected_count"] == 1
    assert [w[1] for w in api.writes] == ["archive"]


def test_concurrent_history_does_not_replace_invocation_identity(owner, routine, run_live, reverse_shuffle):
    api = MutableSpotify(owner, target=[uri(1), uri(2)])
    competing_ids = []

    def insert_competing():
        row = JobExecution(schedule_id=routine.id, status="failed", tracks_total=999)
        db.session.add(row)
        db.session.commit()
        competing_ids.append(row.id)

    api.on_write = insert_competing
    result = run_live(routine, api)
    assert result["execution_id"] not in competing_ids
    assert result["tracks_total"] == 2
    assert db.session.get(JobExecution, result["execution_id"]).status == "success"


def test_creation_failure_is_not_started_and_sanitized(routine):
    with patch.object(JobExecutorService, "_create_execution_record", side_effect=RuntimeError("secret")):
        result = JobExecutorService._run_job(routine, routine.id)
    assert result["execution_id"] is None
    assert result["execution_state"] == "not_started"
    assert result["operation"]["aggregate_status"] == "partial"
    assert "secret" not in str(result)


@pytest.mark.parametrize("job_type", [JobType.RAID, JobType.RAID_AND_DRIP])
@pytest.mark.parametrize("linked", [True, False])
def test_committed_staging_survives_identity_read_failure(owner, routine, run_live, monkeypatch, job_type, linked):
    from shuffify.models.db import PendingRaidTrack

    prepare_raid(monkeypatch, owner, routine)
    routine.job_type = job_type
    if not linked:
        from shuffify.models.db import RaidPlaylistLink

        RaidPlaylistLink.query.delete()
        db.session.commit()
    api = MutableSpotify(owner, target=[uri(1)], collection=[])
    with patch(
        "shuffify.services.executors.raid_executor._pending_ids",
        side_effect=[set(), RuntimeError("private identity lookup failure")],
    ):
        result = run_live(routine, api)
    books = {item["kind"]: item for item in result["operation"]["bookkeeping"]}
    assert books["pending_stage"]["status"] == "complete"
    assert books["pending_stage"]["affected_count"] == 1
    assert books["pending_stage_identity"]["status"] == "unknown"
    assert result["tracks_added"] == 1
    assert result["operation"]["aggregate_status"] == "unknown"
    assert result["operation"]["retryable"] is False
    expected = [("collection", "raid", "complete", None)] if linked else []
    if job_type == JobType.RAID_AND_DRIP:
        expected.append(("target", "drip", "not_required", False))  # The Drip never started.
    assert [
        (entry["resource"], entry["step"], entry["status"], entry.get("started"))
        for entry in result["operation"]["resources"]
    ] == expected
    assert api.playlists == {"target": [uri(1)], "collection": [uri(2)] if linked else []}
    assert [row.track_uri for row in PendingRaidTrack.query.all()] == [uri(2)]
    assert [write[:2] for write in api.writes] == ([("add", "collection")] if linked else [])
    assert "private identity lookup failure" not in str(result)


def test_pending_identity_lookup_scopes_candidates_user_and_target(owner):
    from shuffify.models.db import PendingRaidTrack, User
    from shuffify.services.executors.raid_executor import _pending_ids

    other = User(spotify_id="other-live-owner", display_name="Other")
    db.session.add(other)
    db.session.commit()
    rows = [
        PendingRaidTrack(user_id=owner.id, target_playlist_id="target", track_uri=uri(2), track_name="Selected"),
        PendingRaidTrack(user_id=owner.id, target_playlist_id="target", track_uri=uri(9), track_name="Different URI"),
        PendingRaidTrack(user_id=owner.id, target_playlist_id="other", track_uri=uri(2), track_name="Different target"),
        PendingRaidTrack(user_id=other.id, target_playlist_id="target", track_uri=uri(2), track_name="Different owner"),
    ]
    db.session.add_all(rows)
    db.session.commit()
    assert _pending_ids(owner.id, "target", [uri(2)]) == {rows[0].id}


def test_pending_identity_preflight_failure_reports_staging_never_started(owner, routine, run_live, monkeypatch):
    from shuffify.models.db import PendingRaidTrack

    prepare_raid(monkeypatch, owner, routine)
    api = MutableSpotify(owner, target=[uri(1)], collection=[])
    with patch(
        "shuffify.services.executors.raid_executor._pending_ids",
        side_effect=RuntimeError("private pre-stage read failure"),
    ):
        result = run_live(routine, api)
    books = {item["kind"]: item for item in result["operation"]["bookkeeping"]}
    assert books["pending_stage"]["status"] == "failed"
    assert books["pending_stage"]["affected_count"] == 0
    assert books["pending_stage"]["error_code"] == "pending_stage_read_failed"
    assert result["tracks_added"] == 0
    assert result["operation"]["resources"][0]["status"] == "complete"
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["retryable"] is False
    assert api.playlists == {"target": [uri(1)], "collection": [uri(2)]}
    assert PendingRaidTrack.query.count() == 0
    assert len(api.writes) == 1


@pytest.mark.parametrize(
    ("job_type", "dependent_step"),
    [(JobType.RAID_AND_DRIP, "drip"), (JobType.RAID_AND_SHUFFLE, "shuffle")],
)
def test_saved_composite_lists_unstarted_steps_after_raid_failure(
    owner, routine, run_live, monkeypatch, job_type, dependent_step
):
    prepare_raid(monkeypatch, owner, routine)
    routine.job_type = job_type
    api = MutableSpotify(owner, target=[uri(1)], collection=[])
    with patch(
        "shuffify.services.executors.raid_executor.PendingRaidService.stage_tracks",
        side_effect=RuntimeError("staging failed after collection write"),
    ), patch(f"shuffify.services.executors.{dependent_step}_executor.execute_{dependent_step}") as dependent:
        result = run_live(routine, api)
    dependent.assert_not_called()
    assert result["execution_id"] is not None
    assert result["execution_state"] == "recorded"
    assert result["operation"]["steps"] == [
        {"step": "raid", "status": "partial"},
        {"step": dependent_step, "status": "not_started"},
    ]
    assert result["operation"]["resources"][0]["status"] == "complete"
    assert result["operation"]["bookkeeping"][0]["status"] == "failed"
    # The unstarted step staged and promoted nothing; only Drip records an inbox fact.
    unstarted = [b for b in result["operation"]["bookkeeping"] if b["step"] == dependent_step]
    assert unstarted == ([{"kind": "pending_promotion", "step": "drip", "status": "not_required", "error_code": None,
                           "resource": "target", "affected_count": 0}] if dependent_step == "drip" else [])
    # Nor did it write the target: the reply names it as unchanged, by a step that did not run.
    assert [r for r in result["operation"]["resources"] if r["step"] == dependent_step] == [{
        "resource": "target", "resource_name": "Fresh target", "step": dependent_step,
        "mutation_type": {"drip": "track_membership", "shuffle": "track_order"}[dependent_step],
        "status": "not_required", "dispatched": False, "affected_count": 0, "retryable": False,
        "error_code": None, "verification": None, "started": False,
    }]
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["retryable"] is False
    assert api.playlists == {"target": [uri(1)], "collection": [uri(2)]}


@pytest.mark.parametrize("job_type", [JobType.RAID_AND_DRIP, JobType.RAID_AND_SHUFFLE])
@pytest.mark.parametrize("compensate", [False, True])
def test_later_composite_failure_preserves_raid_and_final_step_certainty(
    owner, routine, run_live, monkeypatch, reverse_shuffle, job_type, compensate
):
    from shuffify.models.db import PendingRaidTrack
    from shuffify.spotify.exceptions import SpotifyAPIError, SpotifyPartialBatchError

    prepare_raid(monkeypatch, owner, routine)
    routine.job_type = job_type
    monkeypatch.setattr(
        "shuffify.services.executors.raid_executor._fetch_raid_sources_with_limits",
        lambda *args, **kwargs: ([uri(2), uri(5)], {}),
    )
    api = MutableSpotify(owner, target=[uri(1), uri(4)], collection=[uri(3)])
    step = "drip" if job_type == JobType.RAID_AND_DRIP else "shuffle"
    method, playlist = ("remove", "collection") if step == "drip" else ("update", "target")
    monkeypatch.setattr("shuffify.services.executors.drip_executor._select_drip_tracks", lambda *args: [uri(2)])

    def fail(fake, playlist_id, uris):
        fake.playlists[playlist_id] = [uri(99)]
        del fake.faults[(method, playlist_id)]
        if compensate:
            raise SpotifyPartialBatchError(playlist_id, method, 1, 2, uris, [], SpotifyAPIError("lost"))
        raise TimeoutError("lost reply")

    api.faults[(method, playlist)] = fail
    result = run_live(routine, api)
    operation = result["operation"]
    assert operation["steps"][0]["status"] == "complete"
    assert operation["steps"][1]["status"] == ("restored" if compensate else "unknown")
    assert operation["aggregate_status"] == ("partial" if compensate else "unknown")
    assert operation["retryable"] is False
    assert operation["resources"][0]["step"] == "raid"
    assert operation["resources"][0]["status"] == "complete"
    assert next(b for b in operation["bookkeeping"] if b["kind"] == "pending_stage")["status"] == "complete"
    assert {row.track_uri for row in PendingRaidTrack.query.all()} == {uri(2), uri(5)}
    assert {row.status for row in PendingRaidTrack.query.all()} == {"pending"}
    assert len({b["step"] for b in operation["bookkeeping"] if b["kind"] == "pending_stage"}) == 1
    if compensate:
        assert api.playlists == {"target": [uri(1), uri(4)], "collection": [uri(3), uri(2), uri(5)]}
    elif step == "shuffle":
        assert api.playlists["collection"] == [uri(3), uri(2), uri(5)]
    assert result["tracks_added"] == 2
    if step == "drip":
        assert result["tracks_dripped"] == 1


def test_verified_drip_reconciles_locks_even_when_promotion_commit_fails(owner, routine, run_live):
    from shuffify.models.db import PendingRaidTrack, TrackLock
    from shuffify.services.track_lock_service import TrackLockService

    link_collection(owner)
    routine.job_type = JobType.DRIP
    db.session.add(PendingRaidTrack(
        user_id=owner.id, target_playlist_id="target", track_uri=uri(2), track_name="New", status="pending"
    ))
    db.session.commit()
    TrackLockService.set_lock(owner.id, "target", uri(1), 0, "super")
    api = MutableSpotify(owner, target=[uri(1)], collection=[uri(2)])
    with patch("shuffify.services.base.safe_commit", side_effect=RuntimeError("promotion database failure")):
        result = run_live(routine, api)
    assert api.playlists == {"target": [uri(2), uri(1)], "collection": []}
    assert TrackLock.query.one().position == 1
    assert PendingRaidTrack.query.one().status == "pending"
    assert {r["status"] for r in result["operation"]["resources"]} == {"complete"}
    promotion = next(b for b in result["operation"]["bookkeeping"] if b["kind"] == "pending_promotion")
    assert promotion["status"] == "failed"
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["retryable"] is False


@pytest.mark.parametrize(
    ("job_type", "dependent_step"),
    [(JobType.RAID_AND_DRIP, "drip"), (JobType.RAID_AND_SHUFFLE, "shuffle")],
)
def test_ambiguous_first_composite_step_keeps_unknown_and_unstarted_successor(
    owner, routine, run_live, monkeypatch, job_type, dependent_step
):
    prepare_raid(monkeypatch, owner, routine)
    routine.job_type = job_type
    api = MutableSpotify(owner, target=[uri(1)], collection=[])

    def lost(fake, playlist_id, uris):
        fake.playlists[playlist_id] = list(uris)
        raise TimeoutError("reply lost after append")

    api.faults[("add", "collection")] = lost
    with patch(f"shuffify.services.executors.{dependent_step}_executor.execute_{dependent_step}") as dependent:
        result = run_live(routine, api)
    dependent.assert_not_called()
    assert result["operation"]["steps"] == [
        {"step": "raid", "status": "unknown"},
        {"step": dependent_step, "status": "not_started"},
    ]
    assert result["operation"]["resources"][0]["status"] == "unknown"
    assert result["operation"]["aggregate_status"] == "unknown"
    assert api.writes == [("add", "collection", [uri(2)])]


@pytest.mark.parametrize("triggered_by", ["manual", "scheduler"])
@pytest.mark.parametrize("collect_to_spotify", [False, True])
def test_composite_drip_gate_records_applied_raid_as_failed_not_skipped(
    owner, routine, run_live, monkeypatch, triggered_by, collect_to_spotify
):
    from shuffify.models.db import PendingRaidTrack
    from shuffify.services.raid_link_service import RaidLinkService

    prepare_raid(monkeypatch, owner, routine)
    routine.job_type = JobType.RAID_AND_DRIP
    routine.is_enabled = triggered_by == "scheduler"
    link = RaidLinkService.get_link_for_playlist(owner.id, "target")
    link.drip_enabled = False
    db.session.commit()
    api = MutableSpotify(owner, target=[uri(1)], collection=[])
    if not collect_to_spotify:
        # The supported DB-only Raid path has no linked collection write.
        monkeypatch.setattr("shuffify.services.executors.raid_executor._add_to_raid_playlist", lambda *args: None)
    original_run = JobExecutorService._run_job
    observed = []

    def capture(*args, **kwargs):
        observed.append(original_run(*args, **kwargs))
        return observed[-1]

    with patch.object(JobExecutorService, "_get_spotify_api", return_value=api), patch.object(
        JobExecutorService, "_run_job", side_effect=capture
    ):
        if triggered_by == "manual":
            JobExecutorService.execute_now(routine.id, owner.id)
        else:
            JobExecutorService.execute(routine.id)
    result = observed[0]
    assert result["status"] == "failed"
    assert result["http_status"] == 502
    assert result["operation"]["aggregate_status"] == "partial"
    assert result["operation"]["error_code"] == "drip_disabled"
    assert result["operation"]["retryable"] is False
    assert [step["status"] for step in result["operation"]["steps"]] == ["complete", "not_started"]
    promotion = next(b for b in result["operation"]["bookkeeping"] if b["kind"] == "pending_promotion")
    assert (promotion["step"], promotion["status"]) == ("drip", "not_required")
    assert result["tracks_added"] == 1
    assert result["tracks_dripped"] == 0
    assert api.writes == ([("add", "collection", [uri(2)])] if collect_to_spotify else [])
    assert PendingRaidTrack.query.one().status == "pending"
    assert link.drip_enabled is False
    row = db.session.get(JobExecution, result["execution_id"])
    assert row.status == routine.last_status == "failed"
    assert row.tracks_added == 1
    assert row.completed_at is not None
    assert routine.is_enabled == (triggered_by == "scheduler")


@pytest.mark.parametrize("job_type", [JobType.DRIP, JobType.RAID_AND_DRIP])
def test_no_effect_drip_gate_allows_explicit_retry_after_enabling(
    owner, routine, run_live, monkeypatch, job_type
):
    from shuffify.models.db import ActivityLog, PendingRaidTrack
    from shuffify.services.raid_link_service import RaidLinkService

    prepare_raid(monkeypatch, owner, routine)
    routine.job_type = job_type
    link = RaidLinkService.get_link_for_playlist(owner.id, "target")
    link.drip_enabled = False
    db.session.commit()
    # The source resolver filters the discovered URI already on target.
    monkeypatch.setattr(
        "shuffify.services.executors.raid_executor._fetch_raid_sources_with_limits",
        lambda *args, **kwargs: ([], {}),
    )
    api = MutableSpotify(owner, target=[uri(1), uri(2)], collection=[uri(3)])
    result = run_live(routine, api)
    assert result["status"] == "skipped"
    assert result["http_status"] == 409
    assert result["operation"]["retryable"] is True
    assert result["operation"]["aggregate_status"] == "partial"
    assert db.session.get(JobExecution, result["execution_id"]).status == "skipped"
    assert PendingRaidTrack.query.count() == 0
    assert ActivityLog.query.count() == 0
    assert api.writes == []
    link.drip_enabled = True
    db.session.commit()
    retry = run_live(routine, api)
    assert retry["status"] == "success"
    assert retry["execution_id"] != result["execution_id"]
    assert api.playlists == {"target": [uri(3), uri(1), uri(2)], "collection": []}


@pytest.mark.parametrize("job_type", [JobType.RAID_AND_DRIP, JobType.RAID_AND_SHUFFLE])
@pytest.mark.parametrize("failure", ["execution_record", "coordination"])
def test_run_bookkeeping_uncertainty_preserves_completed_composite_steps(
    owner, routine, run_live, monkeypatch, reverse_shuffle, job_type, failure
):
    prepare_raid(monkeypatch, owner, routine)
    routine.job_type = job_type
    api = MutableSpotify(owner, target=[uri(1), uri(4)], collection=[])
    actual_commit = db.session.commit

    def fail_status_commit():
        if any(isinstance(row, JobExecution) and row.status == "success" for row in db.session.dirty):
            raise RuntimeError("private status-commit detail")
        return actual_commit()

    @contextmanager
    def fail_release(*args, **kwargs):
        yield True
        raise RuntimeError("private lock-release detail")

    failure_context = (
        patch.object(db.session, "commit", side_effect=fail_status_commit)
        if failure == "execution_record"
        else patch("shuffify.services.executors.base_executor.playlist_lock", fail_release)
    )
    with failure_context:
        result = run_live(routine, api)
    assert result["status"] == "failed"
    assert result["http_status"] == 502
    assert result["operation"]["aggregate_status"] == "unknown"
    assert result["operation"]["retryable"] is False
    assert [step["status"] for step in result["operation"]["steps"]] == ["complete", "complete"]
    assert {r["status"] for r in result["operation"]["resources"]} == {"complete"}
    entry = next(b for b in result["operation"]["bookkeeping"] if b["kind"] == failure)
    assert entry["step"] == "run"
    assert entry["status"] == "unknown"
    assert "private" not in str(result)
    assert len(api.writes) == (3 if job_type == JobType.RAID_AND_DRIP else 2)
    assert db.session.get(JobExecution, result["execution_id"]) is not None


def test_rotate_compensation_uses_baseline_before_all_subphase_writes(owner, routine, run_live, monkeypatch):
    from types import SimpleNamespace

    from shuffify.spotify.exceptions import SpotifyAPIError, SpotifyPartialBatchError

    routine.job_type = JobType.ROTATE
    routine.algorithm_params = {"target_size": 2, "rotation_count": 1}
    original = {"target": [uri(1), uri(2)], "archive": [uri(1), uri(3)]}
    api = MutableSpotify(owner, **original)
    monkeypatch.setattr("shuffify.services.executors.rotate_executor._sample_at_most", lambda values, n: values[:n])

    def partial(fake, playlist_id, uris):
        fake.playlists[playlist_id].extend(uris)
        raise SpotifyPartialBatchError(playlist_id, "add", 1, 2, uris, [], SpotifyAPIError("lost"))

    api.faults[("add", "target")] = partial
    with patch(
        "shuffify.services.playlist_pair_service.PlaylistPairService.get_pair_for_playlist",
        return_value=SimpleNamespace(archive_playlist_id="archive"),
    ):
        result = run_live(routine, api)
    assert result["status"] == "failed_rolled_back"
    assert api.playlists == original
    assert {r["step"] for r in result["operation"]["resources"]} == {"rotate", "archive_purge"}
    assert {r["status"] for r in result["operation"]["resources"]} == {"restored"}
    archive_facts = [r for r in result["operation"]["resources"] if r["resource"] == "archive"]
    assert all(r["restored_track_uris"] == original["archive"] for r in archive_facts)
    assert [write[:2] for write in api.writes] == [
        ("remove", "archive"), ("remove", "target"), ("add", "archive"),
        ("remove", "archive"), ("add", "target"), ("update", "archive"), ("update", "target"),
    ]


def test_zero_row_staging_emits_not_required_before_retryable_drip_gate(owner, routine, run_live, monkeypatch):
    from shuffify.models.db import ActivityLog, PendingRaidTrack
    from shuffify.services.raid_link_service import RaidLinkService

    prepare_raid(monkeypatch, owner, routine)
    routine.job_type = JobType.RAID_AND_DRIP
    RaidLinkService.get_link_for_playlist(owner.id, "target").drip_enabled = False
    # A selected URI is already staged by the time this DB-only Raid stages it.
    db.session.add(PendingRaidTrack(
        user_id=owner.id, target_playlist_id="target", track_uri=uri(2), track_name="Existing", status="pending"
    ))
    db.session.commit()
    monkeypatch.setattr("shuffify.services.executors.raid_executor._add_to_raid_playlist", lambda *args: None)
    api = MutableSpotify(owner, target=[uri(1)], collection=[])
    result = run_live(routine, api)
    stage = next(b for b in result["operation"]["bookkeeping"] if b["kind"] == "pending_stage")
    assert stage["status"] == "not_required"
    assert stage["affected_count"] == 0
    assert result["operation"]["retryable"] is True
    assert result["status"] == "skipped"
    assert result["http_status"] == 409
    assert api.writes == []
    assert PendingRaidTrack.query.count() == 1
    assert ActivityLog.query.count() == 0
    assert all(
        b["kind"] == "execution_record" or b["status"] == "not_required"
        for b in result["operation"]["bookkeeping"]
    )
