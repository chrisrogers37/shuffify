"""PostgreSQL freezes volatile admission facts once behind the request-key lock."""

from threading import Lock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, User
from shuffify.services.operations.repository import RequestKeyConflict
from tests.services.operations.test_repository import (
    postgres_repository as postgres_repository,
)
from tests.services.operations.test_repository import (
    race,
)


def seed_user(repository):
    with Session(repository.engine) as session, session.begin():
        user = User(spotify_id='factory-owner')
        session.add(user)
        session.flush()
        return user.id


def counted_factory(repository, user_id):
    calls, lock = [], Lock()

    def build(session):
        assert isinstance(session, Session)
        assert session.in_transaction()
        assert session.scalar(select(User.spotify_id).where(User.id == user_id)) == 'factory-owner'
        with lock:
            calls.append(len(calls) + 1)
            revision = calls[-1]
        return ['target'], {'version': 1, 'command': {'captured_revision': revision},
                            'history': {'scope': f'volatile-scope-{revision}'}}
    return build, calls


@pytest.mark.integration
def test_concurrent_replay_captures_volatile_facts_once_and_returns_the_same_operation(postgres_repository):
    repository = postgres_repository
    user_id = seed_user(repository)
    factory, calls = counted_factory(repository, user_id)
    intent = {'version': 1, 'playlist_id': 'target', 'algorithm': 'BasicShuffle'}
    outcomes = race(lambda _: repository.enqueue(kind='shuffle', user_id=user_id, request_key='same-request',
                                                 intent=intent, command_factory=factory))
    assert outcomes[0].id == outcomes[1].id
    assert calls == [1]
    assert outcomes[0].payload['command']['captured_revision'] == 1
    # The closure has changed; matching visible intent still replays before looking at current history.
    calls.append(99)
    replayed = repository.enqueue(kind='shuffle', user_id=user_id, request_key='same-request',
                                  intent=intent, command_factory=factory)
    assert replayed.id == outcomes[0].id
    assert calls == [1, 99]
    assert replayed.payload == outcomes[0].payload
    with Session(repository.engine) as session:
        assert len(session.scalars(select(JobExecution)).all()) == 1


@pytest.mark.integration
def test_conflicting_concurrent_intent_never_calls_the_losing_factory(postgres_repository):
    repository = postgres_repository
    user_id = seed_user(repository)
    factory, calls = counted_factory(repository, user_id)
    outcomes = race(lambda index: repository.enqueue(
        kind='shuffle', user_id=user_id, request_key='same-request', intent={'version': 1, 'choice': index},
        command_factory=factory,
    ))
    assert sum(isinstance(result, RequestKeyConflict) for result in outcomes) == 1
    assert calls == [1]
    accepted = next(result for result in outcomes if not isinstance(result, RequestKeyConflict))
    before = list(calls)
    with pytest.raises(RequestKeyConflict):
        repository.enqueue(kind='shuffle', user_id=user_id, request_key='same-request',
                           intent={'version': 1, 'choice': 'changed'}, command_factory=factory)
    assert calls == before
    assert repository.get(accepted.id).payload['command']['captured_revision'] == 1
