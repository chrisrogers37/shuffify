"""Index Schedule.is_enabled and LoginHistory.logged_in_at (SR-043)

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-08-01 00:00:02.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade():
    # Scheduler startup and every reload filter the whole schedules table on
    # is_enabled, and enabled rows are the minority once schedules accumulate.
    op.create_index(
        "ix_schedules_is_enabled",
        "schedules",
        ["is_enabled"],
    )

    # User.login_history is ordered by logged_in_at, so every load of a
    # user's sign-in history sorts on this column.
    op.create_index(
        "ix_login_history_logged_in_at",
        "login_history",
        ["logged_in_at"],
    )


def downgrade():
    op.drop_index(
        "ix_login_history_logged_in_at",
        table_name="login_history",
    )
    op.drop_index(
        "ix_schedules_is_enabled",
        table_name="schedules",
    )
