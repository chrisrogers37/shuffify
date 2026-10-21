"""The actual private playlist and its required association publish one PG receipt."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, PlaylistPair, User
from shuffify.services.operations.commands.asset_capture import capture_asset
from shuffify.services.operations.commands.assets import HANDLERS
from shuffify.worker import OperationWorker
from tests.services.operations.test_asset_commands import asset_operation as asset_operation
from tests.services.operations.test_repository import postgres_repository as postgres_repository


def enqueue(case, repository):
    with Session(repository.engine) as storage, storage.begin():
        storage.add(User(id=case.user.id, spotify_id="owner"))
        storage.flush()
        command = capture_asset(storage, case.user.id, "target", kind="pair_create")
    return repository.enqueue(
        kind=command.kind,
        user_id=case.user.id,
        targets=command.reserved_targets,
        payload={"version": 1, "command": command.model_dump(mode="json")},
    )


@pytest.mark.integration
@pytest.mark.parametrize("lost_reply", [False, True])
def test_atomic_asset_receipt_is_invisible_before_commit_and_replays_after_lost_reply(
    asset_operation,
    postgres_repository,
    db_app,
    monkeypatch,
    lost_reply,
):
    case, repository = asset_operation, postgres_repository
    row = enqueue(case, repository)
    original, observed = repository.local_step, []

    def step(lease, name, callback, **kwargs):
        if name != "accept.asset":
            return original(lease, name, callback, **kwargs)

        def commit(storage):
            receipt = callback(storage)
            with Session(repository.engine) as observer:
                assert observer.scalar(select(func.count()).select_from(PlaylistPair)) == 0
                assert observer.scalar(select(func.count()).select_from(ActivityLog)) == 0
                assert not repository.get(row.id).journal.get("boundaries", {}).get("accepted")
            observed.append(True)
            return receipt

        result = original(lease, name, commit, **kwargs)
        if lost_reply:
            raise OperationalError("COMMIT reply lost", {}, Exception("private database detail"))
        return result

    monkeypatch.setattr(repository, "local_step", step)
    worker = OperationWorker(
        db_app, repository=repository, handlers=HANDLERS, api_factory=case.api_factory, heartbeat_interval=0
    )
    assert worker.run_once(row.id)
    completed = repository.get(row.id)
    assert completed.state == "succeeded" and observed == [True]
    assert completed.journal["result"]["playlist"]["id"] == "created-1"
    assert completed.journal["result"]["privacy_confirmed"] is True
    assert [method for method, _, _ in case.world.writes] == ["POST", "PUT"]
    with Session(repository.engine) as observer:
        assert observer.scalar(select(func.count()).select_from(PlaylistPair)) == 1
        assert observer.scalar(select(func.count()).select_from(ActivityLog)) == 1


@pytest.mark.integration
def test_new_association_before_required_receipt_keeps_verified_orphan_without_overwrite(
    asset_operation,
    postgres_repository,
    db_app,
    monkeypatch,
):
    import shuffify.services.operations.commands.assets as assets

    case, repository = asset_operation, postgres_repository
    row = enqueue(case, repository)
    original = assets.commit_asset

    def raced(storage, user_id, operation_id, command, applied):
        with Session(repository.engine) as other, other.begin():
            other.add(PlaylistPair(user_id=user_id, production_playlist_id="target", archive_playlist_id="external"))
        return original(storage, user_id, operation_id, command, applied)

    monkeypatch.setattr(assets, "commit_asset", raced)
    worker = OperationWorker(
        db_app, repository=repository, handlers=HANDLERS, api_factory=case.api_factory, heartbeat_interval=0
    )
    assert worker.run_once(row.id)
    completed = repository.get(row.id)
    assert completed.state == "needs_attention"
    result = completed.journal["result"]
    assert result["playlist"]["id"] == "created-1" and result["privacy_confirmed"] is True
    assert result["error_code"] == "database_update_pending"
    assert [method for method, _, _ in case.world.writes] == ["POST", "PUT"]
    with Session(repository.engine) as storage:
        assert storage.scalar(select(PlaylistPair)).archive_playlist_id == "external"
        assert storage.scalar(select(func.count()).select_from(ActivityLog)) == 0
