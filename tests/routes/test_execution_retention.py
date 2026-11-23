"""Exercise maintenance through Flask CLI, followed by the normal restore, Undo and replay paths."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from shuffify.models.db import JobExecution, PlaylistSnapshot, Schedule, UserSettings, db
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.scheduled_admission import admit_scheduled, build_cadence
from tests.mutable_spotify import uri
from tests.queued_operations import post_and_run, run_operation
from tests.routes.test_operation_restore_routes import client
from tests.routes.test_storage_limits import accounts as accounts
from tests.services.operations.test_restore_command import restoration as restoration
from tests.services.operations.test_routine_commands import routine as routine

OLD = datetime.now(timezone.utc) - timedelta(days=91)
RECENT = OLD + timedelta(days=2)


def cleanup(app, *args):
    result = app.test_cli_runner().invoke(args=["prune-executions", *args])
    assert result.exit_code == 0, result.output or repr(result.exception)
    db.session.expire_all()
    return json.loads(result.output)


def age(*ids):
    JobExecution.query.filter(JobExecution.id.in_(ids)).update({"completed_at": OLD}, synchronize_session=False)
    db.session.commit()


def test_bounded_cleanup_cutoff_cursor_snapshot_and_unsettled_evidence(accounts):
    owner, alice, _, _ = accounts
    app = alice.application
    original = {"runtime": {"terminal_finalization": {"state": "succeeded", "done": True}},
                "plans": [{"batches": [{"state": "acknowledged"}]}], "result": {"success": True}}

    def execution(**values):
        defaults = {"user_id": owner, "kind": "visibility", "state": "succeeded", "status": "success",
                    "completed_at": OLD, "journal": deepcopy(original), "payload": {"command": {}}}
        return JobExecution(**(defaults | values))

    old = execution()
    recent = execution(completed_at=RECENT)
    legacy = JobExecution(status="success", completed_at=OLD)
    linked = execution()
    running = execution(state="compensating")
    attention = execution(state="needs_attention")
    unknown = execution(journal={"plans": [{"batches": [{"state": "dispatched"}]}]})
    pending = execution(journal={"runtime": {"terminal_finalization": {"state": "succeeded", "done": False}}})
    unfinished = execution(completed_at=None)
    legacy_pending = JobExecution(status="running", completed_at=OLD)
    fixtures = [old, recent, legacy, linked, running, attention, unknown, pending, unfinished, legacy_pending]
    db.session.add_all(fixtures)
    db.session.flush()
    old_id, recent_id, legacy_id = (row.id for row in fixtures[:3])
    snapshot = PlaylistSnapshot(user_id=owner, playlist_id="linked", playlist_name="Linked", track_count=0,
                                track_uris_json="[]", job_execution_id=linked.id)
    db.session.add(snapshot)
    db.session.commit()

    first = cleanup(app, "--limit", "2")
    assert first == {"examined": 2, "compacted": 1, "deleted": 0, "next_after_id": recent_id}
    assert db.session.get(JobExecution, legacy_id) is not None
    assert cleanup(app, "--after-id", str(recent_id)) == {
        "examined": 8, "compacted": 0, "deleted": 1, "next_after_id": None,
    }
    assert db.session.get(JobExecution, legacy_id) is None
    assert db.session.get(JobExecution, old_id).journal == {"result": {"success": True}}
    assert old.payload == {}
    for row in (recent, linked, running, attention, unfinished):
        assert row.journal == original
    assert snapshot.job_execution_id == linked.id
    assert unknown.journal["plans"][0]["batches"][0]["state"] == "dispatched"
    assert pending.journal["runtime"]["terminal_finalization"]["done"] is False
    assert legacy_pending.status == "running"
    assert cleanup(app) == {"examined": 9, "compacted": 0, "deleted": 0, "next_after_id": None}


def test_pending_undo_and_snapshot_restore_replay_survive_compaction(accounts):
    owner, alice, _, spotify = accounts
    settings = UserSettings.query.filter_by(user_id=owner).one()
    settings.auto_snapshot_enabled = False
    snapshot = PlaylistSnapshot(user_id=owner, playlist_id="target", playlist_name="Target", track_count=1)
    snapshot.track_uris = [uri(3)]
    db.session.add(snapshot)
    db.session.commit()
    path = f"/api/snapshots/{snapshot.id}/restore"
    headers = {"Idempotency-Key": "restore-snapshot"}
    restored = post_and_run(alice, spotify, path, headers=headers)
    assert restored.operation["state"] == "succeeded"
    op_id = restored.operation["id"]
    age(op_id)
    before = OperationRepository().get(op_id)
    assert alice.delete(f"/api/snapshots/{snapshot.id}").status_code == 200
    assert cleanup(alice.application)["compacted"] == 0  # latest confirmed history for this browser scope
    undo = alice.post("/api/undo/target")
    assert undo.status_code == 202
    assert cleanup(alice.application)["compacted"] == 0
    assert OperationRepository().get(op_id).journal == before.journal
    assert run_operation(alice, spotify, undo.json["operation_id"], undo).operation["state"] == "succeeded"
    assert cleanup(alice.application)["compacted"] == 1  # Undo now owns the confirmed entry
    assert "plans" not in OperationRepository().get(op_id).journal
    assert cleanup(alice.application)["compacted"] == 0
    replay = post_and_run(alice, spotify, path, headers=headers)
    assert replay.operation == alice.get(restored.accepted.headers["Location"]).json["operation"]
    assert replay.operation["id"] == op_id and replay.get_json() == restored.get_json()
    changed = alice.post("/api/snapshots/999/restore", headers=headers)
    assert changed.status_code == 409 and changed.json["error_code"] == "request_key_conflict"
    assert spotify.writes == [("update", "target", [uri(3)]), ("update", "target", [uri(2)])]


def test_review_restore_and_child_replay_after_cleanup(restoration, sample_token, monkeypatch):
    browser = client(restoration, sample_token)
    base = f"/api/operations/{restoration.parent_id}"
    age(restoration.parent_id)
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: restoration.factory(restoration.user))
    assert cleanup(restoration.app)["compacted"] == 0
    reviewed = browser.get(base + "/restore-context").json["context"]["reviewed"]
    options = {"json": {"reviewed": reviewed}, "headers": {"Idempotency-Key": "reviewed-child"}}
    accepted = browser.post(base + "/restore", **options)
    assert accepted.status_code == 202
    assert cleanup(restoration.app)["compacted"] == 0
    child_id = accepted.json["operation_id"]
    done, row = restoration.run_restore(restoration.repository.get(child_id))
    assert done and row.state == "succeeded"
    age(child_id)
    parent = restoration.repository.get(restoration.parent_id)
    assert cleanup(restoration.app)["compacted"] == 0
    assert restoration.repository.get(child_id).journal == row.journal
    assert restoration.repository.get(parent.id).journal == parent.journal
    assert browser.get(base).json["operation"]["recovery"]["resolved_operation_id"] == child_id
    replay = browser.post(base + "/restore", **options)
    assert replay.status_code == 202 and replay.json["operation_id"] == child_id
    assert len(restoration.world.writes) == 1


def test_compacted_scheduled_fire_replays_after_pause(routine):
    schedule = Schedule(user_id=routine.user.id, job_type="shuffle", target_playlist_id="target",
                        algorithm_name="BasicShuffle", schedule_type="interval", schedule_value="daily", timezone="UTC")
    db.session.add_all([schedule, UserSettings(user_id=routine.user.id, auto_snapshot_enabled=False)])
    db.session.commit()
    args = (routine.repository, schedule.id, build_cadence(schedule).fingerprint, OLD)
    fire = admit_scheduled(*args)
    row = routine.run(routine.repository.get(fire.operation_id))
    assert row.state == "succeeded" and "plans" in row.journal
    schedule.is_enabled = False
    db.session.commit()
    age(row.id)
    assert cleanup(routine.app)["compacted"] == 1
    assert "plans" not in routine.repository.get(row.id).journal
    assert admit_scheduled(*args).operation_id == row.id
    assert JobExecution.query.count() == len(routine.world.writes) == 1
