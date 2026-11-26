"""
Flask CLI commands for operational tasks.

Registered by create_app; run via `flask <command>` with the app's
context (config, database) already loaded.
"""

import json
import os
import re

import click
from flask import Flask

# Set on the api only while a new database is being built, to that database's
# Neon endpoint id; scripts/pre-deploy.sh runs create-schema only while it is set.
PREDEPLOY_CREATE_SCHEMA_VAR = "SHUFFIFY_PREDEPLOY_CREATE_SCHEMA"
_NEON_ENDPOINT_ID = re.compile(r"ep-[a-z0-9]+(?:-[a-z0-9]+)*")


def _endpoint_refusal(expected, endpoint):
    """Why create-schema must not build, or None when ``expected`` names the database's endpoint.

    A value that isn't an endpoint id is never echoed: it could be a pasted URL.
    """
    where = f"the database is on Neon endpoint {endpoint}" if endpoint else "the database isn't on Neon"
    if not _NEON_ENDPOINT_ID.fullmatch(expected):
        return f"{PREDEPLOY_CREATE_SCHEMA_VAR} isn't a Neon endpoint id, and {where}"
    if expected != endpoint:
        return f"{PREDEPLOY_CREATE_SCHEMA_VAR} names Neon endpoint {expected}, but {where}"
    return None


def _table_names(connection):
    """Every table in the database, named ``schema.table`` outside the default schema.

    On PostgreSQL that is every schema but the system ones (the inspector
    already leaves out the ``pg_`` schemas); elsewhere, the default schema.
    """
    from sqlalchemy import inspect

    inspector = inspect(connection)
    if connection.dialect.name != "postgresql":
        return sorted(inspector.get_table_names())
    default = inspector.default_schema_name
    return sorted(
        table if schema == default else f"{schema}.{table}"
        for schema in inspector.get_schema_names()
        if schema != "information_schema"
        for table in inspector.get_table_names(schema=schema)
    )


def register_cli(app: Flask) -> None:
    """Attach operational CLI commands to the app."""

    @app.cli.command("create-schema")
    def create_schema() -> None:
        """Build a new database from the models and stamp it at head.

        Run ahead of `flask db upgrade` wherever the database may be new. A
        database that already has a revision is left as it is, for the
        upgrade to bring forward. One that has any table but no revision is
        refused, naming the tables: this command did not build it. An empty
        version table on its own doesn't count, since a refused upgrade can
        leave one behind. The tables and the stamp are written in one
        transaction.

        While SHUFFIFY_PREDEPLOY_CREATE_SCHEMA is set, it must be the Neon
        endpoint id of the database's host; any other value, or a host that
        isn't Neon's, is refused before the database is read. The output
        names the stamped revision and the endpoint, never the URL.
        """
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from flask import current_app

        from shuffify.models.db import db
        from shuffify.services.operations.wake import neon_endpoint_id

        endpoint = neon_endpoint_id(db.engine.url)
        expected = os.environ.get(PREDEPLOY_CREATE_SCHEMA_VAR)
        refusal = _endpoint_refusal(expected, endpoint) if expected else None
        if refusal:
            raise click.ClickException(f"{refusal}; refusing to build.")
        config = current_app.extensions["migrate"].migrate.get_config()
        script = ScriptDirectory.from_config(config)
        head = script.get_current_head()
        with db.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            revision = context.get_current_revision()
            if revision is not None:
                click.echo(
                    f"Database already at revision {revision}; nothing to build."
                )
                return
            existing = [name for name in _table_names(connection) if name != context.version_table]
            if existing:
                raise click.ClickException(
                    "the database has tables but no revision ("
                    + ", ".join(existing)
                    + "); refusing to stamp a schema this command did not build."
                )
            db.metadata.create_all(connection)
            context.stamp(script, head)
        on = f" on Neon endpoint {endpoint}." if endpoint else "; the database isn't on Neon."
        click.echo(f"Built the schema from the models and stamped it at head ({head}){on}")

    from shuffify.services.operations.retention import MAX_PAGE, cleanup_page

    @app.cli.command("prune-executions")
    @click.option("--after-id", type=click.IntRange(min=0), default=0, show_default=True)
    @click.option("--limit", type=click.IntRange(1, MAX_PAGE), default=MAX_PAGE, show_default=True)
    def prune_executions(after_id, limit):
        """Clean one page of expired execution history and print the continuation cursor."""
        from shuffify.models.db import db

        click.echo(json.dumps(cleanup_page(db.engine, after_id=after_id, limit=limit)))
