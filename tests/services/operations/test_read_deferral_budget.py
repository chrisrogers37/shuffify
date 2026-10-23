"""Persisted safe-read outages cannot keep a target busy forever."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from shuffify.models.db import JobExecution
from shuffify.services.operations.repository import PlaylistNeedsReview
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.spotify.deadline import SpotifyDeadlineError
from shuffify.spotify.exceptions import SpotifyRateLimitError, SpotifyReadTransientError
from tests.services.operations.test_composite_runtime import composite as composite
from tests.services.operations.test_worker_finalization import AFTER, BEFORE, Handler
from tests.services.operations.test_worker_finalization import terminal as terminal
from tests.services.operations.test_worker_journal_integration import PLAYLIST, ProcessStopped

OTHER = 'B' * 22


def make_due(case, operation_id, *, first_deferred_at=None):
    """Advance the queue's persisted due time without deleting retry history."""
    document = deepcopy(case.repository.get(operation_id).journal)
    retry = document.get('read_retry')
    if retry:
        retry['next_attempt_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        if first_deferred_at is not None:
            retry['first_deferred_at'] = first_deferred_at.isoformat()
    with case.repository.engine.begin() as connection:
        connection.execute(update(JobExecution).where(JobExecution.id == operation_id).values(
            journal=document, lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))


def failing_read(_runtime, _target):
    raise SpotifyReadTransientError('Private provider outage')


def fail_journal_read(runtime, message):
    runtime.journal._read_call(
        lambda _resource: (_ for _ in ()).throw(SpotifyReadTransientError(message)), PLAYLIST,
    )


def fail_recovery_reads_after_first_effect(monkeypatch, world):
    original = OperationRuntime.read_resource

    def read(runtime, target):
        attempts = runtime.journal.doc.get('read_retry', {}).get('attempts', 0)
        if (world.calls and runtime.journal.doc.get('phase') == 'forward'
                and not runtime.journal.doc.get('compensation_retry_started')
                and attempts < 8):
            raise SpotifyReadTransientError('Private provider outage')
        return original(runtime, target)

    monkeypatch.setattr(OperationRuntime, 'read_resource', read)


def seed_seven_prior_read_failures(runtime):
    now = datetime.now(timezone.utc)
    runtime.journal.doc['read_retry'] = {
        'next_attempt_at': (now - timedelta(seconds=1)).isoformat(),
        'first_deferred_at': now.isoformat(), 'phase': runtime.journal.doc['phase'], 'attempts': 7,
        'deadline_expiries': 0, 'error_code': 'read_temporarily_unavailable',
    }
    runtime.journal._save()


def replacement(runtime):
    return runtime.step('replace', lambda: {'success': runtime.api.update_playlist_tracks(PLAYLIST, AFTER)})


def test_read_deferral_counter_and_first_time_survive_new_worker_instances(terminal, monkeypatch):
    monkeypatch.setattr(OperationRuntime, 'read_resource', failing_read)
    row = terminal.accept()
    first = None
    for attempt in range(1, 4):
        assert terminal.build(replacement).run_once(row.id) is False
        retry = terminal.repository.get(row.id).journal['read_retry']
        assert retry['attempts'] == attempt
        first = first or retry['first_deferred_at']
        assert retry['first_deferred_at'] == first
        assert datetime.fromisoformat(first).tzinfo is not None
        make_due(terminal, row.id)
    assert terminal.world.calls == 0


def test_successful_safe_reads_reset_consecutive_failure_budget_across_workers(terminal):
    from shuffify.services.operations.journal import OperationDeferred

    outcomes = [False, True] * 8 + [True]

    def handler(runtime):
        # Keep one operation alive while successive worker instances encounter
        # transient read failures separated by successful reads.
        while outcomes:
            succeeded = outcomes.pop(0)
            try:
                runtime.journal._read_call(
                    lambda _resource: (_ for _ in ()).throw(SpotifyReadTransientError("temporary"))
                    if not succeeded else {"ok": True},
                    PLAYLIST,
                )
            except OperationDeferred:
                raise
        return {"success": True}

    row = terminal.accept()
    for _ in range(9):
        finished = terminal.build(handler).run_once(row.id)
        if not finished:
            make_due(terminal, row.id)

    completed = terminal.repository.get(row.id)
    assert finished is True and completed.state == "succeeded"
    assert "read_retry" not in completed.journal or completed.journal["read_retry"].get("attempts", 0) == 0


def test_unrelated_successful_reads_cannot_reset_a_failing_reads_budget(terminal):
    def handler(runtime):
        runtime.journal._read_call(lambda _resource: {'ok': True}, OTHER)
        fail_journal_read(runtime, 'This playlist read keeps failing')

    row = terminal.accept()
    for attempt in range(1, 9):
        finished = terminal.build(handler).run_once(row.id)
        if attempt < 8:
            assert finished is False
            make_due(terminal, row.id)
    assert finished is True
    completed = terminal.repository.get(row.id)
    assert completed.state == 'failed' and completed.completed_at is not None
    assert completed.journal['result']['error_code'] == 'read_retry_exhausted'
    assert terminal.world.calls == 0
    assert terminal.accept().id != row.id


def test_reads_that_fail_in_turn_stop_deferring_at_the_phase_cap(terminal):
    runs = []

    def handler(runtime):
        # Each failing read succeeds on the next run, so no single read
        # exhausts its own budget; only the phase total can end the outage.
        runs.append(True)
        healthy, failing = (OTHER, PLAYLIST) if len(runs) % 2 else (PLAYLIST, OTHER)
        runtime.journal._read_call(lambda _resource: {'ok': True}, healthy)
        runtime.journal._read_call(
            lambda _resource: (_ for _ in ()).throw(SpotifyReadTransientError('Alternating outage')), failing,
        )

    row = terminal.accept()
    for attempt in range(1, 25):
        finished = terminal.build(handler).run_once(row.id)
        if attempt < 24:
            assert finished is False
            make_due(terminal, row.id)
    assert finished is True
    completed = terminal.repository.get(row.id)
    assert completed.state == 'failed' and completed.completed_at is not None
    assert completed.journal['result']['error_code'] == 'read_retry_exhausted'
    assert terminal.accept().id != row.id


@pytest.mark.parametrize('failure,limit', [(SpotifyReadTransientError, 8), (SpotifyDeadlineError, 3)])
def test_repeated_safe_failures_eventually_finish_and_release_the_busy_target(terminal, failure, limit):
    def unavailable(runtime):
        raise failure('Private repeated failure')

    row = terminal.accept()
    for attempt in range(1, limit + 1):
        finished = terminal.build(unavailable).run_once(row.id)
        if attempt < limit:
            assert finished is False
            make_due(terminal, row.id)
    completed = terminal.repository.get(row.id)
    assert finished is True
    assert completed.state == 'failed' and completed.completed_at is not None
    assert completed.journal['read_retry']['attempts'] == limit
    assert completed.journal['read_retry']['exhausted'] is True
    assert completed.journal['result']['error_code'] == 'read_retry_exhausted'
    assert 'Private' not in str(completed.journal['result'])
    assert terminal.world.calls == 0
    assert terminal.accept().id != row.id


def test_elapsed_first_deferral_window_is_not_reset_by_a_restart(terminal):
    def unavailable(runtime):
        raise SpotifyReadTransientError('Provider still unavailable')

    row = terminal.accept()
    assert terminal.build(unavailable).run_once(row.id) is False
    first = datetime.now(timezone.utc) - timedelta(minutes=16)
    make_due(terminal, row.id, first_deferred_at=first)
    assert terminal.build(unavailable).run_once(row.id) is True
    completed = terminal.repository.get(row.id)
    assert completed.state == 'failed' and completed.completed_at is not None
    assert completed.journal['read_retry']['first_deferred_at'] == first.isoformat()
    assert terminal.accept().id != row.id


@pytest.mark.parametrize('retry_after', [86400, 10 ** 100])
def test_a_long_retry_hint_cannot_schedule_past_the_elapsed_budget(terminal, retry_after):
    def unavailable(runtime):
        raise SpotifyRateLimitError('Long provider hint', retry_after=retry_after)

    row = terminal.accept()
    assert terminal.build(unavailable).run_once(row.id) is False
    retry = terminal.repository.get(row.id).journal['read_retry']
    assert datetime.fromisoformat(retry['next_attempt_at']) <= (
        datetime.fromisoformat(retry['first_deferred_at']) + timedelta(minutes=15)
    )


def test_exhausted_forward_reads_can_still_compensate_acknowledged_effects(terminal, monkeypatch):
    fail_recovery_reads_after_first_effect(monkeypatch, terminal.world)

    def handler(runtime):
        if not terminal.world.calls:
            replacement(runtime)
        fail_journal_read(runtime, 'Post-write read outage')

    row = terminal.accept()
    for attempt in range(1, 9):
        finished = terminal.build(handler).run_once(row.id)
        if attempt < 8:
            assert finished is False
            make_due(terminal, row.id)
    assert finished is True
    completed = terminal.repository.get(row.id)
    assert completed.state == 'rolled_back'
    assert terminal.world.uris == BEFORE and terminal.world.calls == 2
    assert terminal.accept().id != row.id


def test_successful_reconciliation_resets_retry_budget_without_rolling_back_accepted_prefix(composite):
    from tests.services.operations.test_composite_runtime import BEFORE, COLLECTED, collect

    repository, operation_id, world, build, expire = composite
    collected = []

    def handler(runtime):
        if not collected:
            collect(runtime)
            runtime.journal.begin_step('next')
            collected.append(True)
        fail_journal_read(runtime, 'The next unaccepted read is unavailable')

    for _attempt in range(1, 9):
        finished = build(handler).run_once(operation_id)
        assert finished is False
        row = repository.get(operation_id)
        document = deepcopy(row.journal)
        document['read_retry']['next_attempt_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with repository.engine.begin() as connection:
            connection.execute(update(JobExecution).where(JobExecution.id == operation_id).values(journal=document))
        expire()
    completed = repository.get(operation_id)
    assert completed.state == 'running' and completed.completed_at is None
    assert completed.journal['read_retry']['attempts'] == 1
    assert world.uris == [BEFORE, COLLECTED] and world.calls == 1
    assert len(completed.journal['boundaries']['accepted']) == 1


def test_permanent_compensation_read_outage_releases_without_unproved_rollback(terminal, monkeypatch):
    def handler(runtime):
        replacement(runtime)
        now = datetime.now(timezone.utc)
        runtime.journal.doc['read_retry'] = {
            'next_attempt_at': (now + timedelta(seconds=60)).isoformat(),
            'first_deferred_at': now.isoformat(), 'phase': 'forward', 'attempts': 7,
            'deadline_expiries': 0, 'error_code': 'read_temporarily_unavailable',
        }
        runtime.journal._save()
        raise ValueError('Start compensation after an acknowledged write')

    original = OperationRuntime.read_resource
    original_snapshot = OperationRuntime.read_snapshot

    def read(runtime, target):
        if runtime.get_journal().get('runtime', {}).get('failure'):
            raise SpotifyReadTransientError('Compensation read outage')
        return original(runtime, target)

    def read_snapshot(runtime, target):
        if runtime.get_journal().get('runtime', {}).get('failure'):
            raise SpotifyReadTransientError('Compensation read outage')
        return original_snapshot(runtime, target)

    monkeypatch.setattr(OperationRuntime, 'read_resource', read)
    monkeypatch.setattr(OperationRuntime, 'read_snapshot', read_snapshot)
    row = terminal.accept()
    for attempt in range(1, 9):
        finished = terminal.build(handler).run_once(row.id)
        if attempt == 1:
            pending = terminal.repository.get(row.id)
            assert finished is False and pending.state == 'compensating'
            assert pending.journal['read_retry']['attempts'] == 1
        if attempt < 8:
            assert finished is False
            make_due(terminal, row.id)
    assert finished is True
    completed = terminal.repository.get(row.id)
    assert completed.state == 'needs_attention'
    assert terminal.world.uris == AFTER and terminal.world.calls == 1
    assert completed.journal['result']['success'] is False
    with pytest.raises(PlaylistNeedsReview) as refused:
        terminal.accept()
    assert refused.value.operation_id == row.id


def test_terminal_read_deferrals_are_bounded_without_replaying_verified_writes(terminal, monkeypatch):
    finish = terminal.repository.finish
    monkeypatch.setattr(
        terminal.repository, 'finish', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('DB outage')),
    )
    row, handler = terminal.accept(), Handler()
    assert terminal.build(handler).run_once(row.id) is False
    monkeypatch.setattr(terminal.repository, 'finish', finish)
    monkeypatch.setattr(OperationRuntime, 'read_resource', failing_read)
    for attempt in range(1, 9):
        make_due(terminal, row.id)
        finished = terminal.build(handler).run_once(row.id)
        if attempt < 8:
            assert finished is False
    completed = terminal.repository.get(row.id)
    assert finished is True and completed.state == 'needs_attention'
    assert handler.executions == 1 and terminal.world.calls == 1
    with pytest.raises(PlaylistNeedsReview) as refused:
        terminal.accept()
    assert refused.value.operation_id == row.id


def test_exhaustion_preserves_fresh_verified_publication_protection(terminal, monkeypatch):
    verified_once = []

    def handler(runtime):
        if not verified_once:
            runtime.journal.begin_step('verified')
            replacement(runtime)
            runtime.journal.verify_step('verified')
            runtime.protect_verified('verified', {'success': True})
            verified_once.append(True)
            seed_seven_prior_read_failures(runtime)
        fail_journal_read(runtime, 'Read outage after verified publication boundary')

    row = terminal.accept()
    finished = terminal.build(handler).run_once(row.id)
    completed = terminal.repository.get(row.id)
    assert finished is True and completed.state == 'needs_attention'
    assert terminal.world.uris == AFTER and terminal.world.calls == 1
    assert not completed.journal.get('compensation')
    with pytest.raises(PlaylistNeedsReview) as refused:
        terminal.accept()
    assert refused.value.operation_id == row.id


@pytest.mark.parametrize('reason', ['read_retry_exhausted', 'domain_failed'])
def test_crash_after_abort_preserves_verified_read_exhaustion_but_keeps_domain_rollback(
    terminal, monkeypatch, reason,
):
    verified_once = []

    def handler(runtime):
        if not verified_once:
            runtime.journal.begin_step('verified')
            replacement(runtime)
            runtime.journal.verify_step('verified')
            runtime.protect_verified('verified', {'success': True})
            verified_once.append(True)
        if reason == 'read_retry_exhausted':
            seed_seven_prior_read_failures(runtime)
            fail_journal_read(runtime, 'Post-proof outage')
        runtime.journal.request_abort('domain_failed')

    row = terminal.accept()
    checkpoint, stopped = terminal.repository.checkpoint, []

    def stop_after_abort(lease, document, **kwargs):
        value = checkpoint(lease, document, **kwargs)
        if document.get('abort', {}).get('reason') == reason and not stopped:
            stopped.append(True)
            raise ProcessStopped()
        return value

    with monkeypatch.context() as patches:
        patches.setattr(terminal.repository, 'checkpoint', stop_after_abort)
        with pytest.raises(ProcessStopped):
            terminal.build(handler).run_once(row.id)
    terminal.expire(row.id)
    assert terminal.build(handler).run_once(row.id) is True
    completed = terminal.repository.get(row.id)
    if reason == 'read_retry_exhausted':
        assert completed.state == 'needs_attention'
        assert terminal.world.uris == AFTER and terminal.world.calls == 1
        assert not completed.journal.get('compensation')
        with pytest.raises(PlaylistNeedsReview) as refused:
            terminal.accept()
        assert refused.value.operation_id == row.id
    else:
        assert completed.state == 'rolled_back'
        assert terminal.world.uris == BEFORE and terminal.world.calls == 2
        assert terminal.accept().id != row.id
