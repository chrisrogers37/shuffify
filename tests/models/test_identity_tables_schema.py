"""The identity revision adds two tables and two person columns and keeps every existing user."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from shuffify.models.db import SpotifyConnection, User, Workspace, db
from tests.postgres_server import LOCAL_HOSTS

SCRIPT = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "migrations"))
REVISION = "2dbd3eab76c1"
NEW_TABLES = ("workspaces", "spotify_connections")
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_new_revision_extends_the_deployed_head_without_a_fork():
    assert SCRIPT.get_heads() == [REVISION]
    assert SCRIPT.get_revision(REVISION).down_revision == "41b97c86ad20"


def test_migration_drops_and_relaxes_nothing():
    text = Path(SCRIPT.get_revision(REVISION).path).read_text()
    for forbidden in ("drop_column", "drop_constraint", "alter_column", "rename_table", "drop_table"):
        assert forbidden not in text


def test_downgrade_refuses():
    with pytest.raises(NotImplementedError):
        SCRIPT.get_revision(REVISION).module.downgrade()


def legacy_users(metadata):
    """The users table as it was before this revision."""
    return sa.Table(
        "users", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("spotify_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("email", sa.String(255)),
        sa.Column("profile_image_url", sa.String(1024)),
        sa.Column("encrypted_refresh_token", sa.Text),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("login_count", sa.Integer, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("country", sa.String(10)),
        sa.Column("spotify_product", sa.String(50)),
        sa.Column("spotify_uri", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Index("ix_users_spotify_id", "spotify_id", unique=True),
    )


def describe(connection, schema=None):
    """Columns, constraints and indexes of the new tables, as the database reports them."""
    inspector = sa.inspect(connection)
    described = {}
    for name in NEW_TABLES:
        described[name] = {
            "columns": {
                column["name"]: (str(column["type"]), column["nullable"], column["default"])
                for column in inspector.get_columns(name, schema=schema)
            },
            "unique": sorted(
                (constraint["name"], tuple(constraint["column_names"]))
                for constraint in inspector.get_unique_constraints(name, schema=schema)
            ),
            "check": sorted(
                (constraint["name"], " ".join(constraint["sqltext"].split()))
                for constraint in inspector.get_check_constraints(name, schema=schema)
            ),
            "indexes": sorted(
                (index["name"], tuple(index["column_names"]), bool(index["unique"]))
                for index in inspector.get_indexes(name, schema=schema)
            ),
            "foreign_keys": sorted(
                (
                    foreign_key["name"],
                    tuple(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                    foreign_key["options"].get("ondelete"),
                )
                for foreign_key in inspector.get_foreign_keys(name, schema=schema)
            ),
        }
    return described


def exercise_upgrade(connection, schema=None):
    """Upgrade a users table that holds rows, check what survives, and describe the new tables."""
    legacy_metadata = sa.MetaData()
    users = legacy_users(legacy_metadata)
    legacy_metadata.create_all(connection)
    row = {
        "display_name": "Test User", "email": "test@example.invalid",
        "profile_image_url": "https://example.invalid/p.png",
        "login_count": 3, "is_active": True, "country": "US", "spotify_product": "premium", "spotify_uri": None,
        "created_at": NOW, "updated_at": NOW,
    }
    connection.execute(users.insert(), [
        {**row, "id": 1, "spotify_id": "spotify-user-1", "encrypted_refresh_token": "encrypted-token-1"},
        {**row, "id": 2, "spotify_id": "spotify-user-2", "encrypted_refresh_token": None,
         "spotify_uri": "spotify:user:2"},
    ])
    before = [tuple(r) for r in connection.execute(sa.select(users).order_by(users.c.id))]

    with Operations.context(MigrationContext.configure(connection)):
        SCRIPT.get_revision(REVISION).module.upgrade()

    upgraded = sa.Table("users", sa.MetaData(), autoload_with=connection)
    old_columns = [upgraded.c[column.name] for column in users.c]
    kept = [tuple(r) for r in connection.execute(sa.select(*old_columns).order_by(upgraded.c.id))]
    assert kept == before
    saved = connection.execute(sa.select(upgraded).order_by(upgraded.c.id)).mappings().all()
    assert [r["google_sub"] for r in saved] == [None, None]
    assert [bool(r["email_verified"]) for r in saved] == [False, False]
    for name in NEW_TABLES:
        assert connection.scalar(sa.text(f"SELECT COUNT(*) FROM {name}")) == 0

    inspector = sa.inspect(connection)
    columns = {c["name"]: c for c in inspector.get_columns("users")}
    assert columns["google_sub"]["nullable"] is True
    assert columns["email_verified"]["nullable"] is False
    uniques = {c["name"]: c["column_names"] for c in inspector.get_unique_constraints("users")}
    assert uniques == {"uq_users_google_sub": ["google_sub"]}
    # A row written the old way, with neither new column, still inserts.
    connection.execute(sa.text(
        "INSERT INTO users (id, spotify_id, login_count, is_active, created_at, updated_at) "
        "VALUES (3, 'spotify-user-3', 0, :active, :now, :now)"
    ), {"active": True, "now": NOW})
    assert connection.scalar(sa.text("SELECT email_verified FROM users WHERE id = 3")) in (False, 0)

    return describe(connection, schema)


def build_reference(connection, schema=None):
    """The new tables as the models build them, described the same way."""
    db.metadata.create_all(connection, tables=[User.__table__, Workspace.__table__, SpotifyConnection.__table__])
    return describe(connection, schema)


def assert_names_are_described(described):
    """The description sees every named constraint, so two equal descriptions are not two empty ones."""
    connections, workspaces = described["spotify_connections"], described["workspaces"]
    assert {name for name, _ in connections["unique"]} == {
        "uq_spotify_connections_workspace_id", "uq_spotify_connections_spotify_id",
    }
    assert {name for name, _ in connections["check"]} == {
        "ck_spotify_connections_status", "ck_spotify_connections_broken_reason",
        "ck_spotify_connections_broken_has_reason",
    }
    assert [key[0] for key in connections["foreign_keys"]] == ["fk_spotify_connections_workspace_id"]
    assert [key[0] for key in workspaces["foreign_keys"]] == ["fk_workspaces_user_id"]
    assert ("ix_workspaces_user_id", ("user_id",), False) in workspaces["indexes"]


def test_upgrade_keeps_users_and_builds_the_models_tables_on_sqlite():
    upgraded_engine = sa.create_engine("sqlite://")
    reference_engine = sa.create_engine("sqlite://")
    try:
        with upgraded_engine.begin() as connection:
            upgraded = exercise_upgrade(connection)
        with reference_engine.begin() as connection:
            reference = build_reference(connection)
    finally:
        upgraded_engine.dispose()
        reference_engine.dispose()
    assert_names_are_described(reference)
    assert upgraded == reference


@pytest.mark.integration
def test_upgrade_keeps_users_and_builds_the_models_tables_on_postgres(postgres_url):
    assert sa.make_url(postgres_url).host in LOCAL_HOSTS
    engine = sa.create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                token = uuid4().hex
                upgraded_schema = "identity_upgraded_" + token
                reference_schema = "identity_reference_" + token
                for schema in (upgraded_schema, reference_schema):
                    connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{upgraded_schema}"')
                upgraded = exercise_upgrade(connection, upgraded_schema)
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{reference_schema}"')
                reference = build_reference(connection, reference_schema)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    assert_names_are_described(reference)
    assert upgraded == reference
