"""Collect partials and pending receipts share a durable accepted step, including lost replies."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from sqlalchemy import update

from shuffify.models.db import JobExecution, PendingRaidTrack, ScrapedPlaylistCache, User, db
from shuffify.services.operations.executors.collect_storage import apply_raid_local
from shuffify.services.operations.executors.raid import RaidConfig, RaidSource, execute_raid, prepare_raid
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.services.source_resolver.base import ResolveResult
from shuffify.services.source_resolver.public_scraper_pathway import ScrapeOutcome
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import TokenInfo
from shuffify.spotify.exceptions import SpotifyForbiddenError
from tests.services.operations.test_collect_drip_executors import Runtime
from tests.services.operations.test_crash_recovery import Crash, World, uri


@pytest.fixture
def accepted_runtime(db_app, sample_token, monkeypatch):
    user = User(spotify_id='collector')
    db.session.add(user)
    db.session.commit()
    world = World([uri(1)])
    world.playlists['collection'] = deepcopy(world.playlists['target'])
    world.playlists['collection']['track_uris'] = []
    repository = OperationRepository()
    row = repository.enqueue(kind='test', user_id=user.id, targets=['target', 'collection'], payload={'version': 1})
    source_calls = []

    class Resolver:
        def __init__(self, **kwargs):
            pass

        def resolve(self, source, api):
            source_calls.append(source.id)
            return ResolveResult([uri(2)], 'fixture', False, partial=True)

    monkeypatch.setattr('shuffify.services.operations.executors.raid.SourceResolver', Resolver)

    def build():
        lease = repository.claim(operation_id=row.id)
        assert lease is not None
        client = SpotifyAPI(TokenInfo.from_dict(sample_token), auto_refresh=False)
        client.get_tracks = lambda values: [{'uri': value, 'name': value} for value in values]

        def request(method, url, **kwargs):
            response = world.dispatch(method, urlsplit(url).path.removeprefix('/v1'), kwargs['json'])
            return SimpleNamespace(status_code=200, content=b'json' if response else b'', json=lambda: response)

        client._http._session.request = request
        runtime = OperationRuntime(repository, lease, api_factory=lambda _user: client)
        runtime.read_resource = world.read
        return runtime

    config = RaidConfig(user.id, 'target', 'collection', sources=(RaidSource(None, 'external', 'upstream'),))
    return repository, row.id, world, build, config, source_calls


def collect(runtime, config):
    def commit(storage, applied):
        receipt = apply_raid_local(storage, runtime.user.id, config.target_playlist_id, applied['local_actions'])
        return {**applied, 'receipt': receipt}

    return runtime.composite_step('raid', lambda: execute_raid(runtime, config, prepare_key='raid.v1'), commit)


def test_partial_healthy_contribution_is_accepted_with_exact_pending_ids(accepted_runtime):
    repository, operation_id, world, build, config, source_calls = accepted_runtime
    runtime = build()
    with runtime.journal.observe():
        result = collect(runtime, config)
    assert result['result']['success'] is False
    assert result['result']['aggregate_status'] == 'partial'
    assert result['result']['error_code'] == 'source_resolution_failed'
    ids = result['receipt']['staged_ids']
    assert ids == [PendingRaidTrack.query.one().id]
    assert world.playlists['collection']['track_uris'] == [uri(2)]
    assert len(world.writes) == 1
    assert source_calls == [None]
    document = repository.get(operation_id).journal
    assert document['boundaries']['accepted'][0]['result']['receipt']['staged_ids'] == ids
    assert document['runtime']['local_steps']['accept.raid']['result']['success'] is False


def test_optional_activity_failure_under_real_observer_cannot_rollback_required_staging(
    accepted_runtime, monkeypatch,
):
    repository, operation_id, _world, build, config, _source_calls = accepted_runtime

    def unavailable(*args, **kwargs):
        raise ValueError('activity unavailable')

    monkeypatch.setattr('shuffify.services.activity_log_service.ActivityLogService.log', unavailable)
    runtime = build()
    with runtime.journal.observe():
        result = collect(runtime, config)
    ids = result['receipt']['staged_ids']
    assert ids == [PendingRaidTrack.query.one().id]
    assert result['receipt']['activities'] == [{'status': 'failed', 'error_code': 'activity_log_failed'}]
    assert repository.get(operation_id).journal['boundaries']['accepted'][0]['result']['receipt']['staged_ids'] == ids


def test_lost_acceptance_reply_does_not_repeat_resolution_append_or_pending_staging(accepted_runtime, monkeypatch):
    repository, operation_id, world, build, config, source_calls = accepted_runtime
    local_step = repository.local_step

    def lost(*args, **kwargs):
        local_step(*args, **kwargs)
        raise Crash()

    monkeypatch.setattr(repository, 'local_step', lost)
    runtime = build()
    with runtime.journal.observe(), pytest.raises(Crash):
        collect(runtime, config)
    monkeypatch.setattr(repository, 'local_step', local_step)
    with repository.engine.begin() as connection:
        connection.execute(update(JobExecution).where(JobExecution.id == operation_id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
    recovered = build()
    with recovered.journal.observe():
        result = collect(recovered, config)
    assert result['receipt']['staged_ids'] == [PendingRaidTrack.query.one().id]
    assert len(world.writes) == 1
    assert source_calls == [None]


def test_required_stage_failure_keeps_verified_spotify_facts_for_orchestrator_attention(accepted_runtime, monkeypatch):
    repository, operation_id, world, build, config, _source_calls = accepted_runtime

    def fail(*args, **kwargs):
        raise ValueError('mandatory staging unavailable')

    monkeypatch.setattr('shuffify.services.operations.executors.collect_storage.stage_pending', fail)
    runtime = build()
    with runtime.journal.observe(), pytest.raises(ValueError, match='mandatory staging unavailable'):
        collect(runtime, config)
    document = repository.get(operation_id).journal
    assert document['boundaries']['current']['verified']['proof']
    assert document['boundaries']['accepted'] == []
    assert PendingRaidTrack.query.count() == 0
    assert world.playlists['collection']['track_uris'] == [uri(2)]
    assert len(world.writes) == 1


def test_crash_after_ack_before_local_receipt_reuses_resolution_and_exact_append(accepted_runtime, monkeypatch):
    from shuffify.services.operations.executors import collect_common

    repository, operation_id, world, build, config, source_calls = accepted_runtime
    verify = collect_common.verify_resources

    def lost(*args, **kwargs):
        verify(*args, **kwargs)
        raise Crash()

    monkeypatch.setattr(collect_common, 'verify_resources', lost)
    runtime = build()
    with runtime.journal.observe(), pytest.raises(Crash):
        collect(runtime, config)
    assert PendingRaidTrack.query.count() == 0
    monkeypatch.setattr(collect_common, 'verify_resources', verify)
    with repository.engine.begin() as connection:
        connection.execute(update(JobExecution).where(JobExecution.id == operation_id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
    recovered = build()
    with recovered.journal.observe():
        assert recovered.journal.resume()['state'] == 'succeeded'
        result = collect(recovered, config)
    assert result['receipt']['staged_ids'] == [PendingRaidTrack.query.one().id]
    assert len(world.writes) == 1
    assert source_calls == [None]


def test_required_promotion_failure_keeps_both_verified_drip_orders(accepted_runtime, monkeypatch):
    from shuffify.services.operations.executors.collect_storage import apply_drip_local
    from shuffify.services.operations.executors.drip import DripConfig, PendingRow, execute_drip

    repository, operation_id, world, build, config, _source_calls = accepted_runtime
    world.playlists['collection']['track_uris'] = [uri(2), uri(2)]
    pending = PendingRaidTrack(user_id=config.user_id, target_playlist_id='target',
                               track_uri=uri(2), track_name='Track')
    db.session.add(pending)
    db.session.commit()
    captured = DripConfig(config.user_id, 'target', 'collection', force=True,
                          pending_rows=(PendingRow(pending.id, uri(2)),))

    def fail(*args, **kwargs):
        raise ValueError('mandatory promotion unavailable')

    monkeypatch.setattr('shuffify.services.operations.executors.collect_storage.promote_pending', fail)
    runtime = build()
    with runtime.journal.observe(), pytest.raises(ValueError, match='mandatory promotion unavailable'):
        runtime.composite_step(
            'drip', lambda: execute_drip(runtime, captured, prepare_key='drip.v1'),
            lambda storage, applied: apply_drip_local(storage, config.user_id, 'target', applied['local_actions']),
        )
    document = repository.get(operation_id).journal
    assert document['boundaries']['current']['verified']['proof']
    assert world.playlists['target']['track_uris'] == [uri(2), uri(2), uri(1)]
    assert world.playlists['collection']['track_uris'] == []
    assert PendingRaidTrack.query.one().status == 'pending'
    assert len(world.writes) == 2


def test_scraper_preparation_returns_cache_receipt_without_database_mutation(db_app, monkeypatch):
    user = User(spotify_id='collector')
    db.session.add(user)
    db.session.commit()
    world = World([uri(1)])
    runtime = Runtime(world)
    runtime.user.id = user.id
    runtime.repository = SimpleNamespace(engine=db.engine)

    def forbidden(*args, **kwargs):
        raise SpotifyForbiddenError('Foreign playlist')

    runtime.api.get_playlist_tracks = forbidden
    monkeypatch.setattr('shuffify.services.source_resolver.public_scraper_pathway.PublicScraperPathway._scrape_embed',
                        lambda *args: ScrapeOutcome([uri(2)], True))
    config = RaidConfig(user.id, 'target', sources=(RaidSource(None, 'external', 'upstream'),))
    prepared = prepare_raid(runtime, config, prepare_key='raid.v1')
    assert ScrapedPlaylistCache.query.count() == 0
    assert PendingRaidTrack.query.count() == 0
    assert world.writes == []
    with runtime.journal.observe():
        result = execute_raid(runtime, config, prepare_key='raid.v1')
    assert result['local_actions']['scraper_cache'] == prepared['local_actions']['scraper_cache']
    assert ScrapedPlaylistCache.query.count() == 0
    with db.session.begin_nested():
        receipt = apply_raid_local(db.session, user.id, 'target', result['local_actions'])
    assert receipt['scraper_cache'][0]['status'] == 'complete'
    assert ScrapedPlaylistCache.query.one().track_uris == [uri(2)]
