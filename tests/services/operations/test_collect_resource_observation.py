"""Read-only exclusion resources guard recovery but never become rollback destinations."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from shuffify.services.operations.journal import NeedsAttention, OperationAborted, OperationDeferred
from tests.services.operations.test_composite_journal import accept, api
from tests.services.operations.test_crash_recovery import World, uri


def test_read_only_resource_edit_stops_recovery_without_another_http_dispatch():
    world = World([uri(1)])
    world.playlists['archive'] = deepcopy(world.playlists['target'])
    journal = world.journal()
    journal.observe_resource('archive', expected_baseline=world.read('archive'))
    journal.freeze_add('target', [uri(2)])
    journal.resume()
    world.playlists['archive'].update(track_uris=[uri(99)], snapshot_id='external')
    writes = deepcopy(world.writes)
    assert world.journal().resume()['state'] == 'needs_attention'
    assert world.writes == writes
    assert world.playlists['archive']['track_uris'] == [uri(99)]


def test_compensation_and_restore_plan_never_include_resources_without_write_plans():
    world = World([uri(1)])
    world.playlists['archive'] = deepcopy(world.playlists['target'])
    journal = world.journal()
    journal.observe_resource('archive', expected_baseline=world.read('archive'))
    assert journal.restore_plan()['resources'] == []
    journal.freeze_add('target', [uri(2)])
    journal.resume()
    assert [item['resource'] for item in journal.restore_plan()['resources']] == ['target']
    assert world.journal().compensate()['state'] == 'rolled_back'
    assert all(path != '/playlists/archive/items' for _, path, _ in world.writes)
    assert world.playlists['target']['track_uris'] == [uri(1)]


def test_observation_replay_uses_the_step_baseline_and_global_acknowledged_ledger():
    world, client = World([uri(1)]), api()
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        journal.observe_resource('target', expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(2)])
        accept(world, journal, 'collect')
        journal.begin_step('drip')
        prepared = world.read('target')
        journal.observe_resource('target', expected_baseline=prepared)
        client.playlist_add_items('target', [uri(3)])
    written = deepcopy(world.writes)
    recovered = world.journal()
    recovered.observe_resource('target', expected_baseline=prepared)
    assert world.writes == written
    assert world.saved['resources']['target']['baseline']['track_uris'] == [uri(1)]
    assert world.saved['boundaries']['current']['baselines']['target']['baseline']['track_uris'] == [uri(1), uri(2)]
    altered = deepcopy(prepared)
    altered['snapshot_id'] = 'arbitrary-live-snapshot'
    with pytest.raises(NeedsAttention):
        recovered.observe_resource('target', expected_baseline=altered)
    assert world.writes == written


@pytest.mark.parametrize('external_edit', [False, True])
def test_terminal_verification_freshly_checks_failed_aborted_done_candidates_without_dispatch(external_edit):
    world, client = World([uri(1)]), api()
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        journal.observe_resource('target', expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(2)])
        accept(world, journal, 'collect')
        with pytest.raises(OperationAborted):
            journal.request_abort('later_failed')
        assert journal.compensate()['state'] == 'failed'
    world.saved.setdefault('runtime', {})['terminal_finalization'] = {'done': True, 'state': 'failed'}
    if external_edit:
        world.playlists['target'].update(track_uris=[uri(99)], snapshot_id='external')
    written, before, trace_size = deepcopy(world.writes), deepcopy(world.playlists), len(world.trace)
    recovered = world.journal()
    outcome = recovered.verify_terminal()
    assert outcome['state'] == ('needs_attention' if external_edit else 'failed')
    assert world.playlists == before
    assert world.writes == written
    assert 'read.before' in world.trace[trace_size:]
    assert recovered.doc['phase'] == 'forward'
    assert recovered.doc['abort']['reason'] == 'later_failed'
    assert recovered.doc['runtime']['terminal_finalization']['done'] is True


def test_terminal_verification_obeys_retry_deadline_before_any_resource_read():
    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_add('target', [uri(2)])
    journal.doc['plans'][0]['batches'][0].update(
        state='rejected', next_attempt_at=(datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
    )
    journal.doc.update(status='failed', abort={'reason': 'failed', 'result': None})
    journal._save()
    trace = deepcopy(world.trace)
    with pytest.raises(OperationDeferred):
        world.journal().verify_terminal()
    assert world.trace == trace
    assert world.writes == []


def test_terminal_verification_checks_a_completed_compensation_without_restarting_it():
    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_add('target', [uri(2)])
    journal.resume()
    assert journal.compensate()['state'] == 'rolled_back'
    written, trace_size = deepcopy(world.writes), len(world.trace)
    recovered = world.journal()
    assert recovered.verify_terminal()['state'] == 'rolled_back'
    assert recovered.doc['phase'] == 'compensating'
    assert world.writes == written
    assert 'read.before' in world.trace[trace_size:]
