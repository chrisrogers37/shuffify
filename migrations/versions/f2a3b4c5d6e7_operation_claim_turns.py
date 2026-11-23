"""Index the latest ordinary worker turn for each queue owner."""

import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_executions", sa.Column("last_claimed_at", sa.DateTime(timezone=True), nullable=True))
    claimed = sa.text("last_claimed_at IS NOT NULL")
    op.create_index("ix_job_executions_owner_turn", "job_executions", ["user_id", "last_claimed_at"],
                    postgresql_where=claimed, sqlite_where=claimed)


def downgrade():
    op.drop_index("ix_job_executions_owner_turn", table_name="job_executions")
    op.drop_column("job_executions", "last_claimed_at")
