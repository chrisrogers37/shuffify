"""Make JobExecution.schedule_id nullable for schedule-less raids (SR-010)

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-07-30 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade():
    # Allow a JobExecution with no owning Schedule, so an inline (schedule-less)
    # raid triggered via "Raid Now" can be recorded through the same executor
    # safety rails as a scheduled run (SR-010).
    with op.batch_alter_table("job_executions") as batch_op:
        batch_op.alter_column(
            "schedule_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade():
    with op.batch_alter_table("job_executions") as batch_op:
        batch_op.alter_column(
            "schedule_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
