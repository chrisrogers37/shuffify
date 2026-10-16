"""Persist a stable schedule catch-up start independent of run bookkeeping."""

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("schedules", sa.Column("recovery_start_at", sa.DateTime(timezone=True), nullable=True))
    schedules = sa.table(
        "schedules",
        sa.column("recovery_start_at", sa.DateTime(timezone=True)),
        sa.column("definition_changed_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.execute(schedules.update().values(
        recovery_start_at=sa.func.coalesce(schedules.c.definition_changed_at, schedules.c.created_at),
    ))


def downgrade():
    op.drop_column("schedules", "recovery_start_at")
