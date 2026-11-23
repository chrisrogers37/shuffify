"""Existing schedules retain their definitions across the additive routine policy migration."""

from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def exercise_migration(connection):
    migration = ScriptDirectory(str(MIGRATIONS)).get_revision("33db533bb9ac")
    assert migration.down_revision == "f2a3b4c5d6e7"
    connection.exec_driver_sql("CREATE TABLE schedules (id INTEGER PRIMARY KEY, target_playlist_id VARCHAR(255))")
    connection.exec_driver_sql("INSERT INTO schedules VALUES (1, 'target')")
    with Operations.context(MigrationContext.configure(connection)):
        migration.module.upgrade()
        assert connection.exec_driver_sql("SELECT id, advanced_cron FROM schedules").one() == (1, None)
        indexes = sa.inspect(connection).get_indexes("schedules")
        assert any(i["column_names"] == ["target_playlist_id"] for i in indexes)
        migration.module.downgrade()
    assert connection.exec_driver_sql("SELECT * FROM schedules").one() == (1, "target")
    assert {c["name"] for c in sa.inspect(connection).get_columns("schedules")} == {"id", "target_playlist_id"}


def test_sqlite_upgrade_and_downgrade():
    with sa.create_engine("sqlite://").begin() as connection:
        exercise_migration(connection)


@pytest.mark.integration
def test_postgres_upgrade_and_downgrade(postgres_url):
    engine = sa.create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            schema = "routine_policy_" + uuid4().hex
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            exercise_migration(connection)
            connection.rollback()
    finally:
        engine.dispose()
