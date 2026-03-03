"""Add last_track_count to upstream_sources

Revision ID: 68f283a9bf88
Revises: d4e5f6a7b8c9
Create Date: 2026-03-03 00:48:54.434115

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '68f283a9bf88'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(
        'upstream_sources', schema=None
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                'last_track_count',
                sa.Integer(),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table(
        'upstream_sources', schema=None
    ) as batch_op:
        batch_op.drop_column('last_track_count')
