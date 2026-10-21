"""Exact inbox promotion and acceptance share one PostgreSQL transaction."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, PendingRaidTrack, RaidPlaylistLink, User
from shuffify.services.operations.commands.finalizer_capture import capture_raids
from shuffify.services.operations.commands.finalizers import _commit
from tests.services.operations.test_repository import postgres_repository as postgres_repository


@pytest.mark.integration
def test_postgres_exact_promotion_locks_selected_rows_and_accepts_the_same_receipt(postgres_repository, monkeypatch):
    repository = postgres_repository
    uri = "spotify:track:" + "a" * 22
    with Session(repository.engine) as storage, storage.begin():
        user = User(spotify_id="example-finalize-owner")
        storage.add(user)
        storage.flush()
        user_id = user.id
        selected = PendingRaidTrack(
            user_id=user_id, target_playlist_id="target", track_uri=uri, track_name="Example", status="pending"
        )
        later = PendingRaidTrack(
            user_id=user_id,
            target_playlist_id="target",
            track_uri="spotify:track:" + "b" * 22,
            track_name="Later example",
            status="pending",
        )
        storage.add_all(
            [
                selected,
                later,
                RaidPlaylistLink(user_id=user_id, target_playlist_id="target", raid_playlist_id="collection"),
            ]
        )
        storage.flush()
        selected_id, later_id = selected.id, later.id
    with Session(repository.engine) as storage:
        command = capture_raids(
            storage,
            user_id,
            "target",
            source_playlist_id="collection",
            target_track_uris=[uri],
            track_ids=[selected_id],
        )
    row = repository.enqueue(
        kind=command.kind,
        user_id=user_id,
        targets=command.reserved_targets,
        payload={"version": 1, "command": command.model_dump(mode="json")},
    )
    lease = repository.claim(operation_id=row.id)
    runtime = SimpleNamespace(user=SimpleNamespace(id=user_id), operation=row)
    applied = {
        "result": {"status": "not_required", "affected_count": 0, "promoted_count": 1, "database_status": "pending"},
        "local_actions": {"pending_promotion": {"rows": [{"id": selected_id, "track_uri": uri, "status": "pending"}]}},
    }
    from shuffify.services.operations.commands import finalizers

    promote = finalizers.promote_pending

    def locked(storage, *args):
        with repository.engine.connect() as competing:
            competing.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError):
                competing.execute(
                    update(PendingRaidTrack).where(PendingRaidTrack.id == selected_id).values(status="dismissed")
                )
            competing.rollback()
        return promote(storage, *args)

    monkeypatch.setattr(finalizers, "promote_pending", locked)

    def commit(storage):
        result = _commit(runtime, command, storage, applied)
        with Session(repository.engine) as observer:
            assert observer.get(PendingRaidTrack, selected_id).status == "pending"
            assert observer.get(JobExecution, row.id).journal == {}
        return result

    def accept(document, value):
        document["accepted"] = value["receipt"]
        return document

    result = repository.local_step(lease, "accept.finalize_raids", commit, journal_update=accept)
    assert result["receipt"]["promoted_ids"] == [selected_id]
    with Session(repository.engine) as observer:
        assert observer.get(PendingRaidTrack, selected_id).status == "promoted"
        assert observer.get(PendingRaidTrack, later_id).status == "pending"
        assert observer.get(JobExecution, row.id).journal["accepted"] == {"promoted_ids": [selected_id]}
        assert list(observer.scalars(select(PendingRaidTrack.id).where(PendingRaidTrack.status == "promoted"))) == [
            selected_id,
        ]
    assert repository.local_step(lease, "accept.finalize_raids", lambda _: pytest.fail("Receipt replay ran")) == result
