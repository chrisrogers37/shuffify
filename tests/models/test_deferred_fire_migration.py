"""The additive retry table leaves existing schedules and execution history intact."""

from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


@pytest.mark.parametrize("backend", ["sqlite", pytest.param("postgres", marks=pytest.mark.integration)])
def test_deferred_fire_upgrade_and_downgrade_with_existing_rows(backend, request):
    url = request.getfixturevalue("postgres_url") if backend == "postgres" else "sqlite://"
    engine = sa.create_engine(url)
    migration = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "migrations")).get_revision("41b97c86ad20")
    try:
        with engine.connect() as conn, conn.begin() as transaction:
            if backend == "postgres":
                schema = "deferred_test_" + uuid4().hex
                conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            else:
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.exec_driver_sql("CREATE TABLE schedules (id INTEGER PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE job_executions (id INTEGER PRIMARY KEY, status VARCHAR(20))")
            conn.exec_driver_sql("INSERT INTO schedules VALUES (7), (8)")
            conn.exec_driver_sql("INSERT INTO job_executions VALUES (9, 'queued')")
            with Operations.context(MigrationContext.configure(conn)):
                migration.module.upgrade()
                assert conn.exec_driver_sql("SELECT * FROM deferred_schedule_fires").all() == []
                conn.exec_driver_sql("INSERT INTO deferred_schedule_fires VALUES (8, '2026-01-01', 'cadence', 9)")
                conn.exec_driver_sql("DELETE FROM schedules WHERE id=8")
                assert conn.exec_driver_sql("SELECT * FROM deferred_schedule_fires").all() == []
                migration.module.downgrade()
            assert not sa.inspect(conn).has_table("deferred_schedule_fires")
            assert conn.exec_driver_sql("SELECT * FROM schedules").all() == [(7,)]
            assert conn.exec_driver_sql("SELECT * FROM job_executions").all() == [(9, "queued")]
            transaction.rollback()
    finally:
        engine.dispose()
