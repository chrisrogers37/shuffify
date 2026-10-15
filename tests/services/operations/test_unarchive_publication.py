"""Local publication failures retain an archive move's freshly verified facts."""

import pytest
from sqlalchemy.exc import OperationalError

from shuffify.services.operations.runtime import OperationRuntime
from shuffify.services.state_service import StateService
from tests.services.operations.test_routine_commands import routine as routine  # noqa: F401
from tests.services.operations.test_unarchive_command import move as move  # noqa: F401


def _assert_retained(move, completed):
    assert completed.state == "needs_attention"
    assert completed.journal["result"]["unarchived_count"] == 3
    assert completed.journal["result"]["removed_count"] == 2
    assert len(move.world.writes) == 2
    assert all(method in {"POST", "DELETE"} for method, _, _ in move.world.writes)


def test_acceptance_storage_outage_retains_verified_move_facts(move, monkeypatch):  # noqa: F811
    local = move.repository.local_step

    def unavailable(lease, name, callback, **kwargs):
        if name == "accept.unarchive":
            raise OperationalError("SELECT receipt", {}, Exception("private storage outage"))
        return local(lease, name, callback, **kwargs)

    monkeypatch.setattr(move.repository, "local_step", unavailable)
    _assert_retained(move, move.run_move(move.enqueue_move()))


def test_history_error_and_marker_outage_retain_verified_move_facts(move, monkeypatch):  # noqa: F811
    def unavailable(*args, **kwargs):
        raise ValueError("private history failure")

    def marker_outage(*args, **kwargs):
        raise OperationalError("UPDATE marker", {}, Exception("private storage outage"))

    monkeypatch.setattr(StateService, "record_verified_change", unavailable)
    monkeypatch.setattr(OperationRuntime, "mark_history_unconfirmed", marker_outage)
    _assert_retained(move, move.run_move(move.enqueue_move()))


def test_history_is_prepared_only_after_complete_journal_verification(move, monkeypatch):  # noqa: F811
    from shuffify.services.operations.journal import DurableJournal

    verify, history = DurableJournal.verify_step, StateService.record_verified_change
    verified, histories = [], []

    def proof(self, name, **kwargs):
        result = verify(self, name, **kwargs)
        verified.append(name)
        return result

    def record(*args, **kwargs):
        assert verified == ["unarchive"]
        histories.append(True)
        return history(*args, **kwargs)

    monkeypatch.setattr(DurableJournal, "verify_step", proof)
    monkeypatch.setattr(StateService, "record_verified_change", record)
    completed = move.run_move(move.enqueue_move())
    assert completed.state == "succeeded"
    assert len(histories) == 2


@pytest.mark.parametrize("checkpoint_phase", ["reconciled", "proof"])
def test_fresh_verification_checkpoint_outage_retains_both_move_effects(move, monkeypatch, checkpoint_phase):  # noqa: F811
    from shuffify.services.operations.journal import DurableJournal

    verify, checkpoint = DurableJournal.verify_step, OperationRuntime.checkpoint
    verifying, failed = False, []

    def during_verification(self, *args, **kwargs):
        nonlocal verifying
        verifying = True
        try:
            return verify(self, *args, **kwargs)
        finally:
            verifying = False

    def unavailable(self, document):
        proof = document.get("boundaries", {}).get("current", {}).get("verified")
        if verifying and bool(proof) == (checkpoint_phase == "proof") and not failed:
            failed.append(checkpoint_phase)
            raise OperationalError("UPDATE proof", {}, Exception("private database proof outage"))
        return checkpoint(self, document)

    monkeypatch.setattr(DurableJournal, "verify_step", during_verification)
    monkeypatch.setattr(OperationRuntime, "checkpoint", unavailable)
    completed = move.run_move(move.enqueue_move())
    assert failed == [checkpoint_phase]
    _assert_retained(move, completed)


def test_lost_atomic_receipt_reply_reuses_verified_move_and_one_activity(move, monkeypatch):  # noqa: F811
    from shuffify.models.db import ActivityLog

    local, lost = move.repository.local_step, []

    def reply_lost(lease, name, callback, **kwargs):
        value = local(lease, name, callback, **kwargs)
        if name == "accept.unarchive" and not lost:
            lost.append(True)
            raise OperationalError("COMMIT acknowledgement", {}, Exception("private lost reply"))
        return value

    monkeypatch.setattr(move.repository, "local_step", reply_lost)
    completed = move.run_move(move.enqueue_move())
    assert completed.state == "succeeded"
    assert completed.journal["result"]["unarchived_count"] == 3
    assert len(move.world.writes) == 2
    assert ActivityLog.query.count() == 1
    assert lost == [True]


@pytest.mark.parametrize("control", ["lease", "deferred"])
def test_history_publication_keeps_durable_controls_typed_without_compensating(move, monkeypatch, control):  # noqa: F811
    from datetime import datetime, timedelta, timezone

    from shuffify.services.operations.commands.archive_move import unarchive
    from shuffify.services.operations.journal import OperationDeferred
    from shuffify.services.operations.repository import LeaseLost
    from shuffify.worker import OperationWorker

    error = (
        LeaseLost("fence lost")
        if control == "lease"
        else OperationDeferred(
            datetime.now(timezone.utc) + timedelta(seconds=120),
        )
    )

    def stop(*args, **kwargs):
        raise error

    monkeypatch.setattr(StateService, "record_verified_change", stop)
    row = move.enqueue_move()
    runner = OperationWorker(
        move.app,
        repository=move.repository,
        handlers={"unarchive": unarchive},
        api_factory=move.factory,
        heartbeat_interval=0,
    )
    assert runner.run_once(row.id) is False
    unfinished = move.repository.get(row.id)
    assert unfinished.state in {"running", "queued"}
    assert unfinished.completed_at is None
    assert unfinished.journal.get("compensation", []) == []
    assert len(move.world.writes) == 2
