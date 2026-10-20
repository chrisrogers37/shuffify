"""Frozen Collect/Drip decisions use real journal and HTTP mutation observation."""

import importlib
import json
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from shuffify.services.operations.journal import NeedsAttention, OperationDeferred
from shuffify.services.source_resolver.base import ResolveResult
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import SpotifyAPIError
from shuffify.spotify.http_client import SpotifyHTTPClient
from tests.services.operations.test_crash_recovery import Crash, World, uri


class Runtime:
    def __init__(self, world):
        self.world, self.journal = world, world.journal()
        self.user = SimpleNamespace(id=1, spotify_id='owner')
        self.lease = SimpleNamespace(targets=tuple(world.playlists))
        self.api = object.__new__(SpotifyAPI)
        self.api._cache, self.api._user_id = None, None
        self.api.get_tracks = lambda values: [{'uri': value, 'name': value, 'artists': []} for value in values]
        self.http = SpotifyHTTPClient('fixture')

        def request(method, url, **kwargs):
            result = world.dispatch(method, urlsplit(url).path.removeprefix('/v1'), kwargs['json'])
            return SimpleNamespace(status_code=200, content=b'json' if result else b'', json=lambda: result)

        self.http._session.request = request
        self.journal.dispatch = lambda method, path, body: self.http._request(method, path, json=body)

    def prepare(self, name, calculate):
        document = deepcopy(self.world.saved or {})
        prepared = document.setdefault('runtime', {}).setdefault('prepared', {})
        if name not in prepared:
            prepared[name] = json.loads(json.dumps(calculate(), allow_nan=False))
            self.world.checkpoint(document)
        return deepcopy(prepared[name])

    def get_journal(self):
        return deepcopy(self.world.saved or {})

    def read_resource(self, resource):
        return self.world.read(resource)

    def check_pending_control(self):
        self.journal.check_pending_control()


@pytest.fixture
def world():
    value = World([uri(1)])
    for name, values in [('collection', [uri(2)]), ('archive', [uri(3)])]:
        value.playlists[name] = deepcopy(value.playlists['target'])
        value.playlists[name]['track_uris'] = values
    return value


def modules():
    return tuple(importlib.import_module('shuffify.services.operations.executors.' + name) for name in ('raid', 'drip'))


def test_collect_excludes_all_resources_and_historical_pending_before_sampling_and_keeps_first_provenance(
    world, monkeypatch,
):
    raid, _ = modules()
    calls, pools = [], []

    class Resolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, source, api):
            calls.append(source.source_playlist_id or source.search_query)
            values = [uri(index) for index in range(1, 8)] if source.id == 1 else [uri(6), uri(8)]
            return ResolveResult(values, 'fixture', True)

    monkeypatch.setattr(raid, 'SourceResolver', Resolver)

    def sample(values, count):
        pools.append(list(values))
        return list(reversed(values))[:count]

    monkeypatch.setattr(raid.random, 'sample', sample)
    sources = (raid.RaidSource(1, 'own', 'first', 'First', raid_count=2),
               raid.RaidSource(2, 'search_query', None, 'Search', search_query='query', raid_count=5))
    config = raid.RaidConfig(1, 'target', 'collection', 'archive', sources=sources,
                             extra_source_playlist_ids=('extra',), pending_track_uris=(uri(4),))
    runtime = Runtime(world)
    with runtime.journal.observe():
        prepared = raid.prepare_raid(runtime, config, prepare_key='raid.v1')
        assert world.writes == []
        applied = raid.execute_raid(runtime, config, prepare_key='raid.v1')
    assert calls == ['first', 'query', 'extra']
    assert pools == [[uri(5), uri(6), uri(7)]]
    assert prepared['choices']['selected_uris'] == [uri(7), uri(6), uri(8)]
    assert world.playlists['collection']['track_uris'] == [uri(2), uri(7), uri(6), uri(8)]
    tracks = applied['local_actions']['pending_stage']['tracks']
    assert next(track for track in tracks if track['uri'] == uri(6))['source_name'] == 'First'
    assert applied['result']['tracks_added'] == 3
    assert applied['result']['tracks_total'] == 1
    assert applied['before']['collection'] == prepared['before']['collection']


@pytest.mark.parametrize('collection', ['collection', None])
def test_partial_source_results_retain_healthy_contributions_as_an_acceptable_partial_step(
    world, monkeypatch, collection,
):
    raid, _ = modules()

    class Resolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, source, api):
            return ResolveResult([uri(5)] if source.id == 1 else [uri(6)], 'fixture', source.id == 1,
                                 partial=source.id == 2, error_message='private provider details')

    monkeypatch.setattr(raid, 'SourceResolver', Resolver)
    config = raid.RaidConfig(1, 'target', collection, sources=(raid.RaidSource(1, 'own', 'first', 'First'),
                                                             raid.RaidSource(2, 'own', 'second', 'Second')))
    runtime = Runtime(world)
    with runtime.journal.observe():
        applied = raid.execute_raid(runtime, config, prepare_key='raid.v1')
    assert applied['result']['success'] is False
    assert applied['result']['aggregate_status'] == 'partial'
    assert applied['result']['error_code'] == 'source_resolution_failed'
    assert applied['choices']['selected_uris'] == [uri(5), uri(6)]
    assert applied['local_actions']['source_tracking'][1]['status'] == 'partial'
    assert applied['local_actions']['activities'][0]['metadata']['source_id'] == 2
    assert 'private provider details' not in str(applied['result'])
    assert len(world.writes) == (1 if collection else 0)


def test_collect_exclusion_read_failure_stops_before_source_resolution_or_mutation(world, monkeypatch):
    raid, _ = modules()
    config = raid.RaidConfig(1, 'target', 'collection', 'archive',
                             sources=(raid.RaidSource(1, 'own', 'first', 'First'),))
    runtime = Runtime(world)
    read = runtime.read_resource
    runtime.read_resource = lambda resource: (_ for _ in ()).throw(ValueError('unavailable')) if resource == 'archive' \
        else read(resource)
    monkeypatch.setattr(raid, 'SourceResolver', lambda **kwargs: pytest.fail('Do not resolve without exclusions'))
    with pytest.raises(ValueError, match='unavailable'):
        raid.prepare_raid(runtime, config, prepare_key='raid.v1')
    assert world.writes == []


def test_collect_rechecks_read_only_exclusion_inputs_before_appending_anything(world, monkeypatch):
    raid, _ = modules()

    class Resolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, source, api):
            return ResolveResult([uri(5)], 'fixture', True)

    monkeypatch.setattr(raid, 'SourceResolver', Resolver)
    config = raid.RaidConfig(1, 'target', 'collection', 'archive',
                             sources=(raid.RaidSource(1, 'own', 'first'),))
    runtime = Runtime(world)
    raid.prepare_raid(runtime, config, prepare_key='raid.v1')
    world.playlists['archive'].update(track_uris=[uri(99)], snapshot_id='external')
    with runtime.journal.observe(), pytest.raises(NeedsAttention):
        raid.execute_raid(runtime, config, prepare_key='raid.v1')
    assert world.writes == []


def test_collect_allows_a_collection_archive_alias_without_losing_its_exclusion(world, monkeypatch):
    raid, _ = modules()
    monkeypatch.setattr(raid, 'SourceResolver', lambda **kwargs: SimpleNamespace(
        resolve=lambda source, api: ResolveResult([uri(2), uri(5)], 'fixture', True),
    ))
    config = raid.RaidConfig(1, 'target', 'collection', 'collection',
                             sources=(raid.RaidSource(1, 'own', 'first'),))
    runtime = Runtime(world)
    with runtime.journal.observe():
        result = raid.execute_raid(runtime, config, prepare_key='raid.v1')
    assert set(result['before']) == {'target', 'collection'}
    assert result['choices']['selected_uris'] == [uri(5)]
    assert world.playlists['collection']['track_uris'] == [uri(2), uri(5)]


def staged_by_collect(raid, world, monkeypatch, values, get_tracks):
    """The tracks a Collect stages for `values`, with the metadata lookup replaced by `get_tracks`."""
    monkeypatch.setattr(raid, 'SourceResolver', lambda **kwargs: SimpleNamespace(
        resolve=lambda source, api: ResolveResult(values, 'fixture', True),
    ))
    config = raid.RaidConfig(1, 'target', 'collection',
                             sources=(raid.RaidSource(1, 'own', 'first', 'First', raid_count=100),))
    runtime = Runtime(world)
    runtime.api.get_tracks = get_tracks
    with runtime.journal.observe():
        prepared = raid.prepare_raid(runtime, config, prepare_key='raid.v1')
    return prepared['local_actions']['pending_stage']['tracks']


def test_collect_looks_up_metadata_for_only_the_first_cap_and_stages_the_rest_by_uri(world, monkeypatch):
    raid, _ = modules()
    cap = raid.MAX_METADATA_LOOKUPS
    values = [uri(100 + index) for index in range(cap + 10)]
    lookups = []

    def get_tracks(batch):
        lookups.append(list(batch))
        return [{'uri': value, 'name': 'Looked up', 'artists': []} for value in batch]

    staged = staged_by_collect(raid, world, monkeypatch, values, get_tracks)
    assert lookups == [values[:cap]]
    assert [track['uri'] for track in staged] == values
    assert all(track['name'] == 'Looked up' for track in staged[:cap])
    assert all('name' not in track for track in staged[cap:])
    assert all(track['source_name'] == 'First' for track in staged)


def test_collect_stages_every_track_by_uri_when_the_metadata_lookup_fails(world, monkeypatch):
    raid, _ = modules()
    values = [uri(100), uri(101)]

    def get_tracks(batch):
        raise SpotifyAPIError('down')

    staged = staged_by_collect(raid, world, monkeypatch, values, get_tracks)
    assert staged == [{'uri': value, 'source_playlist_id': 'first', 'source_name': 'First'} for value in values]


def test_drip_samples_once_before_existing_target_filter_and_removes_every_selected_occurrence(world, monkeypatch):
    _, drip = modules()
    world.playlists['collection']['track_uris'] = [uri(1), uri(2), uri(2), uri(3)]
    calls = []

    def sample(values, count):
        calls.append((list(values), count))
        return [uri(1), uri(2)]

    monkeypatch.setattr(drip.random, 'sample', sample)
    config = drip.DripConfig(1, 'target', 'collection', drip_count=2, drip_enabled=True,
                             pending_rows=(drip.PendingRow(11, uri(2), 'pending'),))
    runtime = Runtime(world)
    with runtime.journal.observe():
        prepared = drip.prepare_drip(runtime, config, prepare_key='drip.v1')
        assert world.writes == []
        applied = drip.execute_drip(runtime, config, prepare_key='drip.v1')
    assert calls == [([uri(1), uri(2), uri(2), uri(3)], 2)]
    assert prepared['choices']['sampled_uris'] == [uri(1), uri(2)]
    assert applied['choices']['selected_uris'] == [uri(2)]
    assert world.playlists['target']['track_uris'] == [uri(2), uri(1)]
    assert world.playlists['collection']['track_uris'] == [uri(1), uri(3)]
    assert [(method, path) for method, path, _ in world.writes] == [
        ('POST', '/playlists/target/items'), ('DELETE', '/playlists/collection/items'),
    ]
    assert applied['local_actions']['pending_promotion']['rows'] == [
        {'id': 11, 'track_uri': uri(2), 'status': 'pending'},
    ]
    assert applied['local_actions']['lock_reconcile']['track_uris'] == [uri(2), uri(1)]


@pytest.mark.parametrize('force', [False, True])
def test_saved_drip_gate_and_explicit_force_are_distinct(world, force):
    _, drip = modules()
    runtime = Runtime(world)
    config = drip.DripConfig(1, 'target', 'collection', drip_enabled=False, force=force)
    with runtime.journal.observe():
        applied = drip.execute_drip(runtime, config, prepare_key='drip.v1')
    assert len(world.writes) == (2 if force else 0)
    assert applied['result'].get('skipped_reason') == (None if force else 'drip_disabled')


def test_drip_replay_reuses_selection_and_does_not_repeat_an_acknowledged_target_write(world, monkeypatch):
    _, drip = modules()
    world.playlists['collection']['track_uris'] = [uri(index) for index in range(2, 10)]
    count = []
    monkeypatch.setattr(drip.random, 'sample', lambda values, total: count.append(total) or values[:total])
    config = drip.DripConfig(1, 'target', 'collection', drip_count=3, force=True)
    runtime = Runtime(world)
    dispatch = world.dispatch

    def crash_remove(method, path, body):
        if method == 'DELETE':
            raise Crash()
        return dispatch(method, path, body)

    world.dispatch = crash_remove
    with runtime.journal.observe(), pytest.raises(Crash):
        drip.execute_drip(runtime, config, prepare_key='drip.v1')
    world.dispatch = dispatch
    recovered = Runtime(world)
    with recovered.journal.observe():
        assert recovered.journal.resume()['state'] == 'succeeded'
        result = drip.execute_drip(recovered, config, prepare_key='drip.v1')
    assert count == [3]
    assert result['result']['tracks_added'] == 3
    assert sum(method == 'POST' for method, _, _ in world.writes) == 1
    assert world.playlists['target']['track_uris'] == [uri(2), uri(3), uri(4), uri(1)]


def test_drip_does_not_adopt_an_external_snapshot_with_identical_content(world):
    _, drip = modules()
    runtime = Runtime(world)
    config = drip.DripConfig(1, 'target', 'collection', force=True)
    drip.prepare_drip(runtime, config, prepare_key='drip.v1')
    world.playlists['target']['snapshot_id'] = 'external-edit-and-restore'
    with runtime.journal.observe(), pytest.raises(NeedsAttention):
        drip.execute_drip(runtime, config, prepare_key='drip.v1')
    assert world.writes == []


def test_deferred_target_add_is_not_swallowed_and_does_not_start_collection_cleanup(world):
    _, drip = modules()
    runtime = Runtime(world)
    runtime.http._session.request = lambda *args, **kwargs: SimpleNamespace(
        status_code=429, headers={'Retry-After': '120'}, content=b'{}', json=lambda: {},
    )
    config = drip.DripConfig(1, 'target', 'collection', force=True)
    with runtime.journal.observe(), pytest.raises(OperationDeferred):
        drip.execute_drip(runtime, config, prepare_key='drip.v1')
    assert world.writes == []
    before = deepcopy(world.trace)
    recovered = Runtime(world)
    with recovered.journal.observe(), pytest.raises(OperationDeferred):
        drip.execute_drip(recovered, config, prepare_key='drip.v1')
    assert world.trace == before


def test_every_multi_batch_drip_http_crash_reuses_sampling_and_preserves_duplicate_order(monkeypatch):
    _, drip = modules()
    sampled = []
    monkeypatch.setattr(drip.random, 'sample', lambda values, count: sampled.append(count) or values[:count])
    pool = [uri(2), uri(2)] + [uri(index) for index in range(3, 103)]
    config = drip.DripConfig(1, 'target', 'collection', drip_count=101, force=True)

    def build():
        value = World([uri(1)])
        value.playlists['collection'] = deepcopy(value.playlists['target'])
        value.playlists['collection']['track_uris'] = list(pool)
        return value

    def execute(value):
        runtime = Runtime(value)
        try:
            with runtime.journal.observe():
                if value.saved and value.saved.get('plans'):
                    assert runtime.journal.resume()['state'] == 'succeeded'
                return drip.execute_drip(runtime, config, prepare_key='drip.v1')
        finally:
            runtime.http.close()

    probe = build()
    execute(probe)
    cuts = [index for index, label in enumerate(probe.trace, 1) if label.startswith('spotify.')]
    assert sum(label == 'spotify.before' for label in probe.trace) == 4
    for cut in cuts:
        sampled.clear()
        value = build()
        value.cut = cut
        with pytest.raises(Crash):
            execute(value)
        value.cut = None
        result = execute(value)
        assert sampled == [101]
        assert result['choices']['selected_uris'] == pool[:101]
        assert value.playlists['target']['track_uris'] == pool[:101] + [uri(1)]
        assert value.playlists['collection']['track_uris'] == [uri(102)]
        assert sum(method == 'POST' for method, _, _ in value.writes) == 2
