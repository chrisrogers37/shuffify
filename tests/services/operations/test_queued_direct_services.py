"""Prepared direct services retain original before/history across lost step output."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from flask.sessions import SecureCookieSession

from shuffify.services.live_playlist_service import LivePlaylistService
from shuffify.spotify.api import SpotifyAPI
from tests.services.operations.test_crash_recovery import Crash, World, uri


class Runtime:
    def __init__(self, world, history=None):
        self.world, self.journal = world, world.journal()
        self.history = SecureCookieSession(deepcopy(history or {}))
        self.user, self.operation = SimpleNamespace(id=1, spotify_id='owner'), SimpleNamespace(id=1)
        self.lose_step = None
        self.local_calls = []

    def prepare(self, name, calculate):
        document = deepcopy(self.world.saved or {})
        values = document.setdefault('runtime', {}).setdefault('prepared', {})
        if name not in values:
            values[name] = json.loads(json.dumps(calculate()))
            self.world.checkpoint(document)
        return deepcopy(values[name])

    def step(self, name, execute):
        document = deepcopy(self.world.saved)
        values = document.setdefault('runtime', {}).setdefault('steps', {})
        if name in values:
            end = document['runtime']['ranges'][name]
            self.journal.skip_to(end)
            return deepcopy(values[name])
        result = execute()
        self.journal.check_pending_control()
        if self.lose_step == name:
            raise Crash()
        document = deepcopy(self.world.saved)
        document.setdefault('runtime', {}).setdefault('steps', {})[name] = json.loads(json.dumps(result))
        document['runtime'].setdefault('ranges', {})[name] = self.journal.cursor
        self.world.checkpoint(document)
        return result

    def local_step(self, name, callback):
        self.local_calls.append(name)
        return None

    def protect_verified(self, name, public_result):
        self.verified_result = deepcopy(public_result)

    def fail_if_verified(self, error):
        if hasattr(self, 'verified_result'):
            from shuffify.services.operations.runtime import VerifiedStepError

            raise VerifiedStepError('direct.publication.v1', {'public_result': self.verified_result}) from error


def api_for(world):
    api = object.__new__(SpotifyAPI)
    api._cache, api._user_id = None, 'owner'

    def metadata(target, **kwargs):
        state = world.playlists[target]
        return {'id': target, 'owner': {'id': 'owner'}, 'items': {'total': len(state['track_uris'])},
                **deepcopy(state['metadata']), 'snapshot_id': state['snapshot_id']}

    def items(target, limit=50, offset=0):
        values = world.playlists[target]['track_uris']
        return {'offset': offset, 'total': len(values), 'next': None,
                'items': [{'added_at': '2026-01-01T00:00:00Z',
                           'track': {'uri': value, 'name': value, 'artists': [], 'type': 'track'}}
                          for value in values[offset:offset + limit]]}

    api.get_playlist, api.get_playlist_items_raw = metadata, items
    return api


def test_lost_target_step_output_keeps_frozen_shuffle_and_original_before_history(monkeypatch):
    world = World([uri(1), uri(2), uri(1)])
    world.playlists['target']['metadata']['description'] = None
    api, runtime = api_for(world), Runtime(world)
    calls = []
    desired = [uri(2), uri(1), uri(1)]

    def algorithm(*args, **kwargs):
        calls.append(args)
        return desired

    monkeypatch.setattr('shuffify.services.live_playlist_service.ShuffleService.execute', algorithm)
    runtime.lose_step = 'direct.target.v1'
    with runtime.journal.observe(), pytest.raises(Crash):
        LivePlaylistService(api).shuffle('target', runtime.user, runtime.history,
                                        algorithm='BasicShuffle', params={}, runtime=runtime)
    writes = deepcopy(world.writes)
    recovered = Runtime(world)
    with recovered.journal.observe():
        assert recovered.journal.resume()['state'] == 'succeeded'
        result = LivePlaylistService(api).shuffle('target', recovered.user, recovered.history,
                                                  algorithm='BasicShuffle', params={}, runtime=recovered)
    assert result['success'] is True
    assert len(calls) == 1
    assert world.writes == writes
    assert recovered.history['playlist_states']['target'] == {
        'states': [[uri(1), uri(2), uri(1)], desired], 'current_index': 1,
    }
    assert recovered.local_calls == ['direct.snapshot.v1', 'direct.activity.v1']


def test_visibility_uses_only_metadata_and_replays_an_acknowledged_change_once():
    from shuffify.services.live_metadata_service import LiveMetadataService
    from shuffify.services.operations.journal import DurableJournal

    world = World([uri(1)])
    world.playlists['target']['metadata']['description'] = None
    runtime = Runtime(world)
    api = api_for(world)

    def metadata(resource):
        state = world.playlists[resource]
        return {'metadata': deepcopy(state['metadata']), 'snapshot_id': state['snapshot_id']}

    def forbidden(*args, **kwargs):
        pytest.fail('Visibility must not read playlist items')

    api.get_playlist_items_raw = forbidden
    runtime.journal = DurableJournal(lambda: world.saved, world.checkpoint, forbidden, world.dispatch,
                                     read_metadata=metadata)
    runtime.lose_step = 'visibility.target.v1'
    with runtime.journal.observe(), pytest.raises(Crash):
        LiveMetadataService(api).set_visibility('target', runtime.user, public=True, runtime=runtime)
    writes = deepcopy(world.writes)
    recovered = Runtime(world)
    recovered.journal = DurableJournal(lambda: world.saved, world.checkpoint, forbidden, world.dispatch,
                                       read_metadata=metadata)
    with recovered.journal.observe():
        assert recovered.journal.resume()['state'] == 'succeeded'
        result = LiveMetadataService(api).set_visibility('target', recovered.user, public=True, runtime=recovered)
    assert result['status'] == 'success'
    assert result['public'] is True
    assert result['operation']['resources'][0]['baseline_metadata'] == {'public': False}
    assert world.writes == writes
    assert recovered.history == {}


@pytest.mark.parametrize('kind', ['undo', 'snapshot_restore'])
def test_recovered_restore_consumes_the_original_history_selection_once(kind):
    from shuffify.services.state_service import UndoSelection

    world = World([uri(1), uri(2)])
    world.playlists['target']['metadata']['description'] = None
    initial = {'playlist_states': {'target': {'states': [[uri(2), uri(1)], [uri(1), uri(2)]], 'current_index': 1}}}
    api, runtime = api_for(world), Runtime(world, initial)
    selected = UndoSelection(((uri(2), uri(1)), (uri(1), uri(2))), 1)
    snapshot = SimpleNamespace(id=17, user_id=1, playlist_id='target', track_uris=[uri(2), uri(1)],
                               to_dict=lambda: {'id': 17, 'track_uris': [uri(2), uri(1)]})

    def run(rt):
        service = LivePlaylistService(api)
        if kind == 'undo':
            return service.undo('target', rt.user, rt.history, selected=selected, runtime=rt)
        return service.restore_snapshot(snapshot, rt.user, rt.history, runtime=rt)

    runtime.lose_step = 'direct.target.v1'
    with runtime.journal.observe(), pytest.raises(Crash):
        run(runtime)
    writes = deepcopy(world.writes)
    recovered = Runtime(world, initial)
    with recovered.journal.observe():
        recovered.journal.resume()
        result = run(recovered)
    assert result['success'] is True
    assert world.writes == writes
    entry = recovered.history['playlist_states']['target']
    assert entry['states'][entry['current_index']] == [uri(2), uri(1)]
    assert entry['current_index'] == (0 if kind == 'undo' else 2)


def test_prepared_action_never_overwrites_an_external_edit_before_its_first_plan(monkeypatch):
    from shuffify.services.operations.journal import NeedsAttention

    world = World([uri(1), uri(2)])
    world.playlists['target']['metadata']['description'] = None
    api, runtime = api_for(world), Runtime(world)
    monkeypatch.setattr(
        'shuffify.services.live_playlist_service.ShuffleService.execute', lambda *a, **k: [uri(2), uri(1)])
    prepare = runtime.prepare

    def stop_after_prepare(*args):
        prepare(*args)
        raise Crash()

    runtime.prepare = stop_after_prepare
    with runtime.journal.observe(), pytest.raises(Crash):
        LivePlaylistService(api).shuffle('target', runtime.user, runtime.history,
                                        algorithm='BasicShuffle', params={}, runtime=runtime)
    world.playlists['target'].update(track_uris=[uri(99)], snapshot_id='external')
    prior = deepcopy(world.playlists)
    recovered = Runtime(world)
    with recovered.journal.observe(), pytest.raises(NeedsAttention) as attention:
        LivePlaylistService(api).shuffle('target', recovered.user, recovered.history,
                                        algorithm='BasicShuffle', params={}, runtime=recovered)
    assert world.playlists == prior
    assert world.writes == []
    assert attention.value.result['operation']['resources'][0]['desired_track_uris'] == [uri(2), uri(1)]


@pytest.mark.parametrize('kind', ['shuffle', 'undo'])
def test_no_write_cases_keep_the_existing_history_semantics(kind, monkeypatch):
    from shuffify.services.state_service import UndoSelection

    world = World([uri(1), uri(2)])
    world.playlists['target']['metadata']['description'] = None
    initial = {} if kind == 'shuffle' else {'playlist_states': {'target': {
        'states': [[uri(1), uri(2)], [uri(2), uri(1)]], 'current_index': 1,
    }}}
    api, runtime = api_for(world), Runtime(world, initial)
    monkeypatch.setattr(
        'shuffify.services.live_playlist_service.ShuffleService.execute', lambda *a, **k: [uri(1), uri(2)])
    with runtime.journal.observe():
        service = LivePlaylistService(api)
        if kind == 'shuffle':
            result = service.shuffle('target', runtime.user, runtime.history,
                                     algorithm='BasicShuffle', params={}, runtime=runtime)
        else:
            selected = UndoSelection(((uri(1), uri(2)), (uri(2), uri(1))), 1)
            result = service.undo('target', runtime.user, runtime.history, selected=selected, runtime=runtime)
    assert result['success'] is True
    assert world.writes == []
    assert runtime.local_calls == []
    if kind == 'shuffle':
        assert runtime.history == {}
        assert runtime.history.modified is False
    else:
        assert runtime.history['playlist_states']['target']['current_index'] == 0


@pytest.mark.parametrize('kind', ['shuffle', 'visibility'])
def test_real_http_rate_limit_is_not_swallowed_or_cached_as_a_failed_service_result(kind, monkeypatch):
    from shuffify.services.live_metadata_service import LiveMetadataService
    from shuffify.services.operations.journal import DurableJournal, OperationDeferred
    from shuffify.spotify.http_client import SpotifyHTTPClient

    world = World([uri(1), uri(2)])
    world.playlists['target']['metadata']['description'] = None
    api, runtime = api_for(world), Runtime(world)
    client = SpotifyHTTPClient('fixture')
    calls = []
    client._session.request = lambda *a, **k: calls.append(a) or SimpleNamespace(
        status_code=429, headers={'Retry-After': '120'},
    )

    def metadata(target):
        return {'metadata': deepcopy(world.playlists[target]['metadata']),
                'snapshot_id': world.playlists[target]['snapshot_id']}
    runtime.journal = DurableJournal(lambda: world.saved, world.checkpoint, world.read,
        lambda method, path, body: client._request(method, path, json=body), read_metadata=metadata)
    monkeypatch.setattr(
        'shuffify.services.live_playlist_service.ShuffleService.execute', lambda *a, **k: [uri(2), uri(1)])
    with runtime.journal.observe(), pytest.raises(OperationDeferred):
        if kind == 'shuffle':
            LivePlaylistService(api).shuffle('target', runtime.user, runtime.history,
                                            algorithm='BasicShuffle', params={}, runtime=runtime)
        else:
            LiveMetadataService(api).set_visibility('target', runtime.user, public=True, runtime=runtime)
    assert len(calls) == 1
    assert not world.saved.get('runtime', {}).get('steps')
    assert runtime.history == {}
    before = len(world.trace)
    with pytest.raises(OperationDeferred):
        runtime.journal.resume()
    assert len(world.trace) == before


@pytest.mark.parametrize('kind', ['shuffle', 'visibility'])
def test_a_lost_acknowledgment_fence_is_not_swallowed_by_api_or_service(kind, monkeypatch):
    from shuffify.services.live_metadata_service import LiveMetadataService
    from shuffify.services.operations.journal import DurableJournal
    from shuffify.services.operations.repository import LeaseLost

    world = World([uri(1), uri(2)])
    world.playlists['target']['metadata']['description'] = None
    api, runtime = api_for(world), Runtime(world)

    def fence(document):
        if any(item['state'] == 'acknowledged' for plan in document.get('plans', []) for item in plan['batches']):
            raise LeaseLost('lease changed')
        world.checkpoint(document)

    def metadata(target):
        return {'metadata': deepcopy(world.playlists[target]['metadata']),
                'snapshot_id': world.playlists[target]['snapshot_id']}
    runtime.journal = DurableJournal(lambda: world.saved, fence, world.read, world.dispatch, read_metadata=metadata)
    monkeypatch.setattr(
        'shuffify.services.live_playlist_service.ShuffleService.execute', lambda *a, **k: [uri(2), uri(1)])
    with runtime.journal.observe(), pytest.raises(LeaseLost):
        if kind == 'shuffle':
            LivePlaylistService(api).shuffle('target', runtime.user, runtime.history,
                                            algorithm='BasicShuffle', params={}, runtime=runtime)
        else:
            LiveMetadataService(api).set_visibility('target', runtime.user, public=True, runtime=runtime)
    assert len(world.writes) == 1
    assert not world.saved.get('runtime', {}).get('steps')
    assert runtime.history == {}


def test_a_known_first_batch_rejection_retains_zero_effect_retryable_facts(monkeypatch):
    from shuffify.spotify.exceptions import SpotifyWriteRejectedError

    world = World([uri(1), uri(2)])
    world.playlists['target']['metadata']['description'] = None
    api, runtime = api_for(world), Runtime(world)
    runtime.journal.dispatch = lambda *args: (_ for _ in ()).throw(SpotifyWriteRejectedError('known rejection'))
    monkeypatch.setattr(
        'shuffify.services.live_playlist_service.ShuffleService.execute', lambda *a, **k: [uri(2), uri(1)])
    with runtime.journal.observe():
        result = LivePlaylistService(api).shuffle('target', runtime.user, runtime.history,
                                                algorithm='BasicShuffle', params={}, runtime=runtime)
    assert result['operation']['aggregate_status'] == 'partial'
    assert result['operation']['retryable'] is True
    resource = result['operation']['resources'][0]
    assert resource['dispatched'] is True
    assert resource['status'] == 'failed'
    assert resource['affected_count'] == 0
    assert world.writes == []
    assert runtime.history == {}
