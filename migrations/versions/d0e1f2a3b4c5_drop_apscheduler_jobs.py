"""Drop the job table v1's persistent APScheduler store left behind."""

from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    # The worker schedules from memory and never reads this table. Databases
    # created after v1 never had it.
    op.execute("DROP TABLE IF EXISTS apscheduler_jobs")


def downgrade():
    """Nothing to restore: no release since the cutover reads the table.

    Kept as a no-op so a rollback can still step the schema back one revision.
    """
