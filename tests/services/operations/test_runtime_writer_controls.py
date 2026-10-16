"""Persisted aborts and history survive service catches and worker interruption."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from shuffify.models.db import JobExecution, User, db
from shuffify.services.operations.journal import OperationAborted, OperationDeferred
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.worker import OperationWorker


@pytest.fixture
def accepted(db_app):
    user = User(spotify_id="runtime-writer-user")
    db.session.add(user)
    db.session.commit()
    repository = OperationRepository()
    row = repository.enqueue(kind="example", user_id=user.id, targets=["playlist"], payload={"version": 1})
    return repository, row


def test_swallowed_abort_cannot_be_cached_as_a_successful_step(accepted):
    repository, row = accepted
    runtime = OperationRuntime(repository, repository.claim(operation_id=row.id))

    def swallowed():
        try:
            runtime.journal.request_abort("verification_failed", result={"success": False})
        except OperationAborted:
            return {"success": True}

    with pytest.raises(OperationAborted):
        runtime.step("apply", swallowed)
    assert "apply" not in repository.get(row.id).journal.get("runtime", {}).get("steps", {})


def test_recovered_abort_preserves_original_failure_without_running_handler(db_app, accepted):
    repository, row = accepted
    runtime = OperationRuntime(repository, repository.claim(operation_id=row.id))
    original = {"success": False, "error_code": "verification_failed", "message": "The save was not verified."}
    with pytest.raises(OperationAborted):
        runtime.journal.request_abort("verification_failed", result=original)
    with repository.engine.begin() as connection:
        connection.execute(update(JobExecution).where(JobExecution.id == row.id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))

    def forbidden(_runtime):
        pytest.fail("An abort recorded before process interruption must never resume forward")

    runner = OperationWorker(db_app, repository=repository, handlers={"example": forbidden}, heartbeat_interval=0)
    assert runner.run_once() is True
    finished = repository.get(row.id)
    assert finished.state == "failed"
    assert finished.journal["result"] == original


def test_worker_checks_swallowed_abort_before_interpreting_handler_success(db_app, accepted):
    repository, row = accepted
    original = {"success": False, "error_code": "verification_failed", "message": "The save was not verified."}

    def swallowed(runtime):
        try:
            runtime.journal.request_abort("verification_failed", result=original)
        except OperationAborted:
            return {"success": True}

    runner = OperationWorker(db_app, repository=repository, handlers={"example": swallowed}, heartbeat_interval=0)
    assert runner.run_once() is True
    assert repository.get(row.id).journal["result"] == original


def test_drain_continues_unrelated_ready_work_after_a_rate_limit_deferral(db_app, accepted):
    repository, first = accepted
    second = repository.enqueue(kind="other", user_id=first.user_id, targets=["other-target"], payload={})
    calls = []

    def deferred(runtime):
        calls.append(runtime.operation.id)
        raise OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=120))

    def unrelated(runtime):
        calls.append(runtime.operation.id)
        return {"success": True}

    runner = OperationWorker(db_app, repository=repository, handlers={"example": deferred, "other": unrelated},
                             heartbeat_interval=0)
    assert runner.drain() == 1
    assert calls == [first.id, second.id]
    assert repository.get(first.id).state == "queued"
    assert repository.get(second.id).state == "succeeded"
