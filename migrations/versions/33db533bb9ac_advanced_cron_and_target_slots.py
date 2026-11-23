"""Distinguish advanced timing from presets and index routine slots by target."""

import sqlalchemy as sa
from alembic import op

revision = "33db533bb9ac"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '1s'")
        op.execute("SET LOCAL statement_timeout = '3s'")
    op.add_column("schedules", sa.Column("advanced_cron", sa.Boolean(), nullable=True))
    op.create_index("ix_schedules_target_playlist_id", "schedules", ["target_playlist_id"])


def downgrade():
    op.drop_index("ix_schedules_target_playlist_id", table_name="schedules")
    op.drop_column("schedules", "advanced_cron")
