"""Accepted domain steps retain their Spotify and local effects during a later rollback."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from shuffify.services.operations.journal import JournalError, NeedsAttention, OperationAborted, OperationDeferred
from shuffify.spotify.api import SpotifyAPI
from tests.services.operations.test_crash_recovery import World, uri


def api():
    client = object.__new__(SpotifyAPI)
    client._cache, client._user_id = None, None
    return client


def accept(world, journal, name, result=None, receipt=None):
    proof = journal.verify_step(name)
    document = deepcopy(world.saved)
    if receipt is not None:
        document.setdefault('runtime', {}).setdefault('local_steps', {})[receipt] = deepcopy(result)
    world.checkpoint(journal.accept_step_document(document, name, proof=proof, result=result))
    journal.reload_latest()


@pytest.mark.parametrize('kind', ['add', 'remove'])
def test_ensure_add_and_remove_bind_complete_baselines_and_replay_the_current_cursor(kind):
    world = World([uri(1), uri(1), uri(2)])
    expected = world.read('target')
    journal, client = world.journal(), api()
    ensure = getattr(journal, 'ensure_' + kind)
    values = [uri(3), uri(3)] if kind == 'add' else [uri(1)]
    options = {'position': 1} if kind == 'add' else {}
    with journal.observe():
        ensure('target', values, expected_baseline=expected, **options)
        getattr(client, 'playlist_' + kind + '_items')('target', values, **options)
    written = deepcopy(world.writes)
    recovered = world.journal()
    with recovered.observe():
        getattr(recovered, 'ensure_' + kind)('target', values, expected_baseline=expected, **options)
        getattr(client, 'playlist_' + kind + '_items')('target', values, **options)
    assert world.writes == written
    expected_order = [uri(1), uri(3), uri(3), uri(1), uri(2)] if kind == 'add' else [uri(2)]
    assert world.playlists['target']['track_uris'] == expected_order


def test_ensure_create_reuses_the_returned_identity_and_never_posts_again():
    world, client = World(), api()
    with world.journal().observe() as journal:
        journal.ensure_create('Frozen name', public=False, description='Frozen description')
        result = client.create_user_playlist('Frozen name', public=False, description='Frozen description')
    with world.journal().observe() as recovered:
        recovered.ensure_create('Frozen name', public=False, description='Frozen description')
        assert client.create_user_playlist('Frozen name', public=False, description='Frozen description') == result
    assert result['id'] == 'created-1'
    assert len(world.writes) == 1


def test_acceptance_builder_is_pure_and_a_later_step_rollback_preserves_collect():
    world = World([uri(1), uri(2)])
    world.playlists['production'] = deepcopy(world.playlists['target'])
    world.playlists['production']['track_uris'] = [uri(9)]
    client, journal = api(), world.journal()
    original = deepcopy(world.playlists)
    with journal.observe():
        journal.begin_step('collect')
        journal.ensure_add('target', [uri(3), uri(3)], expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(3), uri(3)])
        accept(world, journal, 'collect', {'staged_ids': [11, 12]}, receipt='collect.stage')
        collected = deepcopy(world.playlists)
        journal.begin_step('drip')
        journal.ensure_add('production', [uri(3)], position=0, expected_baseline=world.read('production'))
        client.playlist_add_items('production', [uri(3)], position=0)
        journal.ensure_remove('target', [uri(3)], expected_baseline=world.read('target'))
        client.playlist_remove_items('target', [uri(3)])
        world.saved['runtime']['local_steps']['drip.promote'] = {'ids': [11]}
        assert journal.compensation_has_effects() is True
        assert journal.compensation_scope(world.saved)['local_steps'] == ['drip.promote']
        with pytest.raises(OperationAborted):
            journal.request_abort('drip_failed')
        assert journal.compensate()['state'] == 'rolled_back'
    assert world.playlists['target']['track_uris'] == collected['target']['track_uris']
    assert world.playlists['production']['track_uris'] == original['production']['track_uris']
    assert world.saved['resources']['target']['baseline']['track_uris'] == original['target']['track_uris']
    assert world.saved['runtime']['local_steps']['collect.stage'] == {'staged_ids': [11, 12]}
    assert journal.accepted_step('collect')['result'] == {'staged_ids': [11, 12]}
    assert journal.compensation_scope()['retained_steps'] == ['collect']


def test_a_failure_after_all_steps_are_accepted_never_rolls_back_an_accepted_effect():
    world, client = World([uri(1)]), api()
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        journal.ensure_add('target', [uri(2)], expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(2)])
        accept(world, journal, 'collect', {'ids': [11]}, receipt='collect.stage')
        assert journal.compensation_has_effects() is False
        writes = deepcopy(world.writes)
        with pytest.raises(OperationAborted):
            journal.request_abort('later_bookkeeping_failure')
        assert journal.compensate()['state'] == 'failed'
    assert world.writes == writes
    assert world.playlists['target']['track_uris'] == [uri(1), uri(2)]
    assert world.saved['runtime']['local_steps']['collect.stage'] == {'ids': [11]}


def test_external_edit_after_acceptance_needs_attention_without_offering_an_accepted_effect_restore():
    world, client = World([uri(1)]), api()
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        journal.ensure_add('target', [uri(2)], expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(2)])
        accept(world, journal, 'collect', {'ids': [11]}, receipt='collect.stage')
    world.playlists['target'].update(track_uris=[uri(99)], snapshot_id='external')
    writes = deepcopy(world.writes)
    outcome = journal.compensate()
    assert outcome['state'] == 'needs_attention'
    assert outcome['recovery']['available'] is False
    assert outcome['recovery']['action'] is None
    assert journal.restore_plan()['resources'] == []
    assert world.playlists['target']['track_uris'] == [uri(99)]
    assert world.writes == writes
    assert journal.accepted_step('collect')['result'] == {'ids': [11]}


def test_acceptance_refuses_unfinished_plans_and_stale_verification_proof_without_writes():
    world = World([uri(1)])
    journal = world.journal()
    journal.begin_step('collect')
    journal.ensure_add('target', [uri(2)], expected_baseline=world.read('target'))
    with pytest.raises(JournalError):
        journal.verify_step('collect')
    assert world.writes == []
    journal.resume()
    proof = journal.verify_step('collect')
    document = deepcopy(world.saved)
    document['plans'][0]['desired']['track_uris'].append(uri(99))
    before = deepcopy(document)
    with pytest.raises(JournalError):
        journal.accept_step_document(document, 'collect', proof=proof)
    assert document == before


@pytest.mark.parametrize('status', ['failed', 'needs_attention'])
def test_atomic_acceptance_cannot_publish_a_blocked_step_even_with_its_original_proof(status):
    world, journal = World(), None
    journal = world.journal()
    journal.begin_step('collect')
    proof = journal.verify_step('collect')
    document = deepcopy(world.saved)
    document['status'] = status
    before = deepcopy(document)
    with pytest.raises(JournalError):
        journal.accept_step_document(document, 'collect', proof=proof, result={'ids': []})
    assert document == before
    assert world.writes == []


def test_begin_replays_accepted_results_and_cannot_replace_an_unaccepted_step():
    world, journal = World(), None
    journal = world.journal()
    journal.begin_step('collect')
    accept(world, journal, 'collect', {'staged_ids': []}, receipt='collect.stage')
    assert journal.begin_step('collect')['state'] == 'accepted'
    assert journal.begin_step('collect')['result'] == {'staged_ids': []}
    assert journal.begin_step('drip')['state'] == 'active'
    with pytest.raises(JournalError):
        journal.begin_step('shuffle')


@pytest.mark.parametrize('kind', ['add', 'remove'])
def test_a_later_step_cannot_overwrite_an_observed_edit_to_an_accepted_resource(kind):
    world, client = World([uri(1)]), api()
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        journal.ensure_add('target', [uri(2)], expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(2)])
        accept(world, journal, 'collect')
        journal.begin_step('drip')
        expected = world.read('target')
        world.playlists['target'].update(track_uris=[uri(99)], snapshot_id='external')
        writes = deepcopy(world.writes)
        with pytest.raises(NeedsAttention):
            getattr(journal, 'ensure_' + kind)('target', [uri(2)], expected_baseline=expected)
    assert world.writes == writes
    assert world.playlists['target']['track_uris'] == [uri(99)]
    assert world.saved['resources']['target']['baseline']['track_uris'] == [uri(1)]


def test_reload_adopts_atomic_acceptance_preserving_the_observer_and_cursor():
    from shuffify.spotify.observation import current_observer

    world, client = World([uri(1)]), api()
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        journal.ensure_add('target', [uri(2)], expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(2)])
        proof = journal.verify_step('collect')
        document = deepcopy(world.saved)
        document.setdefault('runtime', {}).setdefault('local_steps', {})['collect.stage'] = {'ids': [11]}
        document['runtime']['history_working'] = {'playlist_states': {'target': {'states': [[]], 'current_index': 0}}}
        world.checkpoint(journal.accept_step_document(document, 'collect', proof=proof, result={'ids': [11]}))
        with pytest.raises(JournalError):
            journal._save()
        journal.reload_latest()
        assert current_observer() is journal
        assert journal.cursor == 1
        assert journal._observation_depth == 1
        prior = deepcopy(world.trace)
        result = journal.begin_step('collect')
        assert world.trace == prior
        result['result']['ids'].append(99)
        assert journal.accepted_step('collect')['result'] == {'ids': [11]}
        assert journal.doc['runtime']['history_working'] == document['runtime']['history_working']
        assert journal.begin_step('drip')['start'] == 1


def test_stale_new_resource_mutation_cannot_mask_an_atomically_published_revision():
    world, client = World([uri(1)]), api()
    world.playlists['production'] = deepcopy(world.playlists['target'])
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        journal.ensure_add('target', [uri(2)], expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(2)])
        proof = journal.verify_step('collect')
        document = deepcopy(world.saved)
        document.setdefault('runtime', {}).setdefault('local_steps', {})['accept.collect'] = {'ids': [11]}
        world.checkpoint(journal.accept_step_document(document, 'collect', proof=proof, result={'ids': [11]}))
        published = deepcopy(world.saved)
        with pytest.raises(JournalError, match='reloading'):
            journal.ensure_add('production', [uri(3)], expected_baseline=world.read('production'))
        assert world.saved == published
        assert world.saved['boundaries']['accepted'][0]['name'] == 'collect'
        assert world.saved['runtime']['local_steps']['accept.collect'] == {'ids': [11]}
        journal.reload_latest()
        journal.begin_step('drip')
        journal.ensure_add('production', [uri(3)], expected_baseline=world.read('production'))
        client.playlist_add_items('production', [uri(3)])
    assert world.playlists['production']['track_uris'] == [uri(1), uri(3)]


def test_an_accepted_creation_is_retained_when_a_later_metadata_step_is_compensated():
    world, client = World(), api()
    journal = world.journal()
    with journal.observe():
        journal.begin_step('create')
        journal.ensure_create('Frozen playlist', public=False)
        created = client.create_user_playlist('Frozen playlist', public=False)
        accept(world, journal, 'create', created, receipt='create.result')
        journal.begin_step('details')
        before = world.read(created['id'])
        journal.ensure_details(created['id'], expected_baseline=before, description='Temporary')
        client.update_playlist_details(created['id'], description='Temporary')
        with pytest.raises(OperationAborted):
            journal.request_abort('metadata_failed')
        assert journal.compensate()['state'] == 'rolled_back'
    assert sum(path == '/me/playlists' for _, path, _ in world.writes) == 1
    assert created['id'] in world.playlists
    assert world.playlists[created['id']]['metadata']['description'] == ''
    assert journal.accepted_step('create')['result']['id'] == created['id']


def test_unknown_creation_after_an_accepted_step_never_posts_again_or_erases_accepted_results():
    from tests.services.operations.test_crash_recovery import Crash

    world, client = World(), api()
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        accept(world, journal, 'collect', {'ids': [11]}, receipt='collect.stage')
        journal.begin_step('create')
        journal.ensure_create('Frozen playlist')
        dispatch = world.dispatch

        def lost_response(*args):
            dispatch(*args)
            raise Crash()
        journal.dispatch = lost_response
        with pytest.raises(Crash):
            client.create_user_playlist('Frozen playlist')
    recovered = world.journal()
    assert recovered.resume()['state'] == 'needs_attention'
    assert recovered.compensate()['state'] == 'needs_attention'
    assert len(world.writes) == 1
    assert recovered.accepted_step('collect')['result'] == {'ids': [11]}
    assert recovered.restore_plan()['unresolved_creates'] == [0]


def test_compensation_still_checks_accepted_resources_that_it_must_not_write():
    from tests.services.operations.test_crash_recovery import Crash

    world, client = World([uri(1)]), api()
    world.playlists['aaa-production'] = deepcopy(world.playlists['target'])
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        journal.ensure_add('target', [uri(2)], expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(2)])
        accept(world, journal, 'collect')
        journal.begin_step('drip')
        journal.ensure_add('aaa-production', [uri(3)], expected_baseline=world.read('aaa-production'))
        client.playlist_add_items('aaa-production', [uri(3)])
        with pytest.raises(OperationAborted):
            journal.request_abort('drip_failed')
        journal.dispatch = lambda *args: (_ for _ in ()).throw(Crash())
        with pytest.raises(Crash):
            journal.compensate()
    world.playlists['target'].update(track_uris=[uri(99)], snapshot_id='external')
    writes = deepcopy(world.writes)
    before = deepcopy(world.playlists)
    assert world.journal().resume()['state'] == 'needs_attention'
    assert world.playlists == before
    assert world.writes == writes


def test_scoped_restore_plan_does_not_offer_to_undo_an_accepted_collect():
    world, client = World([uri(1)]), api()
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        journal.ensure_add('target', [uri(2)], expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(2)])
        accept(world, journal, 'collect')
        assert journal.restore_plan()['resources'] == []
        journal.begin_step('drip')
        journal.ensure_remove('target', [uri(2)], expected_baseline=world.read('target'))
        client.playlist_remove_items('target', [uri(2)])
    assert journal.restore_plan()['resources'][0]['desired']['track_uris'] == [uri(1), uri(2)]


@pytest.mark.parametrize('rollback', [False, True])
def test_retry_deadline_survives_crash_without_reconciling_or_touching_accepted_effects(rollback):
    world, client = World([uri(1)]), api()
    journal = world.journal()
    with journal.observe():
        journal.begin_step('collect')
        journal.ensure_add('target', [uri(2)], expected_baseline=world.read('target'))
        client.playlist_add_items('target', [uri(2)])
        accept(world, journal, 'collect', {'ids': [11]}, receipt='collect.stage')
        journal.begin_step('drip')
        journal.ensure_add('target', [uri(3)], expected_baseline=world.read('target'))
        if rollback:
            client.playlist_add_items('target', [uri(3)])
            with pytest.raises(OperationAborted):
                journal.request_abort('drip_failed')

        def reject(method, path, body):
            ticket = journal.before_http(method, path, body)
            journal.after_http(ticket, SimpleNamespace(status_code=429, headers={'Retry-After': '120'}))

        journal.dispatch = reject
        with pytest.raises(OperationDeferred):
            journal.compensate() if rollback else client.playlist_add_items('target', [uri(3)])

    recovered = world.journal()
    assert recovered.compensation_has_effects() is rollback
    before, writes, trace = deepcopy(world.playlists), deepcopy(world.writes), deepcopy(world.trace)
    for action in (recovered.resume, recovered.compensate, lambda: recovered.verify_step('drip'),
                   lambda: recovered.begin_step('later')):
        with pytest.raises(OperationDeferred):
            action()
    assert world.trace == trace
    assert world.playlists == before
    assert world.writes == writes
    assert recovered.accepted_step('collect')['result'] == {'ids': [11]}
    assert world.saved['runtime']['local_steps']['collect.stage'] == {'ids': [11]}

    plans = world.saved['compensation'] if rollback else world.saved['plans']
    for step in plans:
        for item in step['batches']:
            if item.get('next_attempt_at'):
                item['next_attempt_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    recovered = world.journal()
    if not rollback:
        with pytest.raises(OperationAborted):
            recovered.request_abort('drip_failed')
    outcome = recovered.compensate()
    assert outcome['state'] == ('rolled_back' if rollback else 'failed')
    assert world.playlists['target']['track_uris'] == [uri(1), uri(2)]
    if not rollback:
        assert world.writes == writes
