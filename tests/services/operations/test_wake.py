"""A dedicated notification socket wakes without any idle queue polling."""

import socket
import threading
import time
from types import SimpleNamespace

import pytest

from shuffify.services.operations.wake import PostgresWakeListener, direct_url


class Connection:
    def __init__(self):
        self.read, self.write = socket.socketpair()
        self.notifies, self.statements = [], []
        self.polls, self.closed = 0, False
        self.autocommit = False

    def fileno(self):
        return self.read.fileno()

    def cursor(self):
        connection = self

        class Cursor:
            def execute(self, statement):
                assert connection.autocommit is True
                connection.statements.append(statement)

            def close(self):
                pass
        return Cursor()

    def poll(self):
        self.polls += 1
        self.read.recv(1024)
        self.notifies.append(SimpleNamespace(channel='shuffify_operations'))

    def close(self):
        self.closed = True
        self.read.close()
        self.write.close()


def repository(dialect='postgresql'):
    return SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name=dialect)))


def test_registration_and_notifications_wake_but_idle_never_polls():
    connection, received, calls = Connection(), threading.Event(), []

    def wake():
        calls.append(True)
        received.set()
    with PostgresWakeListener(repository(), wake, connection_factory=lambda: connection) as listener:
        assert received.wait(1)
        assert listener.ready.is_set()
        assert connection.statements == ['LISTEN shuffify_operations']
        assert calls == [True]
        time.sleep(0.03)
        assert connection.polls == 0
        received.clear()
        connection.write.send(b'notify')
        assert received.wait(1)
        assert len(calls) == 2
        assert connection.polls == 1
    assert connection.closed


def test_stop_interrupts_an_idle_socket_promptly():
    connection, ready = Connection(), threading.Event()
    listener = PostgresWakeListener(repository(), ready.set, connection_factory=lambda: connection)
    listener.start()
    assert ready.wait(1)
    started = time.monotonic()
    listener.close()
    assert time.monotonic() - started < 0.5
    assert connection.closed
    assert not listener.running


def test_reconnect_wakes_after_registration_and_never_logs_connection_secrets(caplog):
    connection, ready, calls = Connection(), threading.Event(), []

    def connect():
        calls.append(True)
        if len(calls) == 1:
            raise OSError('password=private-fixture-secret')
        return connection
    with PostgresWakeListener(repository(), ready.set, connection_factory=connect, reconnect_delay=0.01):
        assert ready.wait(1)
        assert len(calls) == 2
    assert 'private-fixture-secret' not in caplog.text


def test_stop_interrupts_reconnect_backoff():
    attempted = threading.Event()

    def unavailable():
        attempted.set()
        raise OSError('unavailable')
    listener = PostgresWakeListener(repository(), lambda: None, connection_factory=unavailable, reconnect_delay=10)
    listener.start()
    assert attempted.wait(1)
    started = time.monotonic()
    listener.close()
    assert time.monotonic() - started < 0.5
    assert not listener.running


def test_sqlite_is_an_explicit_noop():
    def forbidden():
        raise AssertionError('SQLite must not create a listener connection')
    with PostgresWakeListener(repository('sqlite'), forbidden, connection_factory=forbidden) as listener:
        assert not listener.running
        assert not listener.ready.is_set()


def test_schedule_channel_boot_delivery_and_reconnect_are_refresh_hints():
    class ScheduleConnection(Connection):
        def poll(self):
            self.polls += 1
            if not self.read.recv(1024):
                raise OSError('disconnected')
            self.notifies.append(SimpleNamespace(channel='shuffify_schedules'))

    first, second = ScheduleConnection(), ScheduleConnection()
    refreshed, queue = threading.Event(), []
    connections = iter([first, second])
    with PostgresWakeListener(repository(), lambda: queue.append(True), schedule_callback=refreshed.set,
                              connection_factory=lambda: next(connections), reconnect_delay=.01) as listener:
        assert refreshed.wait(1)
        assert first.statements == ['LISTEN shuffify_operations', 'LISTEN shuffify_schedules']
        refreshed.clear()
        first.write.send(b'notify')
        assert refreshed.wait(1) and queue == [True]
        refreshed.clear()
        first.write.close()  # Broken LISTEN socket: reconnect must refresh even with no subsequent notification.
        assert refreshed.wait(1)
        assert queue == [True, True]
        assert listener.running


@pytest.mark.parametrize('url, host', [
    ('postgresql://u:p@ep-foo-123-pooler.us-east-2.aws.neon.tech/db', 'ep-foo-123.us-east-2.aws.neon.tech'),
    ('postgresql://u:p@ep-foo-123.us-east-2.aws.neon.tech/db', 'ep-foo-123.us-east-2.aws.neon.tech'),
    ('postgresql://u:p@db-pooler.example.com/db', 'db-pooler.example.com'),
    ('postgresql://u:p@localhost:5432/db', 'localhost'),
])
def test_direct_url_only_strips_the_neon_pooler_suffix(url, host):
    derived = direct_url(url)
    assert derived.host == host
    assert (derived.username, derived.password, derived.database) == ('u', 'p', 'db')
