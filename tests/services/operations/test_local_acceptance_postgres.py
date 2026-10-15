"""PostgreSQL observers see a local effect and its accepted boundary together."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shuffify.models.db import ActivityLog, JobExecution, User
from shuffify.services.operations.bookkeeping import OperationBookkeeping
from tests.services.operations.test_repository import postgres_repository as postgres_repository
from tests.services.operations.test_repository import postgres_url as postgres_url


@pytest.mark.integration
def test_postgres_hides_the_local_effect_and_acceptance_until_the_same_commit(postgres_repository):
    repository = postgres_repository
    with Session(repository.engine) as session, session.begin():
        user = User(spotify_id="atomic-acceptance-owner")
        session.add(user)
        session.flush()
        user_id = user.id
    row = repository.enqueue(kind="example", user_id=user_id, targets=["target"], payload={})
    lease = repository.claim(operation_id=row.id)

    def apply(storage):
        result = OperationBookkeeping(storage, user_id, row.id).activity("raid", "Collected")
        storage.flush()
        with Session(repository.engine) as observer:
            assert observer.scalar(select(func.count(ActivityLog.id))) == 0
            assert observer.get(JobExecution, row.id).journal == {}
        return result

    def accept(document, result):
        document["accepted_steps"] = [{"name": "collect", "activity_id": result["id"]}]
        return document

    result = repository.local_step(lease, "collect", apply, journal_update=accept)
    with Session(repository.engine) as observer:
        assert observer.scalar(select(func.count(ActivityLog.id))) == 1
        journal = observer.get(JobExecution, row.id).journal
        assert journal["accepted_steps"] == [{"name": "collect", "activity_id": result["id"]}]
        assert journal["runtime"]["local_steps"]["collect"] == result
