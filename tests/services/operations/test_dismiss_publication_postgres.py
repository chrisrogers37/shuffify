"""The DB-first accepted boundary and exact dismissal publish atomically in PostgreSQL."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, PendingRaidTrack, RaidPlaylistLink, User
from shuffify.services.operations.commands.dismiss_capture import capture_dismiss_all
from shuffify.services.operations.commands.dismissal import pending_dismiss
from shuffify.worker import OperationWorker
from tests.services.operations.test_dismiss_commands import inbox as inbox
from tests.services.operations.test_repository import postgres_repository as postgres_repository
from tests.services.operations.test_routine_commands import routine as routine


@pytest.mark.integration
@pytest.mark.parametrize("lost_reply", [False, True])
def test_required_dismissal_activity_and_acceptance_share_one_fenced_commit(
    inbox, postgres_repository, monkeypatch, lost_reply
):
    repository = postgres_repository
    with Session(repository.engine) as storage, storage.begin():
        storage.add(User(id=inbox.user.id, spotify_id=inbox.user.spotify_id))
        storage.flush()
        storage.add(RaidPlaylistLink(user_id=inbox.user.id, target_playlist_id="target", raid_playlist_id="collection"))
        storage.add_all(
            [
                PendingRaidTrack(
                    user_id=inbox.user.id,
                    target_playlist_id="target",
                    track_uri=value,
                    track_name="Example",
                    status="pending",
                )
                for value in ("spotify:track:" + "4".zfill(22), "spotify:track:" + "5".zfill(22))
            ]
        )
    with Session(repository.engine) as storage:
        command = capture_dismiss_all(storage, inbox.user.id, "target")
    row = repository.enqueue(
        kind=command.kind,
        user_id=inbox.user.id,
        targets=command.reserved_targets,
        payload={"version": 1, "command": command.model_dump(mode="json")},
    )
    original = repository.local_step
    commits = []

    def local(lease, name, callback, **kwargs):
        if name != "accept.dismiss":
            return original(lease, name, callback, **kwargs)

        def apply(storage):
            result = callback(storage)
            with Session(repository.engine) as observer:
                assert list(observer.scalars(select(PendingRaidTrack.status))) == ["pending", "pending"]
                assert observer.scalar(select(func.count()).select_from(ActivityLog)) == 0
                assert not repository.get(row.id).journal.get("boundaries", {}).get("accepted")
            commits.append(True)
            return result

        result = original(lease, name, apply, **kwargs)
        if lost_reply:
            raise RuntimeError("commit acknowledgement lost")
        return result

    monkeypatch.setattr(repository, "local_step", local)

    def factory(user):
        with Session(repository.engine) as storage:
            assert list(storage.scalars(select(PendingRaidTrack.status))) == ["dismissed", "dismissed"]
            assert storage.scalar(select(func.count()).select_from(ActivityLog)) == 1
        assert [step["name"] for step in repository.get(row.id).journal["boundaries"]["accepted"]] == ["dismiss"]
        return inbox.factory(user)

    worker = OperationWorker(
        inbox.app,
        repository=repository,
        handlers={"pending_dismiss": pending_dismiss},
        api_factory=factory,
        heartbeat_interval=0,
    )
    assert worker.run_once(row.id)
    completed = repository.get(row.id)
    assert completed.state == "succeeded" and commits == [True]
    assert completed.journal["result"]["dismissed_count"] == 2
    assert [method for method, _, _ in inbox.world.writes] == ["DELETE"]
