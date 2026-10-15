"""A local effect cannot commit without its durable accepted-step boundary."""

import pytest

from shuffify.models.db import ActivityLog, User, db
from shuffify.services.operations.bookkeeping import OperationBookkeeping
from shuffify.services.operations.repository import OperationRepository


@pytest.fixture
def operation(db_app):
    user = User(spotify_id="local-acceptance-user")
    db.session.add(user)
    db.session.commit()
    repository = OperationRepository()
    accepted = repository.enqueue(kind="example", user_id=user.id, targets=["target"], payload={})
    return repository, repository.claim(operation_id=accepted.id), user.id


def test_local_effect_receipt_and_acceptance_commit_once_together(operation):
    repository, lease, user_id = operation
    calls = []

    def record(session):
        return OperationBookkeeping(session, user_id, lease.operation_id).activity("raid", "Collected tracks")

    def accept(document, value):
        assert document["runtime"]["local_steps"]["collect-local"] == value
        calls.append(value["id"])
        document["accepted_steps"] = [{"name": "collect", "activity_id": value["id"]}]
        return document

    first = repository.local_step(lease, "collect-local", record, journal_update=accept)
    assert repository.local_step(lease, "collect-local", record, journal_update=accept) == first
    assert calls == [first["id"]]
    assert ActivityLog.query.count() == 1
    journal = repository.get(lease.operation_id).journal
    assert journal["accepted_steps"] == [{"name": "collect", "activity_id": first["id"]}]
    assert journal["runtime"]["local_steps"]["collect-local"] == first


@pytest.mark.parametrize("failure", ["raise", "non_json", "not_object"])
def test_failed_acceptance_rolls_back_both_effect_and_receipt(operation, failure):
    repository, lease, user_id = operation

    def record(session):
        return OperationBookkeeping(session, user_id, lease.operation_id).activity("raid", "Never committed")

    def invalid(document, value):
        if failure == "raise":
            raise ValueError("The step cannot be accepted")
        if failure == "non_json":
            return {"invalid": object()}
        return []

    with pytest.raises(ValueError):
        repository.local_step(lease, "collect-local", record, journal_update=invalid)
    assert ActivityLog.query.count() == 0
    assert repository.get(lease.operation_id).journal == {}
