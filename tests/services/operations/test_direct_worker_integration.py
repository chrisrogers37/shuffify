"""Real queue/runtime/history transactions drive direct Spotify API mutations."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from sqlalchemy import update

from shuffify.models.db import ActivityLog, JobExecution, PlaylistSnapshot, User, db
from shuffify.services.operations.commands.direct import shuffle, snapshot_restore, undo, visibility
from shuffify.services.operations.history import HistoryStore
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.spotify.http_client import SpotifyHTTPClient
from shuffify.worker import OperationWorker
from tests.services.operations.test_crash_recovery import Crash, World, uri
from tests.services.operations.test_queued_direct_services import api_for

SCOPE = 'direct-worker-' + 'a' * 32
HANDLERS = {'shuffle': shuffle, 'undo': undo, 'snapshot_restore': snapshot_restore, 'visibility': visibility}


@pytest.fixture
def setup(db_app):
    user = User(spotify_id='owner', display_name='Owner')
    db.session.add(user)
    db.session.commit()
    repository = OperationRepository()
    world = World([uri(1), uri(2), uri(1)])
    world.playlists['target']['metadata']['description'] = None
    algorithms = []
    return db_app, user.id, repository, world, algorithms


def worker_for(app, repository, world):
    def factory(user):
        api = api_for(world)
        client = SpotifyHTTPClient('fixture')

        def request(method, url, **kwargs):
            result = world.dispatch(method, urlsplit(url).path.removeprefix('/v1'), kwargs['json'])
            return SimpleNamespace(status_code=200, content=b'json' if result else b'', json=lambda: result)
        client._session.request = request
        api._http = client
        return api
    return OperationWorker(app, repository=repository, handlers=HANDLERS, api_factory=factory, heartbeat_interval=0)


def accept(repository, user_id, kind, command, legacy=None):
    history = HistoryStore(repository.engine).capture(user_id, SCOPE, ['target'], legacy=legacy)
    return repository.enqueue(kind=kind, user_id=user_id, targets=['target'], payload={
        'version': 1, 'command': {'playlist_id': 'target', **command}, 'history': history,
    })


def expire(operation_id):
    db.session.execute(update(JobExecution).where(JobExecution.id == operation_id).values(
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    ))
    db.session.commit()


@pytest.mark.parametrize('cut', ['prepared', 'target_output', 'history_output', 'history_checkpoint'])
def test_shuffle_crashes_reuse_one_frozen_order_and_one_confirmed_history_receipt(setup, monkeypatch, cut):
    app, user_id, repository, world, algorithms = setup
    desired = [uri(2), uri(1), uri(1)]

    def calculate(*args, **kwargs):
        algorithms.append(args)
        return desired
    monkeypatch.setattr('shuffify.services.live_playlist_service.ShuffleService.execute', calculate)
    accepted = accept(repository, user_id, 'shuffle', {'algorithm': 'BasicShuffle', 'params': {}})
    runner = worker_for(app, repository, world)
    prepare, step = OperationRuntime.prepare, OperationRuntime.step
    stopped = []

    def crash_prepare(runtime, name, calculate):
        value = prepare(runtime, name, calculate)
        if cut == 'prepared' and not stopped:
            stopped.append(True)
            raise Crash()
        return value

    def crash_step(runtime, name, execute):
        if cut in {'target_output', 'history_output'} and name == {
            'target_output': 'direct.target.v1', 'history_output': 'direct.history.v1',
        }[cut] and not stopped:
            def interrupt():
                execute()
                stopped.append(True)
                raise Crash()
            return step(runtime, name, interrupt)
        value = step(runtime, name, execute)
        if cut == 'history_checkpoint' and name == 'direct.history.v1' and not stopped:
            stopped.append(True)
            raise Crash()
        return value
    monkeypatch.setattr(OperationRuntime, 'prepare', crash_prepare)
    monkeypatch.setattr(OperationRuntime, 'step', crash_step)
    with pytest.raises(Crash):
        runner.run_once(accepted.id)
    assert repository.get(accepted.id).state == 'running'
    expire(accepted.id)
    assert runner.run_once(accepted.id) is True
    finished = repository.get(accepted.id)
    assert finished.state == 'succeeded'
    assert len(algorithms) == 1
    assert len(world.writes) == 1
    assert world.playlists['target']['track_uris'] == desired
    entry, receipt = HistoryStore(repository.engine).load(user_id, SCOPE, 'target')
    assert receipt == accepted.id
    assert entry == {'states': [[uri(1), uri(2), uri(1)], desired], 'current_index': 1}
    assert db.session.query(JobExecution).count() == 1
    assert db.session.query(ActivityLog).count() == 1
    snapshots = db.session.query(PlaylistSnapshot).all()
    assert len(snapshots) == 1
    assert snapshots[0].job_execution_id == accepted.id


def test_visibility_worker_has_no_track_reader_and_does_not_publish_history(setup):
    app, user_id, repository, world, _ = setup
    accepted = accept(repository, user_id, 'visibility', {'public': True})
    runner = worker_for(app, repository, world)
    factory = runner.api_factory

    def metadata_only(user):
        api = factory(user)
        api.get_playlist_items_raw = lambda *a, **k: pytest.fail('Visibility read track items')
        return api
    runner.api_factory = metadata_only
    assert runner.run_once(accepted.id) is True
    row = repository.get(accepted.id)
    assert row.state == 'succeeded'
    assert row.journal['resources']['target']['scope'] == 'metadata'
    assert row.journal['result']['public'] is True
    assert world.playlists['target']['track_uris'] == [uri(1), uri(2), uri(1)]
    assert 'history' not in row.journal


@pytest.mark.parametrize('kind', ['undo', 'snapshot_restore'])
def test_restores_finish_the_selected_history_without_an_extra_execution(setup, kind):
    app, user_id, repository, world, _ = setup
    desired = [uri(2), uri(1), uri(1)]
    legacy = {'target': {'states': [desired, list(world.playlists['target']['track_uris'])], 'current_index': 1}}
    command = {'selection': legacy['target']} if kind == 'undo' else {'snapshot': {
        'id': 17, 'user_id': user_id, 'playlist_id': 'target', 'track_uris': desired,
    }}
    accepted = accept(repository, user_id, kind, command, legacy=legacy)
    assert worker_for(app, repository, world).run_once(accepted.id) is True
    row = repository.get(accepted.id)
    assert row.state == 'succeeded'
    assert world.playlists['target']['track_uris'] == desired
    assert len(world.writes) == 1
    entry, receipt = HistoryStore(repository.engine).load(user_id, SCOPE, 'target')
    assert receipt == accepted.id
    assert entry['current_index'] == (0 if kind == 'undo' else 2)
    assert db.session.query(JobExecution).count() == 1


def test_stale_review_remains_a_rich_no_effect_worker_failure(setup, monkeypatch):
    app, user_id, repository, world, _ = setup
    monkeypatch.setattr('shuffify.services.live_playlist_service.ShuffleService.execute',
                        lambda *a, **k: pytest.fail('A stale review ran the algorithm'))
    accepted = accept(repository, user_id, 'shuffle', {
        'algorithm': 'BasicShuffle', 'params': {}, 'reviewed': {'reviewed_scope': '0' * 64},
    })
    assert worker_for(app, repository, world).run_once(accepted.id) is True
    row = repository.get(accepted.id)
    assert row.state == 'failed'
    assert row.journal['result']['operation']['error_code'] == 'review_changed'
    assert world.writes == []
    assert db.session.query(PlaylistSnapshot).count() == db.session.query(ActivityLog).count() == 0


def test_history_confirmation_failure_keeps_verified_spotify_and_publishes_no_receipt(setup, monkeypatch):
    app, user_id, repository, world, _ = setup
    desired = [uri(2), uri(1), uri(1)]
    monkeypatch.setattr('shuffify.services.live_playlist_service.ShuffleService.execute', lambda *a, **k: desired)

    def unavailable(*args, **kwargs):
        raise RuntimeError('history unavailable')
    monkeypatch.setattr('shuffify.services.state_service.StateService.record_verified_change', unavailable)
    accepted = accept(repository, user_id, 'shuffle', {'algorithm': 'BasicShuffle', 'params': {}})
    assert worker_for(app, repository, world).run_once(accepted.id) is True
    row = repository.get(accepted.id)
    assert row.state == 'needs_attention'
    assert row.journal['runtime']['history_unconfirmed'] is True
    assert row.journal['result']['operation']['error_code'] == 'history_confirmation_failed'
    assert world.playlists['target']['track_uris'] == desired
    assert len(world.writes) == 1
    assert HistoryStore(repository.engine).load(user_id, SCOPE, 'target') == (None, None)


def test_a_real_429_defers_the_worker_without_a_false_step_result_or_history(setup, monkeypatch):
    app, user_id, repository, world, _ = setup
    monkeypatch.setattr('shuffify.services.live_playlist_service.ShuffleService.execute',
                        lambda *a, **k: [uri(2), uri(1), uri(1)])
    accepted = accept(repository, user_id, 'shuffle', {'algorithm': 'BasicShuffle', 'params': {}})
    runner = worker_for(app, repository, world)
    factory, attempts = runner.api_factory, []

    def limited(user):
        api = factory(user)
        api._http._session.request = lambda *a, **k: attempts.append(a) or SimpleNamespace(
            status_code=429, headers={'Retry-After': '120'},
        )
        return api
    runner.api_factory = limited
    assert runner.run_once(accepted.id) is False
    row = repository.get(accepted.id)
    assert row.state == 'running'
    assert row.journal['plans'][0]['batches'][0]['state'] == 'rejected'
    assert not row.journal.get('runtime', {}).get('steps')
    assert len(attempts) == 1
    expire(accepted.id)  # Even an eager reclaim must obey the provider's durable deadline.
    runner.api_factory = lambda user: pytest.fail('Deferred recovery constructed Spotify')
    assert runner.run_once(accepted.id) is False
    assert len(attempts) == 1
    assert world.writes == []
    assert HistoryStore(repository.engine).load(user_id, SCOPE, 'target') == (None, None)


def test_lost_ack_fence_is_reconciled_without_repeating_the_http_write(setup, monkeypatch):
    from shuffify.services.operations.repository import LeaseLost

    app, user_id, repository, world, _ = setup
    desired = [uri(2), uri(1), uri(1)]
    monkeypatch.setattr('shuffify.services.live_playlist_service.ShuffleService.execute', lambda *a, **k: desired)
    accepted = accept(repository, user_id, 'shuffle', {'algorithm': 'BasicShuffle', 'params': {}})
    checkpoint, stopped = repository.checkpoint, []

    def fence(lease, document, **kwargs):
        if not stopped and any(batch['state'] == 'acknowledged'
                               for plan in document.get('plans', []) for batch in plan['batches']):
            stopped.append(True)
            raise LeaseLost('reclaimed')
        return checkpoint(lease, document, **kwargs)
    monkeypatch.setattr(repository, 'checkpoint', fence)
    runner = worker_for(app, repository, world)
    assert runner.run_once(accepted.id) is False
    assert repository.get(accepted.id).state == 'running'
    assert len(world.writes) == 1
    expire(accepted.id)
    assert runner.run_once(accepted.id) is True
    assert repository.get(accepted.id).state == 'succeeded'
    assert len(world.writes) == 1
    entry, _ = HistoryStore(repository.engine).load(user_id, SCOPE, 'target')
    assert entry['states'] == [[uri(1), uri(2), uri(1)], desired]


def test_metadata_noop_recovery_does_not_report_stale_visibility_after_an_external_edit(setup, monkeypatch):
    app, user_id, repository, world, _ = setup
    accepted = accept(repository, user_id, 'visibility', {'public': False})
    original, stopped = OperationRuntime.prepare, []

    def interrupt(runtime, name, calculate):
        result = original(runtime, name, calculate)
        if name == 'visibility.action.v1' and not stopped:
            stopped.append(True)
            raise Crash()
        return result
    monkeypatch.setattr(OperationRuntime, 'prepare', interrupt)
    runner = worker_for(app, repository, world)
    with pytest.raises(Crash):
        runner.run_once(accepted.id)
    world.playlists['target']['metadata']['public'] = True
    world.playlists['target']['snapshot_id'] = 'external-edit'
    expire(accepted.id)
    assert runner.run_once(accepted.id) is True
    row = repository.get(accepted.id)
    assert row.state == 'needs_attention'
    assert row.journal['result']['success'] is False
    assert row.journal['result']['operation']['resources'][0]['current_metadata'] == {'public': True}
    assert world.playlists['target']['metadata']['public'] is True
    assert world.writes == []
