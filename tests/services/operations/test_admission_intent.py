"""Keyed retries reuse frozen commands before local source or history reads."""

import pytest
from sqlalchemy import create_engine

from shuffify.models.db import db
from shuffify.services.operations.repository import OperationRepository, RequestKeyConflict


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'intent.sqlite'}")
    db.metadata.create_all(engine)
    yield OperationRepository(engine)
    engine.dispose()


def test_intent_replay_does_not_recapture_volatile_command_facts(repository):
    calls = []

    def capture(_session):
        calls.append(True)
        return ["target"], {"version": 1, "command": {"saved": "original"}, "history": {"revision": None}}

    accepted = repository.enqueue(kind="snapshot_restore", user_id=1, intent={"snapshot_id": 7},
                                  command_factory=capture, request_key="retry")
    lease = repository.claim(operation_id=accepted.id)
    repository.finish(lease, "succeeded")
    replayed = repository.enqueue(kind="snapshot_restore", user_id=1, intent={"snapshot_id": 7},
                                  command_factory=lambda _session: pytest.fail("The source could have been deleted"),
                                  request_key="retry")
    assert replayed.id == accepted.id
    assert replayed.payload["command"] == {"saved": "original"}
    assert calls == [True]


@pytest.mark.parametrize("intent", [{"snapshot_id": 8}, {"snapshot_id": True}, {"snapshot_id": 7.0}])
def test_intent_key_rejects_changed_request_before_factory(repository, intent):
    repository.enqueue(kind="snapshot_restore", user_id=1, intent={"snapshot_id": 7},
                       command_factory=lambda _session: (["target"], {"version": 1}), request_key="retry")
    with pytest.raises(RequestKeyConflict):
        repository.enqueue(kind="snapshot_restore", user_id=1, intent=intent,
                           command_factory=lambda _session: pytest.fail("Changed requests are refused before capture"),
                           request_key="retry")


def test_failed_capture_does_not_reserve_a_target_or_consume_the_key(repository):
    def unavailable(_session):
        raise ValueError("The saved source is unavailable")

    with pytest.raises(ValueError):
        repository.enqueue(kind="example", user_id=1, intent={}, command_factory=unavailable, request_key="retry")
    accepted = repository.enqueue(kind="example", user_id=1, intent={},
                                  command_factory=lambda _session: (["target"], {"version": 1}), request_key="retry")
    assert accepted.state == "queued"
