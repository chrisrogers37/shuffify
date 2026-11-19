"""A worker owns one accepted execution and leaves interruptions recoverable."""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from shuffify.models.db import JobExecution, User, db


@pytest.fixture
def queue(db_app):
    from shuffify.services.operations.repository import OperationRepository

    user = User(spotify_id="alice", display_name="Alice")
    db.session.add(user)
    db.session.commit()
    return OperationRepository(), user.id


def worker(db_app, repository, handlers, heartbeat_interval=0):
    from shuffify.worker import OperationWorker

    return OperationWorker(db_app, repository=repository, handlers=handlers, heartbeat_interval=heartbeat_interval)


def test_worker_finishes_the_accepted_row_and_preserves_the_result(db_app, queue):
    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})
    calls = []

    def execute(runtime):
        calls.append(runtime.operation.id)
        return {"success": True, "message": "Example completed."}

    runner = worker(db_app, repository, {"example": execute})
    assert runner.run_once() is True
    assert runner.run_once() is False
    assert calls == [accepted.id]
    finished = repository.get(accepted.id)
    assert finished.state == "succeeded"
    assert finished.journal["result"]["success"] is True


def test_unknown_command_fails_without_creating_another_execution(db_app, queue):
    repository, user_id = queue
    accepted = repository.enqueue(kind="unknown", user_id=user_id, targets=["playlist-a"], payload={"version": 1})
    runner = worker(db_app, repository, {})
    assert runner.run_once() is True
    row = repository.get(accepted.id)
    assert row.state == "failed"
    assert row.journal["result"]["error_code"] == "unsupported_operation"


def test_process_interruption_keeps_a_running_row_for_recovery(db_app, queue):
    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})

    class ProcessStopped(BaseException):
        pass

    def interrupted(runtime):
        runtime.prepare("selection", lambda: {"desired": ["track-b", "track-a"]})
        raise ProcessStopped()

    runner = worker(db_app, repository, {"example": interrupted})
    with pytest.raises(ProcessStopped):
        runner.run_once()
    row = repository.get(accepted.id)
    assert row.state == "running"
    assert row.completed_at is None
    assert row.journal["runtime"]["prepared"]["selection"] == {"desired": ["track-b", "track-a"]}


def test_prepare_freezes_randomized_choices_before_the_first_effect(db_app, queue):
    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})

    def execute(runtime):
        first = runtime.prepare("selection", lambda: ["track-b", "track-a"])
        second = runtime.prepare("selection", lambda: pytest.fail("A persisted choice cannot be recomputed"))
        assert first == second == ["track-b", "track-a"]
        first.append("local-mutation")
        assert runtime.prepare("selection", lambda: None) == ["track-b", "track-a"]
        return {"success": True}

    assert worker(db_app, repository, {"example": execute}).run_once() is True
    assert repository.get(accepted.id).state == "succeeded"


def test_stop_prevents_new_claims_and_does_not_interrupt_the_active_command(db_app, queue):
    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})
    runner = worker(db_app, repository, {})
    runner.request_stop()
    assert runner.run_once() is False
    assert repository.get(accepted.id).state == "queued"

    def execute(runtime):
        running.request_stop()
        return {"success": True}

    running = worker(db_app, repository, {"example": execute})
    assert running.run_once() is True
    assert repository.get(accepted.id).state == "succeeded"
    assert running.run_once() is False


def test_worker_wakes_at_a_known_lease_expiry_without_an_external_poke(db_app, queue):
    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})
    repository.claim(operation_id=accepted.id, lease_seconds=0.08)
    calls = []

    def execute(runtime):
        calls.append(runtime.operation.id)
        runner.request_stop()
        return {"success": True}

    runner = worker(db_app, repository, {"example": execute})
    thread = threading.Thread(target=runner.serve)
    thread.start()
    try:
        thread.join(timeout=2)
        assert not thread.is_alive(), "An observed lease deadline must arrange its own wake"
        assert calls == [accepted.id]
        assert repository.get(accepted.id).state == "succeeded"
    finally:
        runner.request_stop()
        thread.join(timeout=2)


def test_recovered_compensation_never_restarts_the_original_command(db_app, queue):
    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})
    lease = repository.claim(operation_id=accepted.id)
    repository.checkpoint(lease, {"phase": "compensating", "plans": [], "compensation": []}, state="compensating")
    with repository.engine.begin() as connection:
        connection.execute(update(JobExecution).where(JobExecution.id == accepted.id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        ))

    def original_command(runtime):
        pytest.fail("Recovery must finish compensation without executing the original command")

    assert worker(db_app, repository, {"example": original_command}).run_once() is True
    assert repository.get(accepted.id).state == "rolled_back"


def test_unwritten_external_conflict_retains_needs_attention(db_app, queue):
    from shuffify.services.operations.journal import NeedsAttention

    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})

    def execute(runtime):
        runtime.checkpoint({"status": "needs_attention", "recovery": {
            "available": True, "action": "restore", "reason": "external_edit",
        }})
        raise NeedsAttention("external_edit")

    assert worker(db_app, repository, {"example": execute}).run_once() is True
    assert repository.get(accepted.id).state == "needs_attention"
    recovery = repository.get_owned(user_id, accepted.id)["recovery"]
    assert recovery["available"] is False
    assert recovery["reason"] == "operation_not_restorable"


def test_lease_loss_while_reporting_failure_never_completes_the_fenced_row(db_app, queue):
    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})

    def execute(runtime):
        repository.defer(runtime.lease)
        raise ValueError("Example failure after ownership changed")

    assert worker(db_app, repository, {"example": execute}).run_once() is False
    row = repository.get(accepted.id)
    assert row.state == "queued"
    assert row.completed_at is None


def test_idle_worker_claims_queued_work_on_timeout_without_a_notification(db_app, queue):
    from shuffify.worker import IDLE_POLL_SECONDS

    repository, user_id = queue
    runner = worker(db_app, repository, {"example": lambda runtime: {"success": True, "message": "Done."}})
    timeouts, accepted = [], []

    def idle_wait(timeout=None):
        timeouts.append(timeout)
        if len(timeouts) == 2:
            # The worker went idle; enqueue without a wake and let the wait expire.
            accepted.append(repository.enqueue(kind="example", user_id=user_id, targets=["a"], payload={"version": 1}))
        elif len(timeouts) == 3:
            runner.request_stop()
        return False

    runner._wake.wait = idle_wait
    runner._serve_waiting()
    assert timeouts[1:] == [IDLE_POLL_SECONDS] * 2
    assert repository.get(accepted[0].id).state == "succeeded"


def test_queue_loop_survives_a_database_error_and_serves_the_next_item(db_app, queue, monkeypatch):
    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})
    claim, failures = repository.claim, []

    def claim_after_one_error(**kwargs):
        if not failures:
            failures.append(True)
            raise OperationalError("SELECT next operation", {}, Exception("database unavailable"))
        return claim(**kwargs)

    def execute(runtime):
        runner.request_stop()
        return {"success": True}

    monkeypatch.setattr(repository, "claim", claim_after_one_error)
    runner = worker(db_app, repository, {"example": execute})
    wait, reported = runner._wake.wait, []

    def observed_wait(timeout=None):
        reported.append(runner._control_status().get("queue_errors"))
        return wait(timeout)

    runner._wake.wait = observed_wait
    runner._serve_waiting()
    assert reported == [0, 1]
    assert runner._control_status()["queue_errors"] == 0
    assert repository.get(accepted.id).state == "succeeded"


def test_database_error_after_the_claim_returns_the_operation_to_the_queue(db_app, queue, monkeypatch):
    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})
    get = repository.get

    def unavailable_once(operation_id):
        monkeypatch.setattr(repository, "get", get)
        raise OperationalError("SELECT operation", {}, Exception("database unavailable"))

    monkeypatch.setattr(repository, "get", unavailable_once)
    with pytest.raises(OperationalError):
        worker(db_app, repository, {}).run_once()
    assert repository.get(accepted.id).state == "queued"


def test_heartbeat_retries_a_failed_renewal_before_giving_up_the_lease(db_app, queue, monkeypatch):
    repository, user_id = queue
    accepted = repository.enqueue(kind="example", user_id=user_id, targets=["playlist-a"], payload={"version": 1})
    renewals, renewed = [], threading.Event()

    def renew(lease):
        renewals.append(lease.operation_id)
        if len(renewals) <= 2:
            raise OperationalError("UPDATE lease", {}, Exception("database unavailable"))
        renewed.set()

    def execute(runtime):
        renewed.wait(timeout=2)
        return {"success": True}

    monkeypatch.setattr(repository, "renew", renew)
    assert worker(db_app, repository, {"example": execute}, heartbeat_interval=0.01).run_once() is True
    assert renewed.is_set()
    assert repository.get(accepted.id).state == "succeeded"
