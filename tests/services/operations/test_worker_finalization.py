"""Terminal database receipts survive crashes without replay or compensating writes."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, JobExecution, User, db
from shuffify.services.operations.history import HistoryError, HistoryStore, get_history_scope
from shuffify.services.operations.journal import OperationDeferred
from shuffify.services.operations.repository import LeaseLost, OperationRepository
from shuffify.services.operations.runtime import OperationResult, OperationRuntime
from shuffify.services.state_service import StateService
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import TokenInfo
from shuffify.worker import OperationWorker
from tests.services.operations.test_worker_journal_integration import PLAYLIST, ProcessStopped, SpotifyWorld

BEFORE = ["spotify:track:" + "1" * 22]
AFTER = ["spotify:track:" + "2" * 22]


def rich_result():
    return {"success": True, "status": "success", "tracks_added": 1, "tracks_total": 1,
            "operation": {"aggregate_status": "complete", "resources": [{"resource": PLAYLIST,
                "status": "complete", "verified": True, "dispatched": True, "affected_count": 1,
                "current_track_uris": list(AFTER), "retryable": False}]}}


def verified(runtime):
    runtime.step("verified.write", lambda: {"written": runtime.api.update_playlist_tracks(PLAYLIST, AFTER)})
    return rich_result()


def record(runtime, state, result):
    """A domain-owned state receipt updates one activity, with no forward runtime control."""
    receipts = runtime.get_journal().get("runtime", {}).get("local_steps", {})
    previous_id = next((value["activity_id"] for name, value in receipts.items()
                        if name.startswith("terminal.example.")), None)

    def database_only(storage):
        activity = storage.get(ActivityLog, previous_id) if previous_id is not None else None
        if activity is None:
            activity = ActivityLog(user_id=runtime.user.id, activity_type="schedule_run", description=state)
            storage.add(activity)
        else:
            activity.description = state
        storage.flush()
        return {"activity_id": activity.id, "state": state}

    receipt = runtime.repository.local_step(runtime.lease, "terminal.example." + state, database_only)
    return OperationResult(state, {**result, "finalization": receipt})


class Handler:
    def __init__(self, execute=verified, finalize=record):
        self.execute = execute
        self.callback = finalize
        self.executions = 0
        self.finalizations = []

    def __call__(self, runtime):
        self.executions += 1
        return self.execute(runtime)

    def finalize(self, runtime, state, result):
        self.finalizations.append((state, runtime.get_journal().get("phase")))
        return self.callback(runtime, state, result)


@pytest.fixture
def terminal(db_app, monkeypatch, sample_token):
    user = User(spotify_id="terminal-owner")
    db.session.add(user)
    db.session.commit()
    repository = OperationRepository()
    world = SpotifyWorld(BEFORE)
    factories = []

    def api_factory(_user):
        factories.append(1)
        api = SpotifyAPI(TokenInfo.from_dict(sample_token), auto_refresh=False)
        monkeypatch.setattr(api._http._session, "request", world.send)
        return api

    monkeypatch.setattr(OperationRuntime, "read_resource", lambda self, target: world.observe(target))
    monkeypatch.setattr(OperationRuntime, "read_snapshot", lambda self, target: world.observe(target)["snapshot_id"])

    def accept(payload=None):
        return repository.enqueue(kind="example", user_id=user.id, targets=[PLAYLIST], payload=payload or {})

    def build(handler):
        return OperationWorker(db_app, repository=repository, handlers={"example": handler}, api_factory=api_factory,
                               heartbeat_interval=0)

    def expire(operation_id):
        with repository.engine.begin() as connection:
            connection.execute(update(JobExecution).where(JobExecution.id == operation_id).values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))

    def activities():
        with Session(repository.engine) as storage:
            return [(row.id, row.description) for row in storage.scalars(select(ActivityLog)).all()]

    return SimpleNamespace(repository=repository, world=world, factories=factories, user_id=user.id,
                           accept=accept, build=build, expire=expire, activities=activities)


def test_success_finalizes_once_before_history_publication_and_persists_exact_counts(terminal, monkeypatch):
    handler = Handler()
    operation = terminal.accept()
    order = []
    original_stage = OperationRuntime.stage_history

    def stage(runtime, state, **kwargs):
        order.append(terminal.activities())
        return original_stage(runtime, state, **kwargs)

    monkeypatch.setattr(OperationRuntime, "stage_history", stage)
    assert terminal.build(handler).run_once(operation.id) is True
    assert terminal.build(handler).run_once(operation.id) is False
    row = terminal.repository.get(operation.id)
    assert row.state == "succeeded" and row.status == "success"
    assert row.tracks_added == row.tracks_total == 1
    assert handler.executions == 1 and [state for state, _ in handler.finalizations] == ["succeeded"]
    assert len(terminal.activities()) == 1
    assert order == [terminal.activities()]
    assert terminal.world.calls == 1


def test_finalizer_runs_after_confirmed_compensation_and_bypasses_abort_forward_controls(terminal):
    def execute(runtime):
        verified(runtime)
        runtime.journal.request_abort("example_failed", {**rich_result(), "success": False})

    def finalize(runtime, state, result):
        assert state == "rolled_back"
        assert terminal.world.uris == BEFORE
        assert runtime.get_journal()["phase"] == "compensating"
        return record(runtime, state, result)

    handler = Handler(execute, finalize)
    operation = terminal.accept()
    assert terminal.build(handler).run_once(operation.id) is True
    assert terminal.repository.get(operation.id).state == "rolled_back"
    assert terminal.world.calls == 2
    assert [state for state, _ in handler.finalizations] == ["rolled_back"]
    assert len(terminal.activities()) == 1


def test_recovered_abort_finishes_rollback_then_finalizes_without_forward_handler_replay(terminal):
    def execute(runtime):
        verified(runtime)
        runtime.journal.request_abort("example_failed", {**rich_result(), "success": False})

    handler = Handler(execute)
    operation = terminal.accept()
    terminal.world.cut = 2
    with pytest.raises(ProcessStopped):
        terminal.build(handler).run_once(operation.id)
    assert terminal.repository.get(operation.id).state == "compensating"
    terminal.world.cut = None
    terminal.expire(operation.id)
    assert terminal.build(handler).run_once(operation.id) is True
    assert handler.executions == 1
    assert [state for state, _ in handler.finalizations] == ["rolled_back"]
    assert terminal.world.uris == BEFORE and terminal.world.calls == 2


@pytest.mark.parametrize("boundary", ["candidate", "receipt", "done"])
def test_terminal_crashes_resume_database_only_without_repeated_activity(terminal, monkeypatch, boundary):
    handler = Handler()
    operation = terminal.accept()
    checkpoint = terminal.repository.checkpoint
    local_step = terminal.repository.local_step
    crashed = []

    def checkpoint_cut(lease, document, **kwargs):
        value = checkpoint(lease, document, **kwargs)
        marker = document.get("runtime", {}).get("terminal_finalization")
        matches = marker and marker["done"] is (boundary == "done")
        if boundary != "receipt" and matches and not crashed:
            crashed.append(True)
            raise ProcessStopped()
        return value

    def receipt_cut(lease, name, callback, **kwargs):
        value = local_step(lease, name, callback, **kwargs)
        if boundary == "receipt" and name.startswith("terminal.example.") and not crashed:
            crashed.append(True)
            raise ProcessStopped()
        return value

    monkeypatch.setattr(terminal.repository, "checkpoint", checkpoint_cut)
    monkeypatch.setattr(terminal.repository, "local_step", receipt_cut)
    with pytest.raises(ProcessStopped):
        terminal.build(handler).run_once(operation.id)
    assert terminal.repository.get(operation.id).completed_at is None
    assert len(terminal.activities()) == (0 if boundary == "candidate" else 1)
    terminal.expire(operation.id)

    assert terminal.build(handler).run_once(operation.id) is True
    assert terminal.repository.get(operation.id).state == "succeeded"
    assert handler.executions == 1
    assert len(terminal.activities()) == 1
    assert terminal.world.calls == 1
    assert len(handler.finalizations) == (2 if boundary == "receipt" else 1)


@pytest.mark.parametrize("done", [False, True])
def test_recovered_terminal_candidate_freshly_detects_external_edit_without_writes(terminal, monkeypatch, done):
    handler = Handler()
    operation = terminal.accept()
    original = terminal.repository.checkpoint
    stopped = []

    def cut(lease, document, **kwargs):
        value = original(lease, document, **kwargs)
        marker = document.get("runtime", {}).get("terminal_finalization")
        if marker and marker["done"] is done and not stopped:
            stopped.append(True)
            raise ProcessStopped()
        return value

    monkeypatch.setattr(terminal.repository, "checkpoint", cut)
    with pytest.raises(ProcessStopped):
        terminal.build(handler).run_once(operation.id)
    terminal.world.uris = ["spotify:track:" + "9" * 22]
    terminal.world.snapshot += 1
    terminal.expire(operation.id)
    assert terminal.build(handler).run_once(operation.id) is True
    row = terminal.repository.get(operation.id)
    assert row.state == "needs_attention"
    assert terminal.world.calls == 1
    assert terminal.world.uris == ["spotify:track:" + "9" * 22]
    assert handler.executions == 1
    assert [state for state, _ in handler.finalizations] == (["succeeded", "needs_attention"] if done
                                                          else ["needs_attention"])
    assert len(terminal.activities()) == 1
    assert terminal.activities()[0][1] == "needs_attention"
    resource = row.journal["result"]["operation"]["resources"][0]
    assert resource["status"] == "unknown" and "current_track_uris" not in resource


@pytest.mark.parametrize("expected_failure", [False, True])
def test_local_finalization_failure_retains_verified_spotify_and_never_compensates(terminal, expected_failure):
    def finalize(runtime, state, result):
        if expected_failure:
            return OperationResult("needs_attention", {**result, "success": False, "error_code": "local_record_failed"})
        raise RuntimeError("private database failure")

    handler = Handler(finalize=finalize)
    operation = terminal.accept()
    assert terminal.build(handler).run_once(operation.id) is True
    row = terminal.repository.get(operation.id)
    assert row.state == "needs_attention"
    assert terminal.world.uris == AFTER and terminal.world.calls == 1
    result = row.journal["result"]
    assert result["operation"]["resources"][0]["verified"] is True
    assert result["operation"]["resources"][0]["current_track_uris"] == AFTER
    assert "private database failure" not in str(result)
    assert result["error_code"] == ("local_record_failed" if expected_failure else "terminal_finalization_failed")


@pytest.mark.parametrize("initial,requested", [("failed", "succeeded"), ("rolled_back", "succeeded"),
                                               ("needs_attention", "succeeded"), ("succeeded", "failed"),
                                               ("failed", "rolled_back")])
def test_finalizer_cannot_upgrade_or_change_a_settled_terminal_outcome(terminal, initial, requested):
    handler = Handler(lambda runtime: OperationResult(initial, {"success": initial == "succeeded"}),
                      lambda runtime, state, result: OperationResult(requested, result))
    operation = terminal.accept()
    assert terminal.build(handler).run_once(operation.id) is True
    row = terminal.repository.get(operation.id)
    assert row.state == "needs_attention"
    assert row.journal["result"]["error_code"] == "terminal_finalization_failed"
    assert terminal.factories == []


@pytest.mark.parametrize("invalid", [None, {"success": True}, OperationResult("succeeded", []),
                                    OperationResult("succeeded", {"invalid": float("nan")})])
def test_finalizer_requires_operation_result_and_finite_json_dict(terminal, invalid):
    handler = Handler(lambda runtime: {"success": True}, lambda runtime, state, result: invalid)
    operation = terminal.accept()
    assert terminal.build(handler).run_once(operation.id) is True
    assert terminal.repository.get(operation.id).state == "needs_attention"
    assert terminal.factories == []


def test_callback_receives_detached_result_and_cannot_mutate_original_service_payload(terminal):
    original = {"success": True, "nested": {"items": [1]}}

    def finalize(runtime, state, result):
        result["nested"]["items"].append(2)
        return OperationResult(state, result)

    operation = terminal.accept()
    assert terminal.build(Handler(lambda runtime: original, finalize)).run_once(operation.id) is True
    assert original == {"success": True, "nested": {"items": [1]}}
    assert terminal.repository.get(operation.id).journal["result"]["nested"] == {"items": [1, 2]}


@pytest.mark.parametrize("control", ["lease", "defer"])
def test_finalization_controls_leave_candidate_recoverable_without_forward_replay(terminal, control):
    calls = []

    def finalize(runtime, state, result):
        calls.append(True)
        if len(calls) == 1:
            if control == "lease":
                runtime.repository.defer(runtime.lease)
                raise LeaseLost("test lease handoff")
            raise OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=120))
        return record(runtime, state, result)

    handler = Handler(finalize=finalize)
    operation = terminal.accept()
    assert terminal.build(handler).run_once(operation.id) is False
    row = terminal.repository.get(operation.id)
    assert row.completed_at is None and row.lease_owner is None
    assert row.journal["runtime"]["terminal_finalization"]["done"] is False
    terminal.expire(operation.id)
    assert terminal.build(handler).run_once(operation.id) is True
    assert handler.executions == 1 and terminal.world.calls == 1


@pytest.mark.parametrize("boundary", ["done_checkpoint", "history", "finish"])
def test_terminal_persistence_outage_never_enters_compensation(terminal, monkeypatch, boundary):
    handler = Handler()
    operation = terminal.accept()
    original_checkpoint = terminal.repository.checkpoint
    original_finish = terminal.repository.finish
    original_stage = OperationRuntime.stage_history
    failed = []

    def checkpoint(lease, document, **kwargs):
        marker = document.get("runtime", {}).get("terminal_finalization")
        if boundary == "done_checkpoint" and marker and marker["done"] and not failed:
            failed.append(True)
            raise RuntimeError("database unavailable")
        return original_checkpoint(lease, document, **kwargs)

    def finish(*args, **kwargs):
        if boundary == "finish" and not failed:
            failed.append(True)
            raise RuntimeError("database unavailable")
        return original_finish(*args, **kwargs)

    def stage(runtime, state, **kwargs):
        if boundary == "history" and not failed:
            failed.append(True)
            raise RuntimeError("database unavailable")
        return original_stage(runtime, state, **kwargs)

    monkeypatch.setattr(terminal.repository, "checkpoint", checkpoint)
    monkeypatch.setattr(terminal.repository, "finish", finish)
    monkeypatch.setattr(OperationRuntime, "stage_history", stage)
    assert terminal.build(handler).run_once(operation.id) is False
    assert terminal.repository.get(operation.id).completed_at is None
    assert terminal.world.uris == AFTER and terminal.world.calls == 1
    assert "failure" not in terminal.repository.get(operation.id).journal["runtime"]
    terminal.expire(operation.id)
    assert terminal.build(handler).run_once(operation.id) is True
    assert terminal.repository.get(operation.id).state == "succeeded"
    assert terminal.world.calls == 1 and handler.executions == 1
    assert len(terminal.activities()) == 1


def test_recovered_terminal_read_failure_cannot_fall_back_into_compensation(terminal, monkeypatch):
    handler = Handler()
    operation = terminal.accept()
    original_finish = terminal.repository.finish

    def crash(*args, **kwargs):
        raise ProcessStopped()

    monkeypatch.setattr(terminal.repository, "finish", crash)
    with pytest.raises(ProcessStopped):
        terminal.build(handler).run_once(operation.id)
    monkeypatch.setattr(terminal.repository, "finish", original_finish)
    unavailable = [True]

    def observe(runtime, playlist_id):
        if unavailable[0]:
            raise RuntimeError("read temporarily unavailable")
        return terminal.world.observe(playlist_id)

    monkeypatch.setattr(OperationRuntime, "read_resource", observe)
    terminal.expire(operation.id)
    assert terminal.build(handler).run_once(operation.id) is False
    assert terminal.repository.get(operation.id).completed_at is None
    assert "failure" not in terminal.repository.get(operation.id).journal["runtime"]
    assert terminal.world.uris == AFTER and terminal.world.calls == 1
    unavailable[0] = False
    terminal.expire(operation.id)
    assert terminal.build(handler).run_once(operation.id) is True
    assert handler.executions == 1 and len(handler.finalizations) == 1
    assert terminal.world.calls == 1


def test_history_downgrade_is_durable_before_finish_and_never_reverted_by_cached_success(terminal, monkeypatch):
    handler = Handler()
    operation = terminal.accept()
    original_finish = terminal.repository.finish
    failures = []

    def stage(runtime, state, **kwargs):
        raise HistoryError("private unavailable history")

    def finish(*args, **kwargs):
        if not failures:
            failures.append(True)
            raise ProcessStopped()
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(OperationRuntime, "stage_history", stage)
    monkeypatch.setattr(terminal.repository, "finish", finish)
    with pytest.raises(ProcessStopped):
        terminal.build(handler).run_once(operation.id)
    marker = terminal.repository.get(operation.id).journal["runtime"]["terminal_finalization"]
    assert marker["state"] == "needs_attention" and marker["done"] is True
    terminal.expire(operation.id)
    assert terminal.build(handler).run_once(operation.id) is True
    row = terminal.repository.get(operation.id)
    assert row.state == "needs_attention"
    assert row.journal["result"]["error_code"] == "history_confirmation_failed"
    assert [state for state, _ in handler.finalizations] == ["succeeded", "needs_attention"]
    assert len(terminal.activities()) == 1 and terminal.world.calls == 1


def test_crash_after_history_staging_keeps_receipt_unpublished_until_same_terminal_finish(terminal, monkeypatch):
    store, scope = HistoryStore(), get_history_scope({})
    operation = terminal.accept({"history": store.capture(terminal.user_id, scope, [PLAYLIST])})

    def execute(runtime):
        result = verified(runtime)

        def history():
            StateService.record_verified_change(runtime.history, PLAYLIST, before_uris=BEFORE, after_uris=AFTER)
            return {"recorded": True}

        runtime.step("verified.history", history)
        return result

    handler = Handler(execute)
    original_finish = terminal.repository.finish
    crashed = []

    def finish(*args, **kwargs):
        if not crashed:
            crashed.append(True)
            raise ProcessStopped()
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(terminal.repository, "finish", finish)
    with pytest.raises(ProcessStopped):
        terminal.build(handler).run_once(operation.id)
    assert "history" in terminal.repository.get(operation.id).journal
    assert store.load(terminal.user_id, scope, PLAYLIST) == (None, None)
    terminal.expire(operation.id)
    assert terminal.build(handler).run_once(operation.id) is True
    entry, revision = store.load(terminal.user_id, scope, PLAYLIST)
    assert revision == operation.id and entry["states"][-1] == AFTER
    assert handler.executions == 1 and len(handler.finalizations) == 1 and terminal.world.calls == 1


@pytest.mark.parametrize("value", [None, True, "3", 1.5, -1])
def test_function_handlers_keep_results_and_unknown_counts_are_not_fabricated(terminal, value):
    result = {"success": True, "tracks_added": value, "tracks_total": value}
    operation = terminal.accept()
    assert terminal.build(lambda runtime: deepcopy(result)).run_once(operation.id) is True
    row = terminal.repository.get(operation.id)
    assert row.journal["result"] == result
    assert row.state == "succeeded" and row.status == "success"
    assert row.tracks_added is None and row.tracks_total is None
    assert terminal.factories == []


def test_closed_drip_gate_is_failed_for_queue_and_skipped_for_legacy_history(terminal):
    result = {"success": False, "status": "skipped", "error_code": "drip_disabled", "tracks_added": 0,
              "tracks_total": 7}
    handler = Handler(lambda runtime: OperationResult("failed", result))
    operation = terminal.accept()
    assert terminal.build(handler).run_once(operation.id) is True
    row = terminal.repository.get(operation.id)
    assert row.state == "failed" and row.status == "skipped"
    assert row.tracks_added == 0 and row.tracks_total == 7


@pytest.mark.parametrize("state,error,message,expected", [
    ("failed", "The step failed.", "Failure summary.", "The step failed."),
    ("needs_attention", None, "Its local records need review.", "Its local records need review."),
    ("rolled_back", 3, "The playlist was restored.", "The playlist was restored."),
    ("succeeded", None, "Completed.", None),
])
def test_final_authored_error_is_preserved_for_legacy_execution_history(terminal, state, error, message, expected):
    result = {"success": state == "succeeded", "error": error, "message": message}
    operation = terminal.accept()
    handler = Handler(lambda runtime: OperationResult(state, result))
    assert terminal.build(handler).run_once(operation.id) is True
    assert terminal.repository.get(operation.id).error_message == expected


@pytest.mark.parametrize("state,status", [("succeeded", "skipped"), ("failed", "success"),
                                        ("rolled_back", "skipped"), ("needs_attention", "skipped")])
def test_repository_refuses_incompatible_legacy_status_override(terminal, state, status):
    operation = terminal.accept()
    lease = terminal.repository.claim(operation_id=operation.id)
    with pytest.raises(ValueError):
        terminal.repository.finish(lease, state, result={"status": "skipped", "error_code": "drip_disabled"},
                                   legacy_status=status)
    assert terminal.repository.get(operation.id).completed_at is None
