"""The reset between tests outlasts a request the last test left running."""

import threading
import time

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from config import HarnessConfig
from tests.e2e.conftest import APP_TABLES, reset_harness


def _waiting_for(engine, table: str) -> bool:
    """Whether a session waits to lock the table exclusively, as a TRUNCATE does."""
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE NOT granted AND mode = 'AccessExclusiveLock' "
            "AND relation = CAST(:table AS regclass))"
        ), {"table": table}).scalar_one()


def test_a_reset_deadlocked_by_a_running_request_completes_once_the_request_ends(fake):
    HarnessConfig.assert_safe()
    engine = create_engine(HarnessConfig.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    errors = []

    def reset():
        try:
            reset_harness()
        except Exception as error:
            errors.append(error)

    resetting = threading.Thread(target=reset)
    try:
        with engine.connect() as connection:
            tables = connection.execute(text(APP_TABLES)).scalar_one().split(", ")
        with engine.connect() as request, request.begin():
            # The request holds the table the reset locks last, then asks for the one it locked
            # first. Only the reset looks for the deadlock in time, so PostgreSQL ends the reset.
            request.execute(text("SET LOCAL deadlock_timeout = '1min'"))
            request.execute(text(f"LOCK TABLE {tables[-1]} IN ACCESS SHARE MODE"))
            resetting.start()
            deadline = time.monotonic() + 10
            while not _waiting_for(engine, tables[-1]):
                assert time.monotonic() < deadline, "the reset never reached the request's table"
                time.sleep(0.05)
            request.execute(text(f"LOCK TABLE {tables[0]} IN ACCESS SHARE MODE"))
        resetting.join(60)
    finally:
        engine.dispose()
    assert not resetting.is_alive()
    assert errors == []
