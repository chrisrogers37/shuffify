"""Add pending_raid_tracks table

Revision ID: 0a39653cc7d7
Revises: 68f283a9bf88
Create Date: 2026-03-05 08:54:34.403016

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0a39653cc7d7'
down_revision = '68f283a9bf88'
branch_labels = None
depends_on = None


def upgrade():
    # Create pending_raid_tracks table if not exists
    op.create_table(
        'pending_raid_tracks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'target_playlist_id',
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            'track_uri',
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            'track_name',
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            'track_artists',
            sa.String(length=1000),
            nullable=True,
        ),
        sa.Column(
            'track_album',
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            'track_image_url',
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            'track_duration_ms',
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            'source_playlist_id',
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            'source_name',
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            'status',
            sa.String(length=20),
            nullable=False,
            server_default='pending',
        ),
        sa.Column(
            'created_at',
            sa.DateTime(),
            server_default=sa.text('now()'),
            nullable=True,
        ),
        sa.Column(
            'resolved_at',
            sa.DateTime(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id',
            'target_playlist_id',
            'track_uri',
            name='uq_pending_raid_track',
        ),
    )
    op.create_index(
        'ix_pending_raid_user_playlist_status',
        'pending_raid_tracks',
        ['user_id', 'target_playlist_id', 'status'],
    )

    # Add upstream_sources columns for source resolver
    with op.batch_alter_table(
        'upstream_sources', schema=None
    ) as batch_op:
        batch_op.add_column(sa.Column(
            'search_query',
            sa.String(length=500),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            'last_resolved_at',
            sa.DateTime(),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            'last_resolve_pathway',
            sa.String(length=30),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            'last_resolve_status',
            sa.String(length=20),
            nullable=True,
        ))
        batch_op.alter_column(
            'source_playlist_id',
            existing_type=sa.VARCHAR(length=255),
            nullable=True,
        )


def downgrade():
    op.drop_index(
        'ix_pending_raid_user_playlist_status',
        table_name='pending_raid_tracks',
    )
    op.drop_table('pending_raid_tracks')

    with op.batch_alter_table(
        'upstream_sources', schema=None
    ) as batch_op:
        batch_op.alter_column(
            'source_playlist_id',
            existing_type=sa.VARCHAR(length=255),
            nullable=False,
        )
        batch_op.drop_column('last_resolve_status')
        batch_op.drop_column('last_resolve_pathway')
        batch_op.drop_column('last_resolved_at')
        batch_op.drop_column('search_query')
