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

        Run after setting TOKEN_ENCRYPTION_KEY (migration off the
        SECRET_KEY-derived key) or after rotating it (with the old
        key in TOKEN_ENCRYPTION_KEY_FALLBACKS). See
        documentation/guides/credential-rotation.md.
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
