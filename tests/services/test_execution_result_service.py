"""The durable result boundary accepts only small observed execution facts."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from shuffify.models.db import JobExecution, Schedule, User, db
from shuffify.services.execution_result_service import (
    build_execution_detail,
    build_live_execution_detail,
    serialize_execution_detail,
)
from shuffify.services.executors.base_executor import JobExecutorService
from shuffify.services.live_operation_service import LiveOperation
from shuffify.services.playlist_pair_service import PlaylistPairService
from shuffify.services.raid_link_service import RaidLinkService


def test_legacy_and_invalid_details_are_unavailable():
    unavailable = {"detail_status": "unavailable", "version": None, "steps": None}
    assert serialize_execution_detail(None) == unavailable
    assert serialize_execution_detail({"version": 2}) == unavailable
    assert serialize_execution_detail({"version": 1, "operation": "raid", "steps": []}) == unavailable


def test_malformed_stored_discriminators_return_unavailable():
    valid = build_execution_detail(
        operation="raid", execution_id=7, outcome="complete",
        steps=[{"step": "raid", "status": "complete", "resources": [{
            "resource": "collection", "status": "complete", "kind": "collection",
        }], "bookkeeping": [{"kind": "pending_stage", "status": "complete"}]}],
    )
    invalid = []
    for key in ("operation", "outcome", "run_state", "execution_record"):
        value = deepcopy(valid)
        value[key] = []
        invalid.append(value)
    for key in ("step", "status"):
        value = deepcopy(valid)
        value["steps"][0][key] = {}
        invalid.append(value)
    for path in (("resources", "kind"), ("resources", "status"),
                 ("bookkeeping", "kind"), ("bookkeeping", "status")):
        value = deepcopy(valid)
        value["steps"][0][path[0]][0][path[1]] = []
        invalid.append(value)
    for value in invalid:
        assert serialize_execution_detail(value)["detail_status"] == "unavailable"


def test_running_composite_reports_pending_step_and_partial_outcome():
    journal = _Journal()
    journal.steps = [{"step": "raid", "status": "complete", "tracks_added": 2}]
    journal.resources = [r for r in journal.resources if r["step"] == "raid"]
    journal.outcome = lambda: {"aggregate_status": "complete"}
    detail = serialize_execution_detail(build_live_execution_detail(
        journal, 8, "target", run_state="running"
    ))
    assert detail["run_state"] == "running"
    assert detail["outcome"] == "partial"
    assert [(step["step"], step["status"]) for step in detail["steps"]] == [
        ("raid", "complete"), ("drip", "pending")
    ]


def test_write_and_read_allowlist_excludes_track_arrays_errors_and_credentials():
    raw = build_execution_detail(
        operation="drip", execution_id=42, outcome="partial",
        resource_ids={"target": "target", "collection": "collection", "token": "secret"},
        steps=[{
            "step": "drip", "status": "unknown", "counts": {
                "tracks_moved_to_target": 3, "tracks_removed_from_collection": None,
                "tracks_collected": -1, "secret": 7,
            },
            "resources": [{
                "resource": "target", "status": "complete", "kind": "target",
                "affected_count": 3, "dispatched": True,
                "baseline_track_uris": ["spotify:track:private"],
                "error_message": "raw exception", "access_token": "secret",
            }, {
                "resource": "collection", "status": "unknown", "kind": "collection",
                "affected_count": None, "dispatched": True,
            }],
            "bookkeeping": [{"kind": "pending_promotion", "status": "unknown", "error_code": "secret"}],
            "error": "raw exception",
        }],
    )
    text = str(raw)
    assert "spotify:track" not in text
    assert "secret" not in text
    assert "raw exception" not in text
    detail = serialize_execution_detail(raw)
    assert detail["steps"][0]["counts"] == {"tracks_moved_to_target": 3}
    assert detail["steps"][0]["resources"][1]["status"] == "unknown"
    assert "affected_count" not in detail["steps"][0]["resources"][1]


class _Journal:
    kind = "raid_and_drip"
    steps = [
        {"step": "raid", "status": "complete", "tracks_added": 2},
        {"step": "drip", "status": "unknown"},
    ]
    bookkeeping = [
        {"kind": "pending_stage", "step": "raid", "status": "complete", "affected_count": 2, "resource": "target"},
    ]
    resources = [
        {"resource": "collection", "step": "raid", "status": "complete", "dispatched": True,
         "affected_count": 2, "baseline_track_uris": [], "current_track_uris": ["secret1", "secret2"]},
        {"resource": "target", "step": "drip", "status": "restored", "dispatched": True,
         "affected_count": None, "baseline_track_uris": [], "restored_track_uris": []},
        {"resource": "collection", "step": "drip", "status": "unknown", "dispatched": True,
         "affected_count": None, "baseline_track_uris": ["secret1", "secret2"]},
    ]

    def step_resources(self, step):
        return [r for r in self.resources if r["step"] == step]

    def outcome(self):
        return {"aggregate_status": "unknown"}


def test_composite_preserves_earlier_collection_and_uncertain_restoration():
    detail = serialize_execution_detail(build_live_execution_detail(_Journal(), 9, "target"))
    assert detail["execution_id"] == 9
    assert detail["resource_ids"] == {"target": "target", "collection": "collection"}
    assert detail["steps"][0]["counts"] == {"tracks_collected": 2}
    assert detail["steps"][1]["status"] == "unknown"
    assert "tracks_moved_to_target" not in detail["steps"][1].get("counts", {})
    assert detail["steps"][1]["resources"][0]["status"] == "restored"
    assert detail["steps"][1]["resources"][1]["status"] == "unknown"


def test_completed_composite_step_commits_before_later_dispatch_and_survives_failure(app_ctx):
    user = User(spotify_id="detail_user", display_name="Detail")
    db.session.add(user)
    db.session.flush()
    schedule = Schedule(
        user_id=user.id, job_type="raid_and_drip", target_playlist_id="target",
        target_playlist_name="Target", schedule_type="interval", schedule_value="daily", is_enabled=True,
    )
    db.session.add(schedule)
    db.session.commit()
    execution = JobExecutorService._create_execution_record(schedule.id)
    journal = LiveOperation("raid_and_drip", Mock(), user)
    journal.execution_id = execution.id
    journal.locked_target = "target"
    token = journal.activate()

    def raid(_schedule, _api):
        journal.book("pending_stage", "complete", resource="target", affected_count=2)
        journal.resources.append({
            "resource": "collection", "step": "raid", "status": "complete",
            "dispatched": True, "affected_count": 2,
            "baseline_track_uris": [], "current_track_uris": ["private1", "private2"],
        })
        return {"tracks_added": 2, "tracks_total": 5}

    def drip(_schedule, _api):
        persisted = db.session.get(JobExecution, execution.id).result_json
        assert persisted["run_state"] == "running"
        assert persisted["steps"][0]["counts"] == {"tracks_collected": 2}
        journal.resources.append({
            "resource": "target", "step": "drip", "status": "unknown",
            "dispatched": True, "affected_count": None,
        })
        raise RuntimeError("simulated later write failure")

    try:
        with patch("shuffify.services.executors.raid_executor.execute_raid", raid), patch(
            "shuffify.services.executors.drip_executor.execute_drip", drip
        ):
            with pytest.raises(RuntimeError):
                JobExecutorService._execute_job_type(schedule, Mock())
        assert JobExecutorService._record_failure(execution, schedule, RuntimeError("failure"), schedule.id)
        db.session.refresh(execution)
        detail = execution.to_dict()["result_detail"]
        assert detail["run_state"] == "finished"
        assert detail["steps"][0]["counts"] == {"tracks_collected": 2}
        assert detail["steps"][1]["status"] == "unknown"
        assert detail["steps"][1]["resources"][0]["status"] == "unknown"
        assert "private1" not in str(execution.result_json)
    finally:
        LiveOperation.reset(token)


def test_detail_commit_failure_stops_later_spotify_step_and_keeps_known_first_step(app_ctx):
    user = User(spotify_id="detail_failure_user", display_name="Detail")
    db.session.add(user)
    db.session.flush()
    schedule = Schedule(
        user_id=user.id, job_type="raid_and_shuffle", target_playlist_id="target",
        target_playlist_name="Target", schedule_type="interval", schedule_value="daily", is_enabled=True,
    )
    db.session.add(schedule)
    db.session.commit()
    calls = []

    def raid(_schedule, _api):
        from shuffify.services.live_operation_service import current_operation

        journal = current_operation()
        journal.book("pending_stage", "complete", resource="target", affected_count=1)
        journal.resources.append({
            "resource": "collection", "resource_name": "Collection", "step": "raid",
            "status": "complete", "dispatched": True, "affected_count": 1,
        })
        return {"tracks_added": 1, "tracks_total": 3}

    def shuffle(_schedule, _api):
        calls.append("shuffle")
        return {"tracks_added": 0, "tracks_total": 3}

    @contextmanager
    def acquired(_target):
        yield True

    original_commit = db.session.commit
    commits = 0

    def fail_first_detail_commit():
        nonlocal commits
        commits += 1
        if commits == 2:  # first is the running execution row
            raise RuntimeError("database unavailable")
        return original_commit()

    with patch("shuffify.services.executors.raid_executor.execute_raid", raid), patch(
        "shuffify.services.executors.shuffle_executor.execute_shuffle", shuffle
    ), patch("shuffify.services.executors.base_executor.playlist_lock", acquired), patch.object(
        JobExecutorService, "_get_spotify_api", return_value=Mock()
    ), patch.object(db.session, "commit", side_effect=fail_first_detail_commit):
        result = JobExecutorService._run_job(schedule, schedule.id)
    assert calls == []
    assert result["status"] == "failed"
    assert result["operation"]["retryable"] is False
    detail = result["result_detail"]
    assert detail["execution_record"] == "unknown"
    assert detail["steps"][0]["status"] == "complete"
    assert detail["steps"][0]["counts"] == {"tracks_collected": 1}
    assert detail["steps"][1]["status"] == "not_started"
    saved = db.session.get(JobExecution, result["execution_id"])
    assert saved.to_dict()["result_detail"]["steps"][0]["counts"] == {"tracks_collected": 1}


def test_closed_drip_gate_has_ordered_proven_zero_counts():
    journal = _Journal()
    journal.kind = "drip"
    journal.steps = [{"step": "drip", "status": "not_started"}]
    journal.bookkeeping = [{
        "kind": "pending_promotion", "step": "drip", "status": "not_required",
        "affected_count": 0, "resource": "target",
    }]
    journal.resources = [{
        "resource": "target", "step": "drip", "status": "not_required",
        "dispatched": False, "affected_count": 0,
    }]
    detail = serialize_execution_detail(build_live_execution_detail(journal, 14, "target"))
    assert detail["steps"][0]["counts"] == {
        "tracks_moved_to_target": 0, "tracks_removed_from_collection": 0,
    }


def test_single_drip_gate_uses_real_dispatch_journal_step():
    schedule = Mock(job_type="drip", target_playlist_id="target")
    journal = LiveOperation("drip", Mock(), Mock())
    journal.locked_target = "target"
    token = journal.activate()

    def closed_gate(_schedule, _api):
        journal.record_not_started(["drip"])
        return {"tracks_added": 0, "tracks_total": 3, "skipped_reason": "drip_disabled"}

    try:
        with patch("shuffify.services.executors.drip_executor.execute_drip", closed_gate):
            JobExecutorService._execute_job_type(schedule, Mock())
        assert [(step["step"], step["status"]) for step in journal.steps] == [("drip", "not_started")]
        detail = serialize_execution_detail(build_live_execution_detail(journal, 12, "target"))
        assert detail["steps"][0]["status"] == "not_started"
        assert detail["steps"][0]["counts"] == {
            "tracks_moved_to_target": 0, "tracks_removed_from_collection": 0,
        }
    finally:
        LiveOperation.reset(token)


@pytest.mark.parametrize("collection_uris,target_uris", [
    ([], ["spotify:track:existing"]),
    (["spotify:track:existing"], ["spotify:track:existing"]),
])
def test_real_drip_noop_persists_proven_zero_counts(app_ctx, collection_uris, target_uris):
    user = User(spotify_id="drip_noop", display_name="Drip no-op")
    db.session.add(user)
    db.session.flush()
    RaidLinkService.create_link(
        user_id=user.id, target_playlist_id="target", raid_playlist_id="collection",
        drip_enabled=True,
    )
    schedule = Schedule(
        user_id=user.id, job_type="drip", target_playlist_id="target", target_playlist_name="Target",
        schedule_type="interval", schedule_value="daily", is_enabled=True,
    )
    db.session.add(schedule)
    db.session.commit()
    execution = JobExecutorService._create_execution_record(schedule.id)
    journal = LiveOperation("drip", Mock(), user)
    journal.locked_target = "target"
    journal.execution_id = execution.id
    uris = {"collection": collection_uris, "target": target_uris}
    journal.read = lambda playlist_id: SimpleNamespace(
        tracks=tuple({"uri": uri} for uri in uris[playlist_id]),
        track_uris=tuple(uris[playlist_id]), name=playlist_id, loaded_at="now",
    )
    token = journal.activate()
    try:
        with patch("shuffify.services.executors.drip_executor._auto_snapshot_before_drip"):
            result = JobExecutorService._execute_job_type(schedule, Mock())
        JobExecutorService._record_success(execution, schedule, result)
        detail = execution.to_dict()["result_detail"]
        assert detail["steps"][0]["status"] == "complete"
        assert detail["steps"][0]["counts"] == {
            "tracks_moved_to_target": 0, "tracks_removed_from_collection": 0,
        }
        assert detail["run_state"] == "finished"
    finally:
        LiveOperation.reset(token)


def test_real_empty_target_rotate_persists_proven_zero_counts(app_ctx):
    user = User(spotify_id="rotate_noop", display_name="Rotate no-op")
    db.session.add(user)
    db.session.flush()
    PlaylistPairService.create_pair(user.id, "target", "archive")
    schedule = Schedule(
        user_id=user.id, job_type="rotate", target_playlist_id="target", target_playlist_name="Target",
        schedule_type="interval", schedule_value="daily", is_enabled=True,
        algorithm_params={"rotation_mode": "swap", "rotation_count": 1, "target_size": 5},
    )
    db.session.add(schedule)
    db.session.commit()
    execution = JobExecutorService._create_execution_record(schedule.id)
    journal = LiveOperation("rotate", Mock(), user)
    journal.locked_target = "target"
    journal.execution_id = execution.id
    journal.read = lambda playlist_id: SimpleNamespace(tracks=(), track_uris=(), name=playlist_id, loaded_at="now")
    token = journal.activate()
    try:
        result = JobExecutorService._execute_job_type(schedule, Mock())
        JobExecutorService._record_success(execution, schedule, result)
        detail = execution.to_dict()["result_detail"]
        assert detail["steps"][0]["status"] == "complete"
        assert detail["steps"][0]["counts"] == {
            "tracks_archived": 0, "tracks_returned": 0,
            "tracks_removed_from_target": 0, "tracks_removed_from_archive": 0,
        }
    finally:
        LiveOperation.reset(token)


def test_rotation_counts_distinguish_archive_target_and_purge():
    journal = _Journal()
    journal.kind = "rotate"
    journal.steps = [{"step": "rotate", "status": "complete", "tracks_added": 1}]
    journal.bookkeeping = []
    journal.resources = [
        {"resource": "archive", "step": "archive_purge", "status": "complete", "dispatched": True,
         "baseline_track_uris": ["overlap", "older"], "current_track_uris": ["older"],
         "affected_count": 1},
        {"resource": "target", "step": "rotate", "status": "complete", "dispatched": True,
         "baseline_track_uris": ["out", "keep"], "current_track_uris": ["keep", "in"],
         "affected_count": 2},
        {"resource": "archive", "step": "rotate", "status": "complete", "dispatched": True,
         "baseline_track_uris": ["older", "in"], "current_track_uris": ["older", "out"],
         "affected_count": 2},
    ]

    def resources_for(step):
        return journal.resources

    journal.step_resources = resources_for
    detail = serialize_execution_detail(build_live_execution_detail(journal, 15, "target"))
    assert detail["steps"][0]["counts"] == {
        "archive_overlaps_removed": 1, "tracks_returned": 1,
        "tracks_removed_from_target": 1, "tracks_archived": 1,
        "tracks_removed_from_archive": 1,
    }
    assert "baseline_track_uris" not in str(detail)
    assert "older" not in str(detail)


def test_exact_result_detail_does_not_expire_unrelated_session_objects(app_ctx):
    user = User(spotify_id="detail_read_user", display_name="Keep me loaded")
    db.session.add(user)
    db.session.commit()
    execution = JobExecution(status="success", result_json=build_execution_detail(
        operation="shuffle", execution_id=1, outcome="complete",
        steps=[{"step": "shuffle", "status": "complete", "counts": {"tracks_reordered": 2}}],
    ))
    db.session.add(execution)
    db.session.commit()
    assert execution.id == 1
    journal = Mock()
    journal.bookkeeping = []
    schedule = Mock(target_playlist_id="target")
    with patch.object(db.session, "expire_all", side_effect=AssertionError("global expiry")), patch.object(
        db.session, "get", side_effect=AssertionError("duplicate lookup")
    ):
        detail = JobExecutorService._result_detail(
            execution, journal, schedule, {"execution_id": execution.id}
        )
    assert detail == execution.to_dict()["result_detail"]
    assert user.display_name == "Keep me loaded"


def test_activity_reuses_response_detail_without_second_lookup(app_ctx):
    detail = serialize_execution_detail(build_execution_detail(
        operation="raid", execution_id=4, outcome="complete",
        steps=[{"step": "raid", "status": "complete", "counts": {"tracks_collected": 2}}],
    ))
    journal = Mock()
    journal.kind = "raid"
    journal.triggered_by = "manual"
    journal.resources = [{
        "resource": "collection", "resource_name": "Collection", "step": "raid",
        "status": "complete", "dispatched": True, "affected_count": 2,
    }]
    journal.bookkeeping = []
    journal.outcome.return_value = {"aggregate_status": "complete"}
    schedule = Mock(id=None, user_id=7, target_playlist_id="target", target_playlist_name="Target")
    with patch.object(JobExecutorService, "_result_detail", side_effect=AssertionError("duplicate detail")), patch(
        "shuffify.services.activity_log_service.ActivityLogService.log"
    ) as log:
        JobExecutorService._log_live_activity(schedule, journal, {"execution_id": 4}, detail)
    assert log.call_args.kwargs["metadata"]["result_detail"] == detail
