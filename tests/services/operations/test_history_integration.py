"""Worker history and JSON context reads share confirmed scoped receipts."""

from unittest.mock import create_autospec, patch

import pytest

from shuffify.models.db import JobExecution, Schedule, User, db
from shuffify.services.operations.history import HistoryStore, get_history_scope
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.services.scheduler_service import SchedulerService
from shuffify.services.state_service import StaleHistoryError, StateService
from shuffify.spotify.api import SpotifyAPI


@pytest.fixture
def history_queue(db_app):
    user = User(spotify_id="history-user")
    db.session.add(user)
    db.session.commit()
    return OperationRepository(), HistoryStore(), user


def saved(*states):
    return {"states": [list(state) for state in states], "current_index": len(states) - 1}


def enqueue(repository, user_id, *, scope=None, legacy=None, target="playlist", schedule_id=None):
    payload = {"version": 1, "command": {}}
    if scope is not None:
        payload["history"] = HistoryStore(repository.engine).capture(
            user_id, scope, [target], legacy=legacy,
        )
    operation = repository.enqueue(kind="example", user_id=user_id, targets=[target], payload=payload,
                                   schedule_id=schedule_id)
    return operation, repository.claim(operation_id=operation.id)


def confirmed(repository, user_id, scope, *, before=("before",), after=("after",), schedule_id=None):
    operation, lease = enqueue(repository, user_id, scope=scope, schedule_id=schedule_id)
    history = HistoryStore(repository.engine)
    mapping = history.session_for(user_id, scope, ["playlist"])
    StateService.record_verified_change(mapping, "playlist", before_uris=list(before), after_uris=list(after))
    repository.checkpoint(lease, history.stage({}, mapping, state="succeeded", confirmed=True))
    repository.finish(lease, "succeeded")
    return operation.id


def test_runtime_history_is_optional_for_commands_without_session_binding(history_queue):
    repository, _store, user = history_queue
    _operation, lease = enqueue(repository, user.id)
    runtime = OperationRuntime(repository, lease)
    assert runtime.history is None
    assert runtime.stage_history("succeeded") is None


def test_runtime_stages_history_then_finish_makes_it_visible_atomically(history_queue):
    repository, store, user = history_queue
    scope = get_history_scope({})
    operation, lease = enqueue(repository, user.id, scope=scope, legacy={"playlist": saved(["original"], ["before"])})
    runtime = OperationRuntime(repository, lease)
    StateService.record_verified_change(runtime.history, "playlist", before_uris=["before"], after_uris=["after"])
    runtime.assert_history_revision("playlist")
    runtime.stage_history("succeeded")
    assert store.load(user.id, scope, "playlist") == (None, None)
    repository.finish(lease, "succeeded")
    assert store.load(user.id, scope, "playlist") == (saved(["original"], ["before"], ["after"]), operation.id)


def test_runtime_undo_rejects_a_stale_expected_revision_without_cross_scope_leak(history_queue):
    repository, store, user = history_queue
    scope = get_history_scope({})
    first_id = confirmed(repository, user.id, scope)
    operation, lease = enqueue(repository, user.id, scope=scope)
    runtime = OperationRuntime(repository, lease)
    assert runtime.history.revisions == {"playlist": first_id}
    repository.finish(lease, "failed")
    confirmed(repository, user.id, scope, before=("after",), after=("newer",))
    with pytest.raises(StaleHistoryError):
        runtime.assert_history_revision("playlist")
    assert store.load(user.id, get_history_scope({}), "playlist") == (None, None)
    assert operation.id != first_id


def test_runtime_rollback_stages_frozen_before_instead_of_advanced_history(history_queue):
    repository, store, user = history_queue
    scope = get_history_scope({})
    confirmed(repository, user.id, scope)
    operation, lease = enqueue(repository, user.id, scope=scope)
    runtime = OperationRuntime(repository, lease)
    StateService.record_verified_change(runtime.history, "playlist", before_uris=["after"], after_uris=["discarded"])
    runtime.stage_history("rolled_back")
    repository.finish(lease, "rolled_back")
    assert store.load(user.id, scope, "playlist") == (saved(["before"], ["after"]), operation.id)


def test_cached_step_restores_its_working_history_after_process_interruption(history_queue):
    repository, _store, user = history_queue
    scope = get_history_scope({})
    operation, lease = enqueue(repository, user.id, scope=scope)
    runtime = OperationRuntime(repository, lease)

    def verified_write():
        StateService.record_verified_change(runtime.history, "playlist", before_uris=["before"], after_uris=["after"])
        return {"success": True}

    assert runtime.step("verified-write", verified_write) == {"success": True}
    resumed = OperationRuntime(repository, lease)
    assert resumed.step("verified-write", lambda: pytest.fail("A confirmed step must not execute again")) == {
        "success": True,
    }
    assert StateService.get_current_uris(resumed.history, "playlist") == ["after"]
    assert resumed.history.before == {"playlist": None}
    assert repository.get(operation.id).journal["runtime"]["history_working"]["playlist_states"] == {
        "playlist": saved(["before"], ["after"]),
    }


def test_library_uses_confirmed_history_and_never_rewrites_stale_flask_history(auth_client):
    user = User.query.filter_by(spotify_id="user123").one()
    repository, scope = OperationRepository(), get_history_scope({})
    confirmed(repository, user.id, scope)
    legacy = {"playlist": saved(["stale-original"])}
    with auth_client.session_transaction() as session:
        session["history_scope"] = scope
        session["playlist_states"] = legacy
    api = create_autospec(SpotifyAPI, instance=True)
    api.get_user_playlists.return_value = [{"id": "playlist", "name": "Playlist", "images": [],
                                           "owner": {"id": "user123"}, "items": {"total": 1},
                                           "external_urls": {"spotify": "https://example.invalid/playlist"},
                                           "public": False}]
    with patch("shuffify.routes.require_auth", autospec=True, return_value=api):
        response = auth_client.get("/api/library")
    assert response.status_code == 200
    assert response.json["sections"]["playlists"][0]["can_undo"] is True
    with auth_client.session_transaction() as session:
        assert session["playlist_states"] == legacy
        session["history_scope"] = get_history_scope({})
    with patch("shuffify.routes.require_auth", autospec=True, return_value=api):
        other_scope = auth_client.get("/api/library")
    assert other_scope.json["sections"]["playlists"][0]["can_undo"] is False


def test_library_projection_prefers_authoritative_history_without_session_mirroring(auth_client):
    user = User.query.filter_by(spotify_id="user123").one()
    repository, scope = OperationRepository(), get_history_scope({})
    confirmed(repository, user.id, scope)
    with auth_client.session_transaction() as session:
        session["history_scope"] = scope
        session["playlist_states"] = {"playlist": saved(["stale"])}
    api = create_autospec(SpotifyAPI, instance=True)
    api.get_user_playlists.return_value = [{"id": "playlist", "name": "Playlist", "images": [],
                                           "owner": {"id": "user123"}, "items": {"total": 1},
                                           "external_urls": {"spotify": "https://example.invalid/playlist"},
                                           "public": False}]
    with patch("shuffify.routes.require_auth", autospec=True, return_value=api):
        response = auth_client.get("/api/library")
    assert response.status_code == 200
    assert response.json["sections"]["playlists"][0]["can_undo"] is True
    with auth_client.session_transaction() as session:
        assert session["playlist_states"] == {"playlist": saved(["stale"])}


def test_schedule_delete_detaches_active_and_confirmed_durable_operations(history_queue):
    repository, store, user = history_queue
    schedule = Schedule(user_id=user.id, job_type="shuffle", target_playlist_id="playlist")
    db.session.add(schedule)
    db.session.commit()
    schedule_id, scope = schedule.id, get_history_scope({})
    receipt_id = confirmed(repository, user.id, scope, schedule_id=schedule_id)
    active, lease = enqueue(repository, user.id, scope=scope, schedule_id=schedule_id)
    legacy = JobExecution(schedule_id=schedule_id, status="success")
    db.session.add(legacy)
    db.session.commit()
    legacy_id = legacy.id
    with patch.object(SchedulerService, "_remove_apscheduler_job", autospec=True):
        SchedulerService.delete_schedule(schedule_id, user.id)
    assert db.session.get(Schedule, schedule_id) is None
    assert repository.get(legacy_id) is None
    assert repository.get(active.id).schedule_id is None
    assert repository.get(active.id).lease_owner == lease.owner
    assert repository.get(receipt_id).schedule_id is None
    assert store.load(user.id, scope, "playlist")[1] == receipt_id


def test_retention_protects_active_work_and_its_referenced_history_revision(history_queue):
    repository, store, user = history_queue
    scope = get_history_scope({})
    receipt_id = confirmed(repository, user.id, scope)
    active, _lease = enqueue(repository, user.id, scope=scope)
    assert {receipt_id, active.id} <= store.protected_operation_ids()
