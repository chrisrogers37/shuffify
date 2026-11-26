"""Add workspaces, Spotify connections, and the person's Google identity."""

import sqlalchemy as sa
from alembic import op

revision = "2dbd3eab76c1"
down_revision = "41b97c86ad20"
branch_labels = None
depends_on = None


def upgrade():
    # Batch mode: SQLite cannot add a constraint with a plain ALTER. On
    # PostgreSQL it emits plain ALTER TABLE statements, with no table rebuild.
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("google_sub", sa.String(255), nullable=True))
        batch_op.add_column(
            sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_unique_constraint("uq_users_google_sub", ["google_sub"])

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_workspaces_user_id", ondelete="CASCADE"),
    )
    op.create_index("ix_workspaces_user_id", "workspaces", ["user_id"])

    op.create_table(
        "spotify_connections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("spotify_id", sa.String(255), nullable=True),
        sa.Column("spotify_display_name", sa.String(255), nullable=True),
        sa.Column("country", sa.String(10), nullable=True),
        sa.Column("spotify_product", sa.String(50), nullable=True),
        sa.Column("spotify_uri", sa.String(255), nullable=True),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("broken_reason", sa.String(32), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_spotify_connections_workspace_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("workspace_id", name="uq_spotify_connections_workspace_id"),
        sa.UniqueConstraint("spotify_id", name="uq_spotify_connections_spotify_id"),
        sa.CheckConstraint("status IN ('unchecked', 'ok', 'broken')", name="ck_spotify_connections_status"),
        sa.CheckConstraint(
            "broken_reason IN ('app_rejected', 'access_revoked', 'user_not_allowed')",
            name="ck_spotify_connections_broken_reason",
        ),
        sa.CheckConstraint(
            "(status = 'broken') = (broken_reason IS NOT NULL)",
            name="ck_spotify_connections_broken_has_reason",
        ),
    )


def downgrade():
    raise NotImplementedError("Dropping the identity tables and columns would destroy data; restore from a backup.")
