"""
Flask CLI commands for operational tasks.

Registered by create_app; run via `flask <command>` with the app's
context (config, database) already loaded.
"""

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    """Attach operational CLI commands to the app."""

    @app.cli.command("create-schema")
    def create_schema() -> None:
        """Build a new database from the models and stamp it at head.

        Run ahead of `flask db upgrade` wherever the database may be new. A
        database that already has a revision is left as it is, for the
        upgrade to bring forward. One that has the models' tables but no
        revision is refused: this command did not build it. The tables and
        the stamp are written in one transaction.
        """
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from flask import current_app
        from sqlalchemy import inspect

        from shuffify.models.db import db

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
            tables = set(inspect(connection).get_table_names())
            existing = sorted(tables & set(db.metadata.tables))
            if existing:
                raise click.ClickException(
                    "the database has the models' tables but no revision ("
                    + ", ".join(existing)
                    + "); refusing to stamp a schema this command did not build."
                )
            db.metadata.create_all(connection)
            context.stamp(script, head)
        click.echo(f"Built the schema from the models and stamped it at head ({head}).")
