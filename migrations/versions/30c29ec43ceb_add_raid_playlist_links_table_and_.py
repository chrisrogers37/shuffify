"""add raid_playlist_links table and upstream_source raid_count

Revision ID: 30c29ec43ceb
Revises: b2c3d4e5f6a7
Create Date: 2026-03-23 19:08:45.541331

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '30c29ec43ceb'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'raid_playlist_links',
        sa.Column('id', sa.Integer(), autoincrement=True,
                  nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('target_playlist_id', sa.String(255),
                  nullable=False),
        sa.Column('target_playlist_name', sa.String(255),
                  nullable=True),
        sa.Column('raid_playlist_id', sa.String(255),
                  nullable=False),
        sa.Column('raid_playlist_name', sa.String(255),
                  nullable=True),
        sa.Column('drip_count', sa.Integer(), nullable=False,
                  server_default='3'),
        sa.Column('drip_enabled', sa.Boolean(), nullable=False,
                  server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'target_playlist_id',
            name='uq_raid_link_user_target',
        ),
    )
    op.create_index(
        'ix_raid_playlist_links_user_id',
        'raid_playlist_links',
        ['user_id'],
    )

    op.add_column(
        'upstream_sources',
        sa.Column('raid_count', sa.Integer(), nullable=False,
                  server_default='5'),
    )


def downgrade():
    op.drop_column('upstream_sources', 'raid_count')
    op.drop_index(
        'ix_raid_playlist_links_user_id',
        table_name='raid_playlist_links',
    )
    op.drop_table('raid_playlist_links')
