"""Add playlist_pairs table

Revision ID: ab278f3938c4
Revises: 05ca11d7c80b
Create Date: 2026-02-15 07:01:46.122545

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ab278f3938c4'
down_revision = '05ca11d7c80b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'playlist_pairs',
        sa.Column(
            'id', sa.Integer(),
            autoincrement=True, nullable=False,
        ),
        sa.Column(
            'user_id', sa.Integer(), nullable=False,
        ),
        sa.Column(
            'production_playlist_id',
            sa.String(length=255), nullable=False,
        ),
        sa.Column(
            'production_playlist_name',
            sa.String(length=255), nullable=True,
        ),
        sa.Column(
            'archive_playlist_id',
            sa.String(length=255), nullable=False,
        ),
        sa.Column(
            'archive_playlist_name',
            sa.String(length=255), nullable=True,
        ),
        sa.Column(
            'auto_archive_on_remove',
            sa.Boolean(), nullable=False,
            server_default=sa.text('true'),
        ),
        sa.Column(
            'created_at', sa.DateTime(), nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'production_playlist_id',
            name='uq_user_production_playlist',
        ),
    )
    op.create_index(
        op.f('ix_playlist_pairs_user_id'),
        'playlist_pairs', ['user_id'], unique=False,
    )


def downgrade():
    op.drop_index(
        op.f('ix_playlist_pairs_user_id'),
        table_name='playlist_pairs',
    )
    op.drop_table('playlist_pairs')
