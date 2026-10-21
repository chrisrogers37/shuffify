"""PostgreSQL serializes restore children and atomically publishes their local acceptance."""

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, JobExecution, User
from shuffify.services.operations.commands.restore import capture_command, operation_restore
from shuffify.services.operations.history import HistoryStore
from shuffify.services.operations.restoration import RestoreUnavailable, capture_restore
from shuffify.services.operations.restore_review import build_context, read_metadata, read_tracks
from shuffify.worker import OperationWorker
from tests.services.operations.test_repository import postgres_repository as postgres_repository
from tests.services.operations.test_restore_command import restoration as restoration
from tests.services.operations.test_routine_commands import routine as routine

SCOPE = "restore_postgres_" + "a" * 32


def seed(case, repository):
    source = case.repository.get(case.parent_id)
    with Session(repository.engine) as storage, storage.begin():
        storage.add(User(id=case.user.id, spotify_id=case.user.spotify_id))
        storage.flush()
        parent = JobExecution(
            user_id=case.user.id,
            kind=source.kind,
            state="needs_attention",
            status="failed",
            targets=deepcopy(source.targets),
            journal=deepcopy(source.journal),
        )
        storage.add(parent)
        storage.flush()
        parent_id = parent.id
    captured = capture_restore(repository.get(parent_id), case.user.id)
    api = case.factory(case.user)
    try:
        reviewed = build_context(
            captured, lambda key: read_tracks(api, case.user, key), lambda key: read_metadata(api, case.user, key)
        )["reviewed"]
    finally:
        api.close()
    return parent_id, reviewed


def factory(case, parent_id, reviewed, *, pause=None):
    def build(storage):
        command = capture_command(storage, case.user.id, parent_id, reviewed)
        if pause is not None:
            pause()
        history = HistoryStore(storage.get_bind(), session=storage).capture(
            case.user.id, SCOPE, command.reserved_targets
        )
        return command.reserved_targets, {"version": 1, "command": command.model_dump(mode="json"), "history": history}

    return build


@pytest.mark.integration
def test_distinct_fresh_keys_serialize_on_parent_until_enqueue_transaction_commits(restoration, postgres_repository):
    repository = postgres_repository
    parent_id, reviewed = seed(restoration, repository)
    admitted, release, start = threading.Event(), threading.Event(), threading.Barrier(2)

    def pause():
        admitted.set()
        assert release.wait(5)

    def enqueue(key):
        start.wait(3)
        try:
            return repository.enqueue(
                kind="operation_restore",
                user_id=restoration.user.id,
                request_key=key,
                intent={"parent": parent_id, "reviewed": reviewed},
                command_factory=factory(restoration, parent_id, reviewed, pause=pause),
            )
        except RestoreUnavailable as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(enqueue, key) for key in ("first-key", "second-key")]
        try:
            assert admitted.wait(3)
            with Session(repository.engine) as observer:
                assert observer.scalar(select(func.count()).select_from(JobExecution)) == 1
            assert not any(future.done() for future in futures)
        finally:
            release.set()
        results = [future.result(timeout=8) for future in futures]
    rows = [result for result in results if isinstance(result, JobExecution)]
    blocked = [result for result in results if isinstance(result, RestoreUnavailable)]
    assert len(rows) == len(blocked) == 1
    assert blocked[0].code == "restoration_attempt_unresolved" and blocked[0].operation_id == rows[0].id
    with Session(repository.engine) as observer:
        assert observer.scalar(select(func.count()).select_from(JobExecution)) == 2
    assert restoration.world.writes == []


@pytest.mark.integration
@pytest.mark.parametrize("lost_reply", [False, True])
def test_parent_resolution_activity_acceptance_and_working_history_publish_in_one_commit(
    restoration, postgres_repository, monkeypatch, lost_reply
):
    repository = postgres_repository
    parent_id, reviewed = seed(restoration, repository)
    row = repository.enqueue(
        kind="operation_restore",
        user_id=restoration.user.id,
        intent={"parent": parent_id, "reviewed": reviewed},
        request_key="accepted-restore",
        command_factory=factory(restoration, parent_id, reviewed),
    )
    original, proofs = repository.local_step, []

    def local(lease, name, callback, **kwargs):
        if name != "accept.restore":
            return original(lease, name, callback, **kwargs)

        def commit(storage):
            value = callback(storage)
            with Session(repository.engine) as observer:
                assert not observer.get(JobExecution, parent_id).journal.get("runtime", {}).get("restoration")
                assert not observer.get(JobExecution, row.id).journal.get("boundaries", {}).get("accepted")
                assert observer.scalar(select(func.count()).select_from(ActivityLog)) == 0
            assert HistoryStore(repository.engine).load(restoration.user.id, SCOPE, "target")[0] is None
            proofs.append(True)
            return value

        value = original(lease, name, commit, **kwargs)
        if lost_reply:
            raise RuntimeError("commit reply lost")
        return value

    monkeypatch.setattr(repository, "local_step", local)
    worker = OperationWorker(
        restoration.app,
        repository=repository,
        handlers={"operation_restore": operation_restore},
        api_factory=restoration.factory,
        heartbeat_interval=0,
    )
    assert worker.run_once(row.id)
    completed = repository.get(row.id)
    assert completed.state == "succeeded" and proofs == [True]
    assert repository.get(parent_id).state == "needs_attention"
    assert repository.get(parent_id).journal["runtime"]["restoration"]["operation_id"] == row.id
    assert "accepted_history_working" in completed.journal["runtime"]
    assert len(restoration.world.writes) == 1
    with Session(repository.engine) as observer:
        assert observer.scalar(select(func.count()).select_from(ActivityLog)) == 1
    entry, receipt = HistoryStore(repository.engine).load(restoration.user.id, SCOPE, "target")
    assert receipt == row.id and entry["states"][-1] == restoration.world.playlists["target"]["track_uris"]
