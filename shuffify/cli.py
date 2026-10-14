"""
Flask CLI commands for operational tasks.

Registered by create_app; run via `flask <command>` with the app's
context (config, database) already loaded.
"""

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    """Attach operational CLI commands to the app."""

    @app.cli.command("rotate-token-encryption")
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Report what would change without writing.",
    )
    def rotate_token_encryption(dry_run: bool) -> None:
        """Re-encrypt stored refresh tokens with the primary key.

        Run after setting TOKEN_ENCRYPTION_KEY (to move tokens off the
        SECRET_KEY-derived key) or after rotating it (with the old
        key in TOKEN_ENCRYPTION_KEY_FALLBACKS).
        """
        from shuffify.services.token_service import TokenService
        from shuffify.services.user_service import UserService

        # Guard needed here despite the service's own init checks:
        # with zero token-bearing users the loop never runs, so this
        # is the only report of an uninitialized state.
        if not TokenService.is_initialized():
            raise click.ClickException(
                "Token encryption is not initialized — check the "
                "startup logs for TokenService errors."
            )

        result = UserService.reencrypt_all_refresh_tokens(dry_run=dry_run)

        mode = " (dry run — nothing written)" if dry_run else ""
        click.echo(
            f"Stored refresh tokens: {result.total}{mode}\n"
            f"  already on primary key: {result.already_current}\n"
            f"  re-encrypted: {result.reencrypted}\n"
            f"  failed: {result.failed}"
        )
        if result.failed:
            raise click.ClickException(
                "undecryptable tokens were left unchanged for users: "
                + ", ".join(result.failed_spotify_ids)
            )

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
