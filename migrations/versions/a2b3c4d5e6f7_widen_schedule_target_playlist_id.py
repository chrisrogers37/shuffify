"""Widen Schedule.target_playlist_id from String(64) to String(255)

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-05-22 00:00:01.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("schedules") as batch_op:
        batch_op.alter_column(
            "target_playlist_id",
            existing_type=sa.String(64),
            type_=sa.String(255),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table("schedules") as batch_op:
        batch_op.alter_column(
            "target_playlist_id",
            existing_type=sa.String(255),
            type_=sa.String(64),
            existing_nullable=False,
        )
