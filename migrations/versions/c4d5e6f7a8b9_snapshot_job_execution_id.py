"""Add PlaylistSnapshot.job_execution_id for job-scoped rollback (SR-019)

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-07-30 00:00:01.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade():
    # Tag auto-snapshots with the JobExecution that created them, so rollback
    # restores only that job's snapshots instead of a time window (SR-019).
    with op.batch_alter_table("playlist_snapshots") as batch_op:
        batch_op.add_column(
            sa.Column(
                "job_execution_id", sa.Integer(), nullable=True
            )
        )
        batch_op.create_index(
            "ix_playlist_snapshots_job_execution_id",
            ["job_execution_id"],
        )
        batch_op.create_foreign_key(
            "fk_playlist_snapshots_job_execution_id",
            "job_executions",
            ["job_execution_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("playlist_snapshots") as batch_op:
        batch_op.drop_constraint(
            "fk_playlist_snapshots_job_execution_id",
            type_="foreignkey",
        )
        batch_op.drop_index(
            "ix_playlist_snapshots_job_execution_id"
        )
        batch_op.drop_column("job_execution_id")
