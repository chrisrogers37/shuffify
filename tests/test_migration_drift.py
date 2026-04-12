"""Test that Alembic migrations match SQLAlchemy model definitions.

Catches the case where a model is added but ``flask db migrate``
was never run to generate the corresponding migration file.

Note: Migrations contain PostgreSQL-specific DDL (ALTER COLUMN, CHECK
constraints) and cannot be executed against SQLite. These tests use
static analysis of migration files instead.
"""

import os
import re

import pytest


@pytest.fixture(scope="module")
def model_table_names():
    """Get all table names defined in SQLAlchemy models."""
    from shuffify.models.db import db
    return set(db.metadata.tables.keys())


@pytest.fixture(scope="module")
def migration_table_names():
    """Parse migration files to find all tables created by migrations."""
    migrations_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migrations", "versions",
    )
    created_tables = set()
    pattern = re.compile(r"""op\.create_table\(\s*['"](\w+)['"]""")

    for filename in os.listdir(migrations_dir):
        if not filename.endswith(".py"):
            continue
        filepath = os.path.join(migrations_dir, filename)
        with open(filepath) as f:
            content = f.read()
        for match in pattern.finditer(content):
            created_tables.add(match.group(1))

    return created_tables


class TestMigrationDrift:
    """Verify that migration files cover all model definitions."""

    def test_all_model_tables_have_migrations(
        self, model_table_names, migration_table_names
    ):
        """Every table defined in models must have a create_table
        in migrations."""
        missing = model_table_names - migration_table_names
        assert not missing, (
            f"Tables defined in models but missing from migrations: "
            f"{sorted(missing)}. "
            f"Run: flask db migrate -m 'add <table>'"
        )

    def test_no_orphan_migration_tables(
        self, model_table_names, migration_table_names
    ):
        """Migrations should not create tables that no model defines."""
        orphans = migration_table_names - model_table_names
        assert not orphans, (
            f"Tables created by migrations but not defined in models: "
            f"{sorted(orphans)}. "
            f"Remove the model or delete the migration."
        )
