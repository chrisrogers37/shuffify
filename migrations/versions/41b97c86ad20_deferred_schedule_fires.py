"""Keep busy scheduled slots across scheduler restarts."""

import sqlalchemy as sa
from alembic import op

revision = "41b97c86ad20"
down_revision = "948c1d4ea673"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deferred_schedule_fires",
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("schedules.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("cadence", sa.String(64), nullable=False),
        sa.Column("blocking_operation_id", sa.Integer(), nullable=False),
    )
    op.create_index("ix_deferred_schedule_fires_blocking_operation_id", "deferred_schedule_fires",
                    ["blocking_operation_id"])


def downgrade():
    op.drop_table("deferred_schedule_fires")
