"""Terminal read failures settle boundedly without replaying verified writes."""
# ruff: noqa: F811

import pytest

from shuffify.services.executors.base_executor import PlaylistVerificationError
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.services.playlist_service import PlaylistAccessError
from shuffify.services.workshop_commit_service import UnsupportedPlaylistItems
from shuffify.spotify.exceptions import SpotifyAuthError, SpotifyForbiddenError, SpotifyNotFoundError
from tests.services.operations.test_composite_runtime import (
    BEFORE,  # noqa: F401
    COLLECTED,
    DRIPPED,
    PLAYLIST,
    collect,
    composite,  # noqa: F401
)
from tests.services.operations.test_worker_finalization import Handler, terminal  # noqa: F401
from tests.services.operations.test_worker_journal_integration import ProcessStopped


def pending_terminal(case, monkeypatch):
    finish = case.repository.finish

    def unavailable(*args, **kwargs):
        raise RuntimeError("Local publication unavailable")

    monkeypatch.setattr(case.repository, "finish", unavailable)
    handler = Handler()
    operation = case.accept()
    assert case.build(handler).run_once(operation.id) is False
    assert case.repository.get(operation.id).journal["runtime"]["terminal_finalization"]
    monkeypatch.setattr(case.repository, "finish", finish)
    return operation, handler


@pytest.mark.parametrize("error", [
    PlaylistAccessError, SpotifyNotFoundError, SpotifyForbiddenError, SpotifyAuthError, UnsupportedPlaylistItems,
])
def test_permanent_terminal_read_loss_releases_targets_without_replay(terminal, monkeypatch, error):
    operation, handler = pending_terminal(terminal, monkeypatch)
    writes = terminal.world.calls

    def unavailable(*args):
        raise error("Private provider detail")

    monkeypatch.setattr(OperationRuntime, "read_resource", unavailable)
    terminal.expire(operation.id)
    assert terminal.build(handler).run_once(operation.id) is True
    row = terminal.repository.get(operation.id)
    assert row.state == "needs_attention"
    assert row.journal["result"]["operation"]["resources"][0]["status"] == "unknown"
    assert "Private provider detail" not in str(row.journal["result"])
    assert handler.executions == 1 and terminal.world.calls == writes
    assert terminal.accept().id != operation.id


def test_unknown_terminal_read_failure_has_a_durable_attempt_limit(terminal, monkeypatch):
    operation, handler = pending_terminal(terminal, monkeypatch)

    def unavailable(*args):
        raise RuntimeError("Unclassified private read failure")

    monkeypatch.setattr(OperationRuntime, "read_resource", unavailable)
    for attempt in range(1, 4):
        terminal.expire(operation.id)
        completed = terminal.build(handler).run_once(operation.id)
        row = terminal.repository.get(operation.id)
        assert row.journal["runtime"]["terminal_recovery_attempts"] == attempt
        assert completed is (attempt == 3)
    assert row.state == "needs_attention"
    assert handler.executions == 1 and terminal.world.calls == 1
    assert terminal.accept().id != operation.id


def test_empty_next_boundary_cannot_inherit_previous_steps_verified_protection(composite):
    repository, operation_id, world, build, expire = composite
    interrupted = []

    def handler(runtime):
        collect(runtime)
        runtime.journal.begin_step("shuffle")
        if not interrupted:
            interrupted.append(True)
            raise ProcessStopped()

        def apply():
            runtime.step("shuffle-write", lambda: {
                "success": runtime.api.update_playlist_tracks(PLAYLIST, [DRIPPED]),
            })
            raise PlaylistVerificationError(PLAYLIST, [DRIPPED], [], None, "shuffle")

        return runtime.composite_step("shuffle", apply, lambda *_args: pytest.fail("Failed work cannot publish"))

    with pytest.raises(ProcessStopped):
        build(handler).run_once(operation_id)
    expire()
    assert build(handler).run_once(operation_id) is True
    row = repository.get(operation_id)
    assert row.state == "rolled_back"
    assert row.journal["result"]["error_code"] != "verified_step_publication_failed"
    assert world.uris == [BEFORE, COLLECTED] and world.calls == 3
    assert len(row.journal["boundaries"]["accepted"]) == 1


def test_compensation_receives_a_fresh_budget_after_forward_budget_expires(terminal, monkeypatch):
    from shuffify.spotify import deadline
    from tests.services.operations.test_worker_finalization import AFTER, BEFORE

    now = [0.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: now[0])
    compensation_reads = []

    def observe(_runtime, target):
        deadline.check_deadline()
        if now[0] > 240:
            compensation_reads.append(deadline.remaining_seconds())
        return terminal.world.observe(target)

    monkeypatch.setattr(OperationRuntime, "read_resource", observe)

    def handler(runtime):
        runtime.step("write", lambda: {"success": runtime.api.update_playlist_tracks(PLAYLIST, AFTER)})
        now[0] = 241.0
        raise ValueError("Failure after the forward budget expired")

    operation = terminal.accept()
    assert terminal.build(handler).run_once(operation.id) is True
    assert terminal.repository.get(operation.id).state == "rolled_back"
    assert terminal.world.uris == BEFORE
    assert compensation_reads and all(remaining > 0 for remaining in compensation_reads)
    assert terminal.world.calls == 2


def test_unambiguous_forward_deadline_retains_command_for_a_later_budget(terminal):
    from shuffify.spotify.deadline import SpotifyDeadlineError

    def handler(runtime):
        raise SpotifyDeadlineError("Forward budget exhausted before dispatch")

    operation = terminal.accept()
    assert terminal.build(handler).run_once(operation.id) is False
    row = terminal.repository.get(operation.id)
    assert row.state == "running" and row.completed_at is None
    assert row.lease_owner is None
    assert row.journal["read_retry"]["next_attempt_at"]
    assert terminal.world.calls == 0


def test_fresh_compensation_budget_restores_the_spent_parent_deadline(monkeypatch):
    from shuffify.spotify import deadline

    now = [0.0]
    monkeypatch.setattr(deadline.time, "monotonic", lambda: now[0])
    with deadline.spotify_deadline(1):
        now[0] = 2.0
        assert deadline.remaining_seconds() == 0
        with deadline.fresh_spotify_deadline(10):
            assert deadline.remaining_seconds() == 10
            with deadline.spotify_deadline(100):
                assert deadline.remaining_seconds() == 10
        assert deadline.remaining_seconds() == 0
    assert deadline.remaining_seconds() is None


@pytest.mark.parametrize("control", ["lease", "defer"])
def test_wrapped_terminal_controls_cannot_become_permanent_read_failure(terminal, monkeypatch, control):
    from datetime import datetime, timedelta, timezone

    from shuffify.services.operations.journal import DurableJournal, OperationDeferred
    from shuffify.services.operations.repository import LeaseLost

    operation, handler = pending_terminal(terminal, monkeypatch)
    due = datetime.now(timezone.utc) + timedelta(seconds=120)

    def unavailable(_journal):
        try:
            if control == "lease":
                raise LeaseLost("Lease changed")
            raise OperationDeferred(due)
        except (LeaseLost, OperationDeferred) as error:
            raise PlaylistAccessError("Wrapped control") from error

    monkeypatch.setattr(DurableJournal, "verify_terminal", unavailable)
    terminal.expire(operation.id)
    assert terminal.build(handler).run_once(operation.id) is False
    row = terminal.repository.get(operation.id)
    assert row.state == "running" and row.completed_at is None
    assert row.journal["runtime"].get("terminal_recovery_attempts", 0) == 0
    assert terminal.world.calls == 1 and handler.executions == 1
    if control == "defer":
        assert row.lease_owner is None and row.lease_expires_at >= due - timedelta(seconds=1)


def test_transient_terminal_deferrals_do_not_spend_the_unclassified_failure_limit(terminal, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from shuffify.services.operations.journal import DurableJournal, OperationDeferred

    operation, handler = pending_terminal(terminal, monkeypatch)
    attempts = []

    def unavailable(_journal):
        attempts.append(True)
        if len(attempts) <= 3:
            raise OperationDeferred(datetime.now(timezone.utc) + timedelta(seconds=120))
        raise RuntimeError("Unclassified failure")

    monkeypatch.setattr(DurableJournal, "verify_terminal", unavailable)
    for attempt in range(1, 7):
        terminal.expire(operation.id)
        assert terminal.build(handler).run_once(operation.id) is (attempt == 6)
        row = terminal.repository.get(operation.id)
        assert row.journal["runtime"].get("terminal_recovery_attempts", 0) == max(0, attempt - 3)
