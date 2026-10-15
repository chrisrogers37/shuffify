"""Accepted child effects survive a later step's failure or interrupted receipt reply."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from shuffify.models.db import ActivityLog, JobExecution, User, db
from shuffify.services.operations.bookkeeping import OperationBookkeeping
from shuffify.services.operations.history import HistoryStore, get_history_scope
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.services.state_service import StateService
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import TokenInfo
from shuffify.worker import OperationWorker
from tests.services.operations.test_worker_journal_integration import PLAYLIST, ProcessStopped, SpotifyWorld

BEFORE = "spotify:track:" + "0" * 22
COLLECTED = "spotify:track:" + "1" * 22
DRIPPED = "spotify:track:" + "2" * 22


@pytest.fixture
def composite(db_app, monkeypatch, sample_token):
    user = User(spotify_id="alice")
    db.session.add(user)
    db.session.commit()
    repository = OperationRepository()
    row = repository.enqueue(kind="example", user_id=user.id, targets=[PLAYLIST], payload={"version": 1})
    world = SpotifyWorld([BEFORE])
    monkeypatch.setattr(OperationRuntime, "read_resource", lambda self, target: world.observe(target))
    monkeypatch.setattr(OperationRuntime, "read_snapshot", lambda self, target: world.observe(target)["snapshot_id"])

    def build(handler):
        def api_factory(_user):
            api = SpotifyAPI(TokenInfo.from_dict(sample_token), auto_refresh=False)
            monkeypatch.setattr(api._http._session, "request", world.send)
            return api

        return OperationWorker(db_app, repository=repository, handlers={"example": handler},
                               api_factory=api_factory, heartbeat_interval=0)

    def expire():
        with repository.engine.begin() as connection:
            connection.execute(update(JobExecution).where(JobExecution.id == row.id).values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ))

    return repository, row.id, world, build, expire


def collect(runtime):
    def apply():
        return runtime.step("collect-write", lambda: {
            "success": runtime.api.playlist_add_items(PLAYLIST, [COLLECTED]),
        })

    def commit(storage, result):
        activity = OperationBookkeeping(storage, runtime.user.id, runtime.operation.id).activity("raid", "Collected")
        return {**result, "activity_id": activity["id"], "collected": [COLLECTED]}

    return runtime.composite_step("collect", apply, commit)


def test_later_failure_compensates_only_the_current_step_and_preserves_accepted_local_effects(composite):
    repository, operation_id, world, build, _expire = composite

    def handler(runtime):
        first = collect(runtime)
        assert first["collected"] == [COLLECTED]

        def apply():
            runtime.step("drip-write", lambda: {"success": runtime.api.playlist_add_items(PLAYLIST, [DRIPPED])})
            raise ValueError("The later child failed")

        runtime.composite_step("drip", apply, lambda storage, result: pytest.fail("Failed work cannot commit"))

    assert build(handler).run_once(operation_id) is True
    row = repository.get(operation_id)
    assert row.state == "rolled_back"
    assert world.uris == [BEFORE, COLLECTED]
    assert world.calls == 3
    assert ActivityLog.query.count() == 1
    assert row.journal["runtime"]["local_steps"]["accept.collect"]["collected"] == [COLLECTED]


def test_lost_atomic_acceptance_reply_replays_the_accepted_result_without_repeating_writes_or_activity(
    composite, monkeypatch,
):
    repository, operation_id, world, build, expire = composite
    original = repository.local_step
    stopped = []

    def interrupt(lease, name, callback, **kwargs):
        result = original(lease, name, callback, **kwargs)
        if name == "accept.collect" and not stopped:
            stopped.append(True)
            raise ProcessStopped()
        return result

    monkeypatch.setattr(repository, "local_step", interrupt)
    with pytest.raises(ProcessStopped):
        build(collect).run_once(operation_id)
    expire()
    assert build(collect).run_once(operation_id) is True
    assert repository.get(operation_id).state == "succeeded"
    assert world.uris == [BEFORE, COLLECTED]
    assert world.calls == 1
    assert ActivityLog.query.count() == 1


def test_failure_after_all_steps_were_accepted_does_not_compensate_the_accepted_step(composite):
    repository, operation_id, world, build, _expire = composite

    def handler(runtime):
        collect(runtime)
        return {"success": False, "error_code": "source_partial", "message": "Healthy contributions were retained."}

    assert build(handler).run_once(operation_id) is True
    row = repository.get(operation_id)
    assert row.state == "failed"
    assert row.journal["result"]["error_code"] == "source_partial"
    assert world.uris == [BEFORE, COLLECTED]
    assert world.calls == 1
    assert ActivityLog.query.count() == 1


@pytest.mark.parametrize("later_write", [False, True])
@pytest.mark.parametrize("crash_after_accept", [False, True])
@pytest.mark.parametrize("record_collect_history", [False, True])
def test_accepted_child_history_is_published_after_a_later_failure(
    composite, monkeypatch, later_write, crash_after_accept, record_collect_history,
):
    repository, operation_id, world, build, expire = composite
    row = repository.get(operation_id)
    store, scope = HistoryStore(), get_history_scope({})
    with repository.engine.begin() as connection:
        connection.execute(update(JobExecution).where(JobExecution.id == operation_id).values(payload={
            "version": 1, "history": store.capture(row.user_id, scope, [PLAYLIST]),
        }))
    if crash_after_accept:
        original = repository.local_step
        interrupted = []

        def stop_after_publication(lease, name, callback, **kwargs):
            result = original(lease, name, callback, **kwargs)
            if name == "accept.collect" and not interrupted:
                interrupted.append(True)
                raise ProcessStopped()
            return result

        monkeypatch.setattr(repository, "local_step", stop_after_publication)

    def handler(runtime):
        def apply():
            return runtime.step("collect-write", lambda: {
                "success": runtime.api.playlist_add_items(PLAYLIST, [COLLECTED]),
            })

        def commit(storage, result):
            if record_collect_history:
                StateService.record_verified_change(runtime.history, PLAYLIST,
                                                   before_uris=[BEFORE], after_uris=[BEFORE, COLLECTED])
            return result

        runtime.composite_step("collect", apply, commit)
        if later_write:
            def drip():
                runtime.step("drip-write", lambda: {"success": runtime.api.playlist_add_items(PLAYLIST, [DRIPPED])})
                StateService.record_verified_change(runtime.history, PLAYLIST,
                                                   before_uris=[BEFORE, COLLECTED],
                                                   after_uris=[BEFORE, COLLECTED, DRIPPED])
                raise ValueError("Drip did not complete")

            runtime.composite_step("drip", drip, lambda storage, result: result)
        return {"success": False, "error_code": "source_partial", "message": "Retained collected tracks."}

    if crash_after_accept:
        with pytest.raises(ProcessStopped):
            build(handler).run_once(operation_id)
        assert store.load(row.user_id, scope, PLAYLIST) == (None, None)
        expire()
    assert build(handler).run_once(operation_id) is True
    assert repository.get(operation_id).state == ("rolled_back" if later_write else "failed")
    assert world.uris == [BEFORE, COLLECTED]
    history, revision = store.load(row.user_id, scope, PLAYLIST)
    assert history == ({"states": [[BEFORE], [BEFORE, COLLECTED]], "current_index": 1}
                       if record_collect_history else None)
    assert revision == (operation_id if record_collect_history or later_write else None)
    assert world.calls == (3 if later_write else 1)
