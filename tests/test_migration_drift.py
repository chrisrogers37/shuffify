"""A revision must not create a table that no model defines.

New databases get their tables from the models (``flask create-schema``),
so a table that only a revision creates would exist in an upgraded
database and in no new one.

Revisions can hold PostgreSQL-only DDL that SQLite cannot run, so this
reads them as text.
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
