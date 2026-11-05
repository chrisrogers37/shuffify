"""Login history's stored raw session ids are cleared: new rows hold keyed digests."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from tests.postgres_server import LOCAL_HOSTS

ROOT = Path(__file__).resolve().parents[2]
REVISION = "e1f2a3b4c5d6"
MIGRATION = ROOT / "migrations/versions" / f"{REVISION}_null_login_history_session_ids.py"


def migration():
    assert MIGRATION.is_file(), "Login history keeps raw session ids until a migration clears them"
    spec = importlib.util.spec_from_file_location("null_login_history_session_ids", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_revision_extends_the_deployed_head_without_a_fork():
    assert MIGRATION.is_file()
    script = ScriptDirectory(str(ROOT / "migrations"))
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == "d0e1f2a3b4c5"


def exercise_upgrade(connection):
    metadata = sa.MetaData()
    # login_history's columns that the migration reads or must leave alone.
    history = sa.Table(
        "login_history", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("session_id", sa.String(255)),
        sa.Column("login_type", sa.String(20), nullable=False),
    )
    metadata.create_all(connection)
    connection.execute(history.insert(), [
        {"id": 1, "user_id": 7, "ip_address": "203.0.113.9", "session_id": "raw-sid-1", "login_type": "oauth_initial"},
        {"id": 2, "user_id": 8, "ip_address": "203.0.113.10", "session_id": None, "login_type": "oauth_initial"},
    ])
    with Operations.context(MigrationContext.configure(connection)):
        module = migration()
        module.upgrade()
        module.upgrade()
        module.downgrade()
    rows = connection.execute(
        sa.select(history.c.id, history.c.user_id, history.c.ip_address, history.c.session_id).order_by(history.c.id)
    ).all()
    assert [tuple(row) for row in rows] == [(1, 7, "203.0.113.9", None), (2, 8, "203.0.113.10", None)]


def test_upgrade_nulls_login_history_session_ids_on_sqlite():
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            exercise_upgrade(connection)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_upgrade_nulls_login_history_session_ids_on_postgres(postgres_url):
    assert sa.make_url(postgres_url).host in LOCAL_HOSTS
    engine = sa.create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                schema = "null_login_history_session_ids_" + uuid4().hex
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                exercise_upgrade(connection)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
