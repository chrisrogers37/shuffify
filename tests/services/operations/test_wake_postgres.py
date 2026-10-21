"""Real PostgreSQL LISTEN ordering and reconnection close the lost-wake gap."""

import threading

import pytest
from sqlalchemy import text

from shuffify.services.operations.wake import PostgresWakeListener
from tests.services.operations.test_repository import (
    postgres_repository as postgres_repository,
)


@pytest.mark.integration
def test_committed_admission_wakes_a_registered_listener(postgres_repository):
    repository = postgres_repository
    received = threading.Event()
    with PostgresWakeListener(repository, received.set) as listener:
        assert received.wait(3)
        assert listener.ready.is_set()
        assert listener.backend_pid is not None
        received.clear()
        row = repository.enqueue(kind='example', user_id=1, targets=['target'], payload={'version': 1})
        assert received.wait(3)
        assert repository.get(row.id).state == 'queued'


@pytest.mark.integration
def test_notification_is_not_delivered_before_its_transaction_commits(postgres_repository):
    repository = postgres_repository
    received = threading.Event()
    with PostgresWakeListener(repository, received.set):
        assert received.wait(3)
        received.clear()
        with repository.engine.begin() as connection:
            connection.execute(text("SELECT pg_notify('shuffify_operations', 'test')"))
            assert not received.wait(0.05)
        assert received.wait(3)


@pytest.mark.integration
def test_reconnection_wakes_for_commits_missed_while_disconnected(postgres_repository):
    repository = postgres_repository
    received, reconnecting, allow_reconnect = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def connect():
        calls.append(True)
        if len(calls) > 1:
            reconnecting.set()
            assert allow_reconnect.wait(3)
        return repository.engine.raw_connection()
    with PostgresWakeListener(repository, received.set, connection_factory=connect, reconnect_delay=0.01) as listener:
        assert received.wait(3)
        first_pid = listener.backend_pid
        received.clear()
        with repository.engine.begin() as admin:
            assert admin.scalar(text('SELECT pg_terminate_backend(:pid)'), {'pid': first_pid}) is True
        assert reconnecting.wait(3)
        accepted = repository.enqueue(kind='example', user_id=1, targets=['missed-target'], payload={'version': 1})
        assert not received.is_set()
        allow_reconnect.set()
        assert received.wait(3)
        assert listener.backend_pid != first_pid
        assert repository.get(accepted.id).state == 'queued'
