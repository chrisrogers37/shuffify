"""Additive operation storage preserves execution history and legacy writers."""

import importlib.util
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex

from shuffify.models.db import JobExecution, Schedule, UTCDateTime
from tests.postgres_server import LOCAL_HOSTS

ROOT = Path(__file__).resolve().parents[2]
REVISION = "b8c9d0e1f2a3"
MIGRATION = ROOT / "migrations" / "versions" / f"{REVISION}_operation_queue_storage.py"
QUEUE_COLUMNS = {
    "kind", "user_id", "targets", "payload", "state", "journal", "lease_owner",
    "lease_expires_at", "heartbeat_at", "request_key", "scheduled_for",
}
UNIQUE_INDEXES = {
    "uq_job_executions_schedule_fire": ("schedule_id", "scheduled_for"),
    "uq_job_executions_user_request": ("user_id", "request_key"),
}
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def _migration():
    spec = importlib.util.spec_from_file_location("operation_queue_storage", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_schema(connection):
    metadata = sa.MetaData()
    schedules = sa.Table("schedules", metadata, sa.Column("id", sa.Integer, primary_key=True))
    executions = sa.Table(
        "job_executions", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("schedule_id", sa.Integer, sa.ForeignKey("schedules.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("tracks_added", sa.Integer, nullable=True),
        sa.Column("tracks_total", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("result_json", sa.JSON, nullable=True),
        sa.Index("ix_job_executions_schedule_id", "schedule_id"),
        sa.Index("ix_job_executions_status", "status"),
        sa.Index("ix_job_executions_started_at", "started_at"),
    )
    metadata.create_all(connection)
    connection.execute(schedules.insert().values(id=7))
    return executions


def _migrate(connection, direction):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(_migration(), direction)()


@pytest.mark.parametrize("name", sorted(QUEUE_COLUMNS))
def test_model_queue_columns_are_nullable_without_defaults(name):
    assert name in JobExecution.__table__.columns
    column = JobExecution.__table__.columns[name]
    assert column.nullable is True
    assert column.default is None
    assert column.server_default is None
    assert not column.foreign_keys


def test_model_queue_types_and_legacy_serializer_are_preserved():
    table = JobExecution.__table__
    assert isinstance(table.c.user_id.type, sa.Integer)
    for name in ("targets", "payload", "journal"):
        assert isinstance(table.c[name].type, sa.JSON)
    for name in ("lease_expires_at", "heartbeat_at", "scheduled_for"):
        assert isinstance(table.c[name].type, UTCDateTime)
    for name in ("kind", "state", "lease_owner", "request_key"):
        assert isinstance(table.c[name].type, sa.String)
    assert not any(isinstance(constraint, sa.CheckConstraint) for constraint in table.constraints)
    assert set(JobExecution(status="success").to_dict()) == {
        "id", "schedule_id", "started_at", "completed_at", "status", "tracks_added",
        "tracks_total", "error_message", "result_detail",
    }


def test_schedule_definition_floor_is_nullable_without_defaults():
    column = Schedule.__table__.c.definition_changed_at
    assert isinstance(column.type, UTCDateTime)
    assert column.nullable is True
    assert column.default is column.server_default is None


@pytest.mark.parametrize("name,columns", UNIQUE_INDEXES.items())
def test_model_unique_indexes_compile_with_distinct_nulls(name, columns):
    indexes = {index.name: index for index in JobExecution.__table__.indexes}
    assert name in indexes
    index = indexes[name]
    assert index.unique is True
    assert tuple(column.name for column in index.columns) == columns
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert ddl.startswith("CREATE UNIQUE INDEX")
    assert "NULLS NOT DISTINCT" not in ddl


def test_migration_extends_the_base_without_a_fork():
    script = ScriptDirectory(str(ROOT / "migrations"))
    assert script.get_heads() == ["d0e1f2a3b4c5"]
    assert script.get_revision(REVISION).down_revision == "f7a8b9c0d1e2"


def test_migration_postgresql_ddl_has_no_new_constraints_or_defaults():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect=postgresql.dialect(), opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        _migration().upgrade()
    ddl = output.getvalue().upper()
    assert ddl.count("ADD COLUMN") == len(QUEUE_COLUMNS) + 1
    assert "ALTER TABLE SCHEDULES ADD COLUMN DEFINITION_CHANGED_AT" in ddl
    assert ddl.count("CREATE UNIQUE INDEX") == len(UNIQUE_INDEXES)
    assert "NULLS NOT DISTINCT" not in ddl
    assert "NOT NULL" not in ddl
    assert "DEFAULT" not in ddl
    assert "REFERENCES" not in ddl
    assert "CHECK" not in ddl


def _exercise_additive_upgrade(connection):
    legacy = _legacy_schema(connection)
    legacy_names = set(legacy.columns.keys())
    connection.execute(legacy.insert().values(id=1, started_at=NOW, status="success", tracks_added=5))

    _migrate(connection, "upgrade")

    inspector = sa.inspect(connection)
    schedule_columns = {column["name"]: column for column in inspector.get_columns("schedules")}
    assert set(schedule_columns) == {"id", "definition_changed_at"}
    assert schedule_columns["definition_changed_at"]["nullable"] is True
    assert schedule_columns["definition_changed_at"]["default"] is None
    assert connection.scalar(sa.text("SELECT definition_changed_at FROM schedules WHERE id = 7")) is None
    connection.execute(sa.text("INSERT INTO schedules (id) VALUES (8)"))
    assert connection.scalar(sa.text("SELECT definition_changed_at FROM schedules WHERE id = 8")) is None
    columns = {column["name"]: column for column in inspector.get_columns("job_executions")}
    assert set(columns) == legacy_names | QUEUE_COLUMNS
    for name in QUEUE_COLUMNS:
        assert columns[name]["nullable"] is True
        assert columns[name]["default"] is None
    assert inspector.get_check_constraints("job_executions") == []
    foreign_keys = inspector.get_foreign_keys("job_executions")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["schedule_id"]
    indexes = {index["name"]: index for index in inspector.get_indexes("job_executions")}
    for name, expected_columns in UNIQUE_INDEXES.items():
        assert indexes[name]["unique"]
        assert tuple(indexes[name]["column_names"]) == expected_columns

    # An insert compiled against the old table still works after the additive upgrade.
    connection.execute(legacy.insert().values(id=2, started_at=NOW, status="success"))
    upgraded = sa.Table("job_executions", sa.MetaData(), autoload_with=connection)
    saved = connection.execute(sa.select(upgraded).where(upgraded.c.id == 1)).mappings().one()
    assert saved["tracks_added"] == 5
    assert all(saved[name] is None for name in QUEUE_COLUMNS)
    added = connection.execute(sa.select(upgraded).where(upgraded.c.id == 2)).mappings().one()
    assert all(added[name] is None for name in QUEUE_COLUMNS)
    return legacy_names, upgraded


def _exercise_unique_keys(connection, table):
    def insert(execution_id, **values):
        connection.execute(table.insert().values(id=execution_id, started_at=NOW, status="running", **values))

    # Each key permits repeated rows when either or both key parts are null.
    for execution_id in (3, 4):
        insert(execution_id)
    for execution_id in (5, 6):
        insert(execution_id, schedule_id=7)
    for execution_id in (7, 8):
        insert(execution_id, scheduled_for=NOW)
    for execution_id in (9, 10):
        insert(execution_id, user_id=99)
    for execution_id in (11, 12):
        insert(execution_id, request_key="unowned-request")
    insert(13, schedule_id=7, scheduled_for=NOW)
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            insert(14, schedule_id=7, scheduled_for=NOW)
    insert(15, user_id=99, request_key="one-request")
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            insert(16, user_id=99, request_key="one-request")
    # Queue validation belongs to the executor, so additive storage accepts future state codes.
    insert(17, state="future-state", user_id=101)


def test_sqlite_upgrade_unique_keys_and_downgrade_preserve_legacy_rows():
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            legacy_names, upgraded = _exercise_additive_upgrade(connection)
            _exercise_unique_keys(connection, upgraded)
            _migrate(connection, "downgrade")
            assert {column["name"] for column in sa.inspect(connection).get_columns("schedules")} == {"id"}
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM schedules")) == 2
            assert {column["name"] for column in sa.inspect(connection).get_columns("job_executions")} == legacy_names
            assert connection.execute(sa.text("SELECT tracks_added FROM job_executions WHERE id = 1")).scalar_one() == 5
            assert connection.execute(sa.text("SELECT COUNT(*) FROM job_executions")).scalar_one() == 15
            connection.execute(sa.text(
                "INSERT INTO job_executions (id, started_at, status) VALUES (18, :started_at, 'success')"
            ), {"started_at": NOW})
    finally:
        engine.dispose()


@pytest.mark.integration
def test_postgresql_additive_storage_and_null_distinct_keys(postgres_url):
    parsed = sa.make_url(postgres_url)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in LOCAL_HOSTS, "This test only connects to local PostgreSQL"
    engine = sa.create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                schema = "queue_storage_" + uuid.uuid4().hex
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                legacy_names, upgraded = _exercise_additive_upgrade(connection)
                _exercise_unique_keys(connection, upgraded)
                definitions = connection.execute(sa.text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = :schema AND indexname IN (:schedule, :request)"
                ), {"schema": schema, "schedule": "uq_job_executions_schedule_fire",
                    "request": "uq_job_executions_user_request"}).scalars().all()
                assert len(definitions) == 2
                assert all("NULLS NOT DISTINCT" not in definition for definition in definitions)
                _migrate(connection, "downgrade")
                columns = sa.inspect(connection).get_columns("job_executions")
                assert {column["name"] for column in columns} == legacy_names
                assert connection.execute(sa.text("SELECT COUNT(*) FROM job_executions")).scalar_one() == 15
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
