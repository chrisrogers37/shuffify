"""Verified provider effects survive failures publishing their local domain receipt."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from shuffify.models.db import ActivityLog, JobExecution, PendingRaidTrack
from shuffify.services.operations.history import HistoryStore
from shuffify.services.operations.journal import DurableJournal
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.services.state_service import StateService
from tests.services.operations.test_crash_recovery import uri
from tests.services.operations.test_finalizer_commands import cleanup as cleanup  # noqa: F401
from tests.services.operations.test_routine_commands import routine as routine  # noqa: F401


@pytest.fixture(params=["finalizer", "routine"])
def publication(request):
    if request.param == "finalizer":
        data = request.getfixturevalue("cleanup")
        row = data.enqueue_cleanup()
        label, run = "finalize_raids", data.run_cleanup
        expected = {key: list(state["track_uris"]) for key, state in data.world.playlists.items()}
        expected["collection"] = [uri(4)]
    else:
        data = request.getfixturevalue("routine")
        row, _ = data.enqueue("shuffle")
        label, run = "shuffle", data.run
        expected = {key: list(state["track_uris"]) for key, state in data.world.playlists.items()}
        expected["target"].reverse()
    return SimpleNamespace(family=request.param, data=data, row=row, label=label, run=run, expected=expected)


def _assert_verified_provider_effects(case):
    assert len(case.data.world.writes) == 1
    assert {key: state["track_uris"] for key, state in case.data.world.playlists.items()} == case.expected


def _assert_verified_result(case, result):
    if case.family == "finalizer":
        assert result["source_status"] == "complete"
    else:
        target = next(fact for fact in result["operation"]["resources"] if fact["resource"] == "target")
        assert target["status"] == "complete"
        assert target["current_track_uris"] == case.expected["target"]


def test_receipt_outage_after_verified_effects_requires_attention_without_compensation(publication, monkeypatch):
    case = publication
    original, failures = case.data.repository.local_step, []

    def unavailable(lease, name, callback, **kwargs):
        if name == "accept." + case.label and not failures:
            _assert_verified_provider_effects(case)
            failures.append(name)
            raise OperationalError("SELECT local receipt", {}, Exception("private database unavailable"))
        return original(lease, name, callback, **kwargs)

    monkeypatch.setattr(case.data.repository, "local_step", unavailable)
    completed = case.run(case.row)
    assert failures == ["accept." + case.label]
    assert completed.state == "needs_attention"
    _assert_verified_provider_effects(case)
    _assert_verified_result(case, completed.journal["result"])
    assert "private database" not in str(completed.journal["result"])
    if case.family == "finalizer":
        assert PendingRaidTrack.query.filter_by(status="pending").count() == 2
        assert ActivityLog.query.count() == 0


def test_lost_atomic_acceptance_reply_uses_proven_receipt_without_repeating_effects(publication, monkeypatch):
    case = publication
    original, failures = case.data.repository.local_step, []

    def lost_reply(lease, name, callback, **kwargs):
        value = original(lease, name, callback, **kwargs)
        if name == "accept." + case.label and not failures:
            _assert_verified_provider_effects(case)
            failures.append(name)
            raise OperationalError("COMMIT acknowledgement", {}, Exception("private connection reply lost"))
        return value

    monkeypatch.setattr(case.data.repository, "local_step", lost_reply)
    completed = case.run(case.row)
    assert failures == ["accept." + case.label]
    # The database is readable and the original transaction committed its receipt.
    # A lost return value must not turn that proven accepted effect into a failed run.
    assert completed.state == "succeeded"
    _assert_verified_provider_effects(case)
    _assert_verified_result(case, completed.journal["result"])
    assert ActivityLog.query.count() == 1
    assert JobExecution.query.count() == 1
    if case.family == "finalizer":
        assert completed.journal["result"]["promoted_count"] == 2
        assert PendingRaidTrack.query.filter_by(status="promoted").count() == 2


@pytest.mark.parametrize("checkpoint_phase", ["reconciled", "proof"])
def test_verified_proof_checkpoint_outage_retains_provider_effects(publication, monkeypatch, checkpoint_phase):
    case = publication
    verify, checkpoint = DurableJournal.verify_step, OperationRuntime.checkpoint
    verifying, failures = False, []

    def during_verification(self, *args, **kwargs):
        nonlocal verifying
        verifying = True
        try:
            return verify(self, *args, **kwargs)
        finally:
            verifying = False

    def unavailable(self, document):
        proof = document.get("boundaries", {}).get("current", {}).get("verified")
        if verifying and bool(proof) == (checkpoint_phase == "proof") and not failures:
            _assert_verified_provider_effects(case)
            failures.append(checkpoint_phase)
            raise OperationalError("UPDATE verification proof", {}, Exception("private proof publication outage"))
        return checkpoint(self, document)

    monkeypatch.setattr(DurableJournal, "verify_step", during_verification)
    monkeypatch.setattr(OperationRuntime, "checkpoint", unavailable)
    completed = case.run(case.row)
    assert failures == [checkpoint_phase]
    assert completed.state == "needs_attention"
    _assert_verified_provider_effects(case)
    _assert_verified_result(case, completed.journal["result"])
    assert "private" not in str(completed.journal["result"])


def test_routine_history_error_and_marker_outage_retain_verified_effects(routine, monkeypatch):  # noqa: F811
    row, command = routine.enqueue("drip")
    binding = HistoryStore(routine.repository.engine).capture(
        routine.user.id,
        "verified_publication_history_" + "a" * 32,
        command.reserved_targets,
    )
    assert set(binding["revisions"]) == set(command.reserved_targets)
    with routine.repository.engine.begin() as connection:
        connection.execute(
            update(JobExecution)
            .where(JobExecution.id == row.id)
            .values(
                payload={**row.payload, "history": binding},
            )
        )
    before = deepcopy(routine.world.playlists)
    history_failures, marker_failures = [], []
    checkpoint = OperationRuntime.checkpoint

    def history_unavailable(*args, **kwargs):
        history_failures.append(True)
        raise ValueError("private history record failure")

    def marker_unavailable(self, document):
        if document.get("runtime", {}).get("history_unconfirmed") and not marker_failures:
            marker_failures.append(True)
            raise OperationalError("UPDATE history marker", {}, Exception("private marker database outage"))
        return checkpoint(self, document)

    monkeypatch.setattr(StateService, "record_verified_change", history_unavailable)
    monkeypatch.setattr(OperationRuntime, "checkpoint", marker_unavailable)
    completed = routine.run(row)
    assert history_failures and marker_failures == [True]
    assert completed.state == "needs_attention"
    assert len(routine.world.writes) == 2
    assert routine.world.playlists["target"]["track_uris"] == [uri(4), uri(4)] + before["target"]["track_uris"]
    assert routine.world.playlists["collection"]["track_uris"] == []
    result = completed.journal["result"]
    assert result["tracks_added"] == 2
    target = next(fact for fact in result["operation"]["resources"] if fact["resource"] == "target")
    assert target["status"] == "complete"
    assert target["current_track_uris"] == routine.world.playlists["target"]["track_uris"]
    assert "private" not in str(result)
    for resource in command.reserved_targets:
        assert HistoryStore(routine.repository.engine).load(routine.user.id, binding["scope"], resource)[0] is None
