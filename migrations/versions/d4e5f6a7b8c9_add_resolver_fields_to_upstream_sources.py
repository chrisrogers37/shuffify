"""Add search_query and resolver tracking to upstream_sources

Revision ID: d4e5f6a7b8c9
Revises: cb0799fcc893
Create Date: 2026-03-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'cb0799fcc893'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'upstream_sources',
        sa.Column('search_query', sa.String(500), nullable=True),
    )
    op.add_column(
        'upstream_sources',
        sa.Column('last_resolved_at', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'upstream_sources',
        sa.Column('last_resolve_pathway', sa.String(30), nullable=True),
    )
    op.add_column(
        'upstream_sources',
        sa.Column('last_resolve_status', sa.String(20), nullable=True),
    )
    # Allow NULL source_playlist_id for search_query sources
    op.alter_column(
        'upstream_sources',
        'source_playlist_id',
        existing_type=sa.String(255),
        nullable=True,
    )


def downgrade():
    op.alter_column(
        'upstream_sources',
        'source_playlist_id',
        existing_type=sa.String(255),
        nullable=False,
    )
    op.drop_column('upstream_sources', 'last_resolve_status')
    op.drop_column('upstream_sources', 'last_resolve_pathway')
    op.drop_column('upstream_sources', 'last_resolved_at')
    op.drop_column('upstream_sources', 'search_query')
