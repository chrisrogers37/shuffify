"""One archive move owns both playlists until verified acceptance and history publication."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import requests
from sqlalchemy import update
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, JobExecution, PlaylistPair, db
from shuffify.services.operations.commands.archive_move import capture_unarchive
from shuffify.services.operations.history import HistoryStore
from shuffify.worker import OperationWorker
from tests.services.operations.test_crash_recovery import Crash, uri
from tests.services.operations.test_routine_commands import routine  # noqa: F401


@pytest.fixture
def move(routine):  # noqa: F811
    from shuffify.services.operations.commands.archive_move import unarchive

    routine.world.playlists["archive"]["track_uris"] = [uri(6), uri(1), uri(1)]
    routine.scope = "archive_move_scope_" + "a" * 32

    def enqueue(values=(1, 1, 7)):
        with Session(db.engine) as storage:
            command = capture_unarchive(storage, routine.user.id, "target", [uri(value) for value in values])
        binding = HistoryStore().capture(routine.user.id, routine.scope, command.reserved_targets)
        return routine.repository.enqueue(
            kind=command.kind,
            user_id=routine.user.id,
            targets=command.reserved_targets,
            payload={"version": 1, "command": command.model_dump(mode="json"), "history": binding},
        )

    def run(row, factory=None):
        worker = OperationWorker(
            routine.app,
            repository=routine.repository,
            handlers={"unarchive": unarchive},
            api_factory=factory or routine.factory,
            heartbeat_interval=0,
        )
        assert worker.run_once(row.id)
        return routine.repository.get(row.id)

    routine.enqueue_move, routine.run_move = enqueue, run
    return routine


def test_move_preserves_requested_occurrences_and_accepts_both_histories_once(move):
    row = move.enqueue_move()
    completed = move.run_move(row)
    result = completed.journal["result"]
    assert completed.state == "succeeded"
    assert result["unarchived_count"] == result["tracks_added"] == 3
    assert result["removed_count"] == 2
    assert move.world.playlists["target"]["track_uris"] == [uri(i) for i in (1, 2, 3, 1, 1, 7)]
    assert move.world.playlists["archive"]["track_uris"] == [uri(6)]
    assert [write[:2] for write in move.world.writes] == [
        ("POST", "/playlists/target/items"),
        ("DELETE", "/playlists/archive/items"),
    ]
    target = HistoryStore().load(move.user.id, move.scope, "target")[0]
    archive = HistoryStore().load(move.user.id, move.scope, "archive")[0]
    assert target["states"] == [[uri(i) for i in (1, 2, 3)], [uri(i) for i in (1, 2, 3, 1, 1, 7)]]
    assert archive["states"] == [[uri(6), uri(1), uri(1)], [uri(6)]]
    assert len(completed.journal["boundaries"]["accepted"]) == 1
    assert ActivityLog.query.count() == 1


def test_pair_relink_after_admission_refuses_before_authentication(move):
    row = move.enqueue_move()
    PlaylistPair.query.one().archive_playlist_id = "replacement"
    db.session.commit()
    completed = move.run_move(row)
    assert completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "configuration_changed"
    assert completed.journal["result"]["retryable"] is False
    assert move.factories == move.world.writes == []


def test_source_rejection_compensates_current_move_to_both_baselines(move):
    baseline = deepcopy(move.world.playlists)

    def factory(user):
        api = move.factory(user)
        send = api._http._session.request

        def reject(method, url, **kwargs):
            if method == "DELETE":
                response = requests.Response()
                response.status_code, response._content = 400, b"{}"
                return response
            return send(method, url, **kwargs)

        api._http._session.request = reject
        return api

    completed = move.run_move(move.enqueue_move(), factory)
    assert completed.state == "rolled_back"
    for resource in ("target", "archive"):
        assert move.world.playlists[resource]["track_uris"] == baseline[resource]["track_uris"]
    assert ActivityLog.query.count() == 0
    assert completed.journal["result"]["unarchived_count"] == 0


def test_required_local_failure_keeps_verified_move_and_requires_review(move, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("private local failure")

    monkeypatch.setattr("shuffify.services.operations.bookkeeping.OperationBookkeeping.reconcile_locks", unavailable)
    completed = move.run_move(move.enqueue_move())
    assert completed.state == "needs_attention"
    result = completed.journal["result"]
    assert result["unarchived_count"] == 3
    assert result["error_code"] == "required_bookkeeping_failed"
    assert len(move.world.writes) == 2
    assert "private" not in str(result)


@pytest.mark.parametrize("boundary", ["target_ack", "acceptance"])
def test_move_resumes_one_frozen_target_append_and_one_local_receipt(move, monkeypatch, boundary):
    from shuffify.services.operations.runtime import OperationRuntime

    checkpoint, local, cut = OperationRuntime.checkpoint, OperationRuntime.local_step, []

    def crash_checkpoint(self, document):
        value = checkpoint(self, document)
        if (
            boundary == "target_ack"
            and not cut
            and any(
                plan["resource"] == "target" and any(batch["state"] == "acknowledged" for batch in plan["batches"])
                for plan in document.get("plans", [])
            )
        ):
            cut.append(True)
            raise Crash()
        return value

    def crash_accept(self, name, callback, **kwargs):
        value = local(self, name, callback, **kwargs)
        if boundary == "acceptance" and name == "accept.unarchive" and not cut:
            cut.append(True)
            raise Crash()
        return value

    monkeypatch.setattr(OperationRuntime, "checkpoint", crash_checkpoint)
    monkeypatch.setattr(OperationRuntime, "local_step", crash_accept)
    row = move.enqueue_move()
    with pytest.raises(Crash):
        move.run_move(row)
    with move.repository.engine.begin() as connection:
        connection.execute(
            update(JobExecution)
            .where(JobExecution.id == row.id)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
    completed = move.run_move(row)
    assert completed.state == "succeeded"
    assert completed.journal["result"]["unarchived_count"] == 3
    assert move.world.playlists["target"]["track_uris"] == [uri(i) for i in (1, 2, 3, 1, 1, 7)]
    assert move.world.playlists["archive"]["track_uris"] == [uri(6)]
    assert len(move.world.writes) == 2
    assert ActivityLog.query.count() == 1


def test_http_admission_worker_and_owned_status_publish_both_histories(move, sample_token, monkeypatch):
    from shuffify.services.operations.commands.registry import COMMAND_HANDLERS

    client = move.app.test_client()
    with client.session_transaction() as session:
        session["spotify_token"] = sample_token
        session["user_data"] = {"id": "owner"}

    def forbidden(*args, **kwargs):
        pytest.fail("Admission cannot construct Spotify authentication")

    with monkeypatch.context() as admission:
        admission.setattr("shuffify.routes.require_auth", forbidden)
        admission.setattr("shuffify.spotify.api.SpotifyAPI.__init__", forbidden)
        response = client.post("/api/playlist/target/pair/unarchive", json={"track_uris": [uri(1), uri(1), uri(7)]})
    assert response.status_code == 202
    operation_id = response.json["operation_id"]
    assert move.world.writes == move.factories == []
    row = move.repository.get(operation_id)
    binding = row.payload["history"]
    assert set(binding["revisions"]) == {"target", "archive"}
    worker = OperationWorker(
        move.app, repository=move.repository, handlers=COMMAND_HANDLERS, api_factory=move.factory, heartbeat_interval=0
    )
    assert worker.run_once(operation_id)
    polled = client.get(response.headers["Location"])
    assert polled.status_code == 200
    assert polled.json["operation"]["state"] == "succeeded"
    assert polled.json["operation"]["result"]["unarchived_count"] == 3
    assert len(move.world.writes) == 2
    for resource in ["target", "archive"]:
        entry, receipt_id = HistoryStore().load(move.user.id, binding["scope"], resource)
        assert receipt_id == operation_id
        assert entry["states"][entry["current_index"]] == move.world.playlists[resource]["track_uris"]
