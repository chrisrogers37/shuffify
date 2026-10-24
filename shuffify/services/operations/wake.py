"""Dedicated PostgreSQL LISTEN socket with interruptible reconnect and shutdown."""

import logging
import select
import socket
import threading

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)
_CHANNEL = 'shuffify_operations'
_SCHEDULE_CHANNEL = 'shuffify_schedules'


def direct_url(url):
    """Drop Neon's `-pooler` host suffix; PgBouncer transaction mode silently loses LISTEN/NOTIFY."""
    url = make_url(url)
    label, dot, rest = (url.host or '').partition('.')
    if rest.endswith('neon.tech') and label.endswith('-pooler'):
        return url.set(host=label[:-len('-pooler')] + dot + rest)
    return url


class PostgresWakeListener:
    """Wake after LISTEN commits and notifications; never scan the queue while idle.

    Registration is asynchronous. The registration wake covers commits before
    LISTEN became effective, including the gap during a reconnect. The worker
    owns draining durable state; notification payloads are only wake hints.
    """

    def __init__(self, repository, wake_callback, *, schedule_callback=None, connection_factory=None,
                 reconnect_delay=0.1, max_reconnect_delay=5, connect_timeout=3):
        self.enabled = repository.engine.dialect.name == 'postgresql'
        self.wake_callback = wake_callback
        self.schedule_callback = schedule_callback
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._connection = None
        self._connection_lock = threading.Lock()
        self._engine = None
        self._closed = False
        self._delay = max(0.001, reconnect_delay)
        self._max_delay = max(self._delay, max_reconnect_delay)
        self._connect_timeout = connect_timeout
        self._stop_read = self._stop_write = None
        if self.enabled:
            self._stop_read, self._stop_write = socket.socketpair()
            if connection_factory is None:
                # A dedicated NullPool keeps LISTEN and autocommit out of the application's pool.
                self._engine = create_engine(direct_url(repository.engine.url), poolclass=NullPool, connect_args={
                    'connect_timeout': connect_timeout, 'keepalives': 1, 'keepalives_idle': 10,
                    'keepalives_interval': 5, 'keepalives_count': 3,
                })
                connection_factory = self._engine.raw_connection
        self._connect = connection_factory

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def backend_pid(self):
        with self._connection_lock:
            connection = self._connection
            return connection.get_backend_pid() if connection is not None else None

    def start(self):
        if self._closed:
            raise RuntimeError('A closed notification listener cannot restart')
        if self.enabled and self._thread is None:
            self._thread = threading.Thread(target=self._listen, name='operation-notifications', daemon=True)
            self._thread.start()
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *exception):
        self.close()

    def _listen(self):
        delay = self._delay
        while not self._stop.is_set():
            connection = None
            try:
                connection = self._connect()
                driver = getattr(connection, 'driver_connection', connection)
                driver.autocommit = True
                cursor = driver.cursor()
                try:
                    cursor.execute(f'LISTEN {_CHANNEL}')
                    if self.schedule_callback is not None:
                        cursor.execute(f'LISTEN {_SCHEDULE_CHANNEL}')
                finally:
                    cursor.close()
                with self._connection_lock:
                    self._connection = driver
                self.ready.set()
                self.wake_callback()
                if self.schedule_callback is not None:
                    self.schedule_callback()
                delay = self._delay
                while not self._stop.is_set():
                    readable, _, _ = select.select([driver, self._stop_read], [], [])
                    if self._stop_read in readable:
                        break
                    driver.poll()
                    notifications = driver.notifies
                    wake = refresh = False
                    while notifications:
                        notification = notifications.pop(0)
                        wake = wake or notification.channel == _CHANNEL
                        refresh = refresh or notification.channel == _SCHEDULE_CHANNEL
                    if wake:
                        self.wake_callback()
                    if refresh and self.schedule_callback is not None:
                        self.schedule_callback()
            except Exception as error:
                # Connection errors can embed passwords, DSNs and hostnames; log only their type.
                if not self._stop.is_set():
                    logger.warning('Operation notifications disconnected (%s)', type(error).__name__)
            finally:
                self.ready.clear()
                with self._connection_lock:
                    self._connection = None
                if connection is not None:
                    try:
                        # Also safe for an injected pooled connection: never return a LISTEN session to its pool.
                        if hasattr(connection, 'invalidate'):
                            connection.invalidate()
                        connection.close()
                    except Exception:
                        logger.warning('Could not close the operation notification connection')
            if self._stop.wait(delay):
                break
            delay = min(self._max_delay, delay * 2)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._stop_write is not None:
            try:
                self._stop_write.send(b'stop')
            except OSError:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=self._connect_timeout + 2)
        for stream in (self._stop_read, self._stop_write):
            if stream is not None:
                stream.close()
        if self._engine is not None:
            self._engine.dispose()
