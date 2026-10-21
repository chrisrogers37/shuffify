"""Actual PostgreSQL locks and fenced receipt visibility for exact dismissal."""

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, PendingRaidTrack, RaidPlaylistLink, User
from shuffify.services.operations.commands.dismiss_capture import capture_dismiss, dismiss_captured
from tests.services.operations.test_repository import postgres_repository as postgres_repository

URI = "spotify:track:" + "a" * 22


def setup(repository):
    with Session(repository.engine) as storage, storage.begin():
        user = User(spotify_id="dismiss-postgres-owner")
        storage.add(user)
        storage.flush()
        owner = user.id
        link = RaidPlaylistLink(user_id=owner, target_playlist_id="target", raid_playlist_id="collection")
        row = PendingRaidTrack(
            user_id=owner, target_playlist_id="target", track_uri=URI, track_name="A", status="pending"
        )
        storage.add_all([link, row])
        storage.flush()
        track_id, link_id = row.id, link.id
    with Session(repository.engine) as storage:
        command = capture_dismiss(storage, owner, "target", track_ids=[track_id])
    return owner, track_id, link_id, command


@pytest.mark.integration
def test_real_postgres_dismissal_and_required_local_receipt_publish_together(postgres_repository):
    repository = postgres_repository
    owner, track_id, _, command = setup(repository)
    operation = repository.enqueue(
        kind="pending_dismiss",
        user_id=owner,
        targets=command.reserved_targets,
        payload={"version": 1, "command": command.model_dump(mode="json")},
    )
    lease = repository.claim(operation_id=operation.id)

    def apply(storage):
        receipt = dismiss_captured(storage, owner, command)
        with Session(repository.engine) as observer:
            assert observer.scalar(select(PendingRaidTrack.status).where(PendingRaidTrack.id == track_id)) == "pending"
            assert observer.get(JobExecution, operation.id).journal == {}
        return receipt

    result = repository.local_step(lease, "dismiss", apply)
    assert result["affected_count"] == 1
    with Session(repository.engine) as observer:
        assert observer.scalar(select(PendingRaidTrack.status).where(PendingRaidTrack.id == track_id)) == "dismissed"
        assert observer.get(JobExecution, operation.id).journal["runtime"]["local_steps"]["dismiss"] == result
    assert (
        repository.local_step(lease, "dismiss", lambda storage: pytest.fail("Receipt replay cannot apply twice"))
        == result
    )


@pytest.mark.integration
def test_real_postgres_idempotent_receipt_locks_association_and_already_dismissed_identity(postgres_repository):
    repository = postgres_repository
    owner, track_id, link_id, command = setup(repository)
    with Session(repository.engine) as storage, storage.begin():
        dismiss_captured(storage, owner, command)
    with Session(repository.engine) as storage:
        assert dismiss_captured(storage, owner, command)["affected_count"] == 0
        for statement in [
            update(RaidPlaylistLink).where(RaidPlaylistLink.id == link_id).values(raid_playlist_id="replacement"),
            update(PendingRaidTrack).where(PendingRaidTrack.id == track_id).values(status="promoted"),
        ]:
            with Session(repository.engine) as contender:
                contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(OperationalError):
                    contender.execute(statement)
                contender.rollback()
        storage.rollback()
