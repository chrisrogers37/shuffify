"""Index playlist membership for admission without rewriting stored target arrays."""

import sqlalchemy as sa
from alembic import op

revision = "948c1d4ea673"
down_revision = "33db533bb9ac"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '1s'")
        op.execute("SET LOCAL statement_timeout = '3s'")
        op.create_index(
            "ix_job_executions_blocking_targets", "job_executions", [sa.text("(targets::jsonb)")],
            postgresql_using="gin",
            postgresql_where=sa.text("kind IS NOT NULL AND state IN ('queued', 'running', 'compensating', "
                                     "'needs_attention')"),
        )


def downgrade():
    if op.get_context().dialect.name == "postgresql":
        op.drop_index("ix_job_executions_blocking_targets", table_name="job_executions")
