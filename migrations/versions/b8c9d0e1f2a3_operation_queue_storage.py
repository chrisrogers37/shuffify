"""Add nullable operation queue storage and a precise schedule eligibility floor."""

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("schedules", sa.Column("definition_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_executions", sa.Column("kind", sa.String(80), nullable=True))
    op.add_column("job_executions", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column("job_executions", sa.Column("targets", sa.JSON(), nullable=True))
    op.add_column("job_executions", sa.Column("payload", sa.JSON(), nullable=True))
    op.add_column("job_executions", sa.Column("state", sa.String(32), nullable=True))
    op.add_column("job_executions", sa.Column("journal", sa.JSON(), nullable=True))
    op.add_column("job_executions", sa.Column("lease_owner", sa.String(128), nullable=True))
    op.add_column("job_executions", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_executions", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_executions", sa.Column("request_key", sa.String(128), nullable=True))
    op.add_column("job_executions", sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_job_executions_schedule_fire", "job_executions", ["schedule_id", "scheduled_for"], unique=True,
    )
    op.create_index(
        "uq_job_executions_user_request", "job_executions", ["user_id", "request_key"], unique=True,
    )
    op.create_index("ix_job_executions_queue_lease", "job_executions", ["state", "lease_expires_at"])


def downgrade():
    op.drop_index("ix_job_executions_queue_lease", table_name="job_executions")
    op.drop_index("uq_job_executions_user_request", table_name="job_executions")
    op.drop_index("uq_job_executions_schedule_fire", table_name="job_executions")
    for name in (
        "scheduled_for", "request_key", "heartbeat_at", "lease_expires_at", "lease_owner",
        "journal", "state", "payload", "targets", "user_id", "kind",
    ):
        op.drop_column("job_executions", name)
    op.drop_column("schedules", "definition_changed_at")
