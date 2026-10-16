"""Inbox dismissal is accepted locally before optional observed collection cleanup."""

from copy import deepcopy

import pytest
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, PendingRaidTrack, db
from shuffify.services.operations.commands.dismiss_capture import capture_dismiss, capture_dismiss_all
from shuffify.services.operations.commands.dismissal import pending_dismiss
from shuffify.worker import OperationWorker
from tests.services.operations.test_crash_recovery import uri
from tests.services.operations.test_routine_commands import routine as routine  # noqa: F401


@pytest.fixture
def inbox(routine):  # noqa: F811
    rows = [
        PendingRaidTrack(
            user_id=routine.user.id,
            target_playlist_id="target",
            track_uri=uri(value),
            track_name="Example track",
            status="pending",
        )
        for value in (4, 5)
    ]
    db.session.add_all(rows)
    db.session.commit()
    routine.pending_ids = [row.id for row in rows]

    def enqueue(*, all_pending=False, ids=None):
        with Session(db.engine) as storage:
            command = (
                capture_dismiss_all(storage, routine.user.id, "target")
                if all_pending
                else capture_dismiss(
                    storage,
                    routine.user.id,
                    "target",
                    track_ids=routine.pending_ids if ids is None else ids,
                )
            )
        return routine.repository.enqueue(
            kind=command.kind,
            user_id=routine.user.id,
            targets=command.reserved_targets,
            payload={"version": 1, "command": command.model_dump(mode="json")},
        )

    def run(row, factory=None):
        worker = OperationWorker(
            routine.app,
            repository=routine.repository,
            handlers={"pending_dismiss": pending_dismiss},
            api_factory=factory or routine.factory,
            heartbeat_interval=0,
        )
        assert worker.run_once(row.id)
        return routine.repository.get(row.id)

    routine.enqueue_dismiss, routine.run_dismiss = enqueue, run
    return routine


@pytest.mark.parametrize("all_pending", [False, True])
def test_required_dismissal_receipt_exists_before_provider_authentication(inbox, all_pending):
    target = deepcopy(inbox.world.playlists["target"])
    row = inbox.enqueue_dismiss(all_pending=all_pending)
    factories = []

    def factory(user):
        assert PendingRaidTrack.query.filter_by(status="dismissed").count() == 2
        assert ActivityLog.query.count() == 1
        accepted = inbox.repository.get(row.id).journal["boundaries"]["accepted"]
        assert [child["name"] for child in accepted] == ["dismiss"]
        factories.append(user.id)
        return inbox.factory(user)

    completed = inbox.run_dismiss(row, factory)
    result = completed.journal["result"]
    assert completed.state == "succeeded"
    assert result["dismissed_count"] == 2
    assert result["dismissed_ids"] == inbox.pending_ids
    assert result["database_status"] == "complete"
    assert result["source_status"] == "complete"
    assert result["source_removed_count"] == 2
    assert inbox.world.playlists["collection"]["track_uris"] == []
    assert inbox.world.playlists["target"] == target
    assert [method for method, _, _ in inbox.world.writes] == ["DELETE"]
    assert factories == [inbox.user.id]


def test_dismiss_all_never_expands_its_admitted_selection(inbox):
    row = inbox.enqueue_dismiss(all_pending=True)
    later = PendingRaidTrack(
        user_id=inbox.user.id,
        target_playlist_id="target",
        track_uri=uri(7),
        track_name="Later example",
        status="pending",
    )
    db.session.add(later)
    db.session.commit()
    inbox.world.playlists["collection"]["track_uris"].append(uri(7))
    completed = inbox.run_dismiss(row)
    assert completed.journal["result"]["dismissed_count"] == 2
    assert completed.journal["result"]["dismissed_ids"] == inbox.pending_ids
    assert db.session.get(PendingRaidTrack, later.id).status == "pending"
    assert inbox.world.playlists["collection"]["track_uris"] == [uri(7)]


def test_empty_selected_intersection_never_authenticates_or_cleans_collection(inbox):
    row = inbox.enqueue_dismiss(ids=[9999])

    def forbidden(_user):
        pytest.fail("An empty dismissal must not authenticate Spotify")

    completed = inbox.run_dismiss(row, forbidden)
    assert completed.state == "succeeded"
    assert completed.journal["result"]["dismissed_count"] == 0
    assert completed.journal["result"]["source_status"] == "not_required"
    assert inbox.world.writes == []
    assert PendingRaidTrack.query.filter_by(status="pending").count() == 2


def test_source_failure_retains_exact_accepted_database_dismissal(inbox, monkeypatch):
    from shuffify.spotify.exceptions import SpotifyForbiddenError

    target = deepcopy(inbox.world.playlists["target"])
    dispatch = inbox.world.dispatch

    def refuse(method, path, body):
        if method == "DELETE":
            raise SpotifyForbiddenError("private provider refusal")
        return dispatch(method, path, body)

    monkeypatch.setattr(inbox.world, "dispatch", refuse)
    completed = inbox.run_dismiss(inbox.enqueue_dismiss())
    assert completed.state in {"failed", "rolled_back", "needs_attention"}
    assert completed.journal["result"]["dismissed_count"] == 2
    assert completed.journal["result"]["database_status"] == "complete"
    assert PendingRaidTrack.query.filter_by(status="dismissed").count() == 2
    assert ActivityLog.query.count() == 1
    assert inbox.world.playlists["target"] == target
    assert "private provider" not in str(completed.journal["result"])
