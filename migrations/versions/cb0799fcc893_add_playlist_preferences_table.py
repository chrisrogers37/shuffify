"""Add playlist_preferences table

Revision ID: cb0799fcc893
Revises: ab278f3938c4
Create Date: 2026-02-25 10:37:47.906640

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cb0799fcc893'
down_revision = 'ab278f3938c4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'playlist_preferences',
        sa.Column(
            'id',
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            'user_id',
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            'spotify_playlist_id',
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            'sort_order',
            sa.Integer(),
            nullable=False,
            server_default='0',
        ),
        sa.Column(
            'is_hidden',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
        sa.Column(
            'is_pinned',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id',
            'spotify_playlist_id',
            name='uq_user_spotify_playlist',
        ),
    )
    op.create_index(
        op.f('ix_playlist_preferences_user_id'),
        'playlist_preferences',
        ['user_id'],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f('ix_playlist_preferences_user_id'),
        table_name='playlist_preferences',
    )
    op.drop_table('playlist_preferences')
