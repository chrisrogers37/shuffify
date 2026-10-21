"""The v1 scheduler's job table is dropped; the worker schedules from memory."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
REVISION = "d0e1f2a3b4c5"
MIGRATION = ROOT / "migrations/versions" / f"{REVISION}_drop_apscheduler_jobs.py"


def migration():
    assert MIGRATION.is_file(), "Databases that ran v1 keep APScheduler's job table until a migration drops it"
    spec = importlib.util.spec_from_file_location("drop_apscheduler_jobs", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_revision_extends_the_deployed_head_without_a_fork():
    assert MIGRATION.is_file()
    script = ScriptDirectory(str(ROOT / "migrations"))
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == "c9d0e1f2a3b4"


def exercise_drop(connection, schema=None):
    metadata = sa.MetaData()
    # APScheduler 3's SQLAlchemyJobStore table, as v1 created it in the app's database.
    jobs = sa.Table(
        "apscheduler_jobs", metadata,
        sa.Column("id", sa.Unicode(191), primary_key=True),
        sa.Column("next_run_time", sa.Float(25), index=True),
        sa.Column("job_state", sa.LargeBinary, nullable=False),
    )
    unrelated = sa.Table("unrelated", metadata, sa.Column("id", sa.Integer, primary_key=True))
    metadata.create_all(connection)
    connection.execute(jobs.insert(), [
        {"id": f"schedule_{n}", "next_run_time": 1.0, "job_state": b"v1"} for n in (4, 10)
    ])
    connection.execute(unrelated.insert(), [{"id": 1}])
    with Operations.context(MigrationContext.configure(connection)):
        migration().upgrade()
        assert "apscheduler_jobs" not in sa.inspect(connection).get_table_names(schema=schema)
        migration().upgrade()
        migration().downgrade()
    assert set(sa.inspect(connection).get_table_names(schema=schema)) == {"unrelated"}
    assert connection.scalar(sa.select(sa.func.count()).select_from(unrelated)) == 1


def test_sqlite_upgrade_drops_only_the_job_table_and_downgrade_brings_nothing_back():
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            exercise_drop(connection)
    finally:
        engine.dispose()


def test_upgrade_of_a_database_that_never_had_the_table_changes_nothing():
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration().upgrade()
            assert sa.inspect(connection).get_table_names() == []
    finally:
        engine.dispose()


@pytest.mark.integration
def test_postgres_upgrade_drops_the_job_table_inside_the_migration_transaction(postgres_url):
    engine = sa.create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                schema = "drop_apscheduler_jobs_" + uuid4().hex
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                exercise_drop(connection, schema)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
