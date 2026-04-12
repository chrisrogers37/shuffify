"""Add track_locks table

Revision ID: e5f6a7b8c9d0
Revises: c3d4e5f6a7b8
Create Date: 2026-04-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'track_locks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('spotify_playlist_id', sa.String(255), nullable=False),
        sa.Column('track_uri', sa.String(255), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('lock_tier', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'spotify_playlist_id', 'track_uri',
            name='uq_track_lock_user_playlist_track',
        ),
    )
    op.create_index('ix_track_locks_user_id', 'track_locks', ['user_id'])
    op.create_index(
        'ix_track_lock_playlist',
        'track_locks',
        ['user_id', 'spotify_playlist_id'],
    )


def downgrade():
    op.drop_index('ix_track_lock_playlist', table_name='track_locks')
    op.drop_index('ix_track_locks_user_id', table_name='track_locks')
    op.drop_table('track_locks')
