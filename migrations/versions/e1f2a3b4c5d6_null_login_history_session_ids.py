"""Clear the raw session ids stored in login history."""

from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade():
    # Login history keeps a keyed digest of each session id. A raw id is the
    # session cookie's value, so a stored one is cleared, not kept.
    op.execute("UPDATE login_history SET session_id = NULL WHERE session_id IS NOT NULL")


def downgrade():
    """Nothing to restore: the cleared ids are gone.

    Kept as a no-op so a rollback can still step the schema back one revision.
    """
