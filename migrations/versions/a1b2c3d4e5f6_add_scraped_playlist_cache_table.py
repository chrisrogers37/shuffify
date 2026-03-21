"""Add scraped_playlist_cache table

Revision ID: a1b2c3d4e5f6
Revises: 0a39653cc7d7
Create Date: 2026-03-21 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '0a39653cc7d7'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    """Check if a table exists in the database."""
    bind = op.get_bind()
    return inspect(bind).has_table(table_name)


def upgrade():
    if not _table_exists('scraped_playlist_cache'):
        op.create_table(
            'scraped_playlist_cache',
            sa.Column(
                'id', sa.Integer(), nullable=False
            ),
            sa.Column(
                'playlist_id',
                sa.String(length=255),
                nullable=False,
            ),
            sa.Column(
                'track_uris_json',
                sa.Text(),
                nullable=False,
            ),
            sa.Column(
                'track_count',
                sa.Integer(),
                nullable=False,
                server_default='0',
            ),
            sa.Column(
                'scraped_at',
                sa.DateTime(),
                server_default=sa.text('now()'),
                nullable=False,
            ),
            sa.Column(
                'scrape_pathway',
                sa.String(length=50),
                nullable=True,
            ),
            sa.Column(
                'expires_at',
                sa.DateTime(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            'ix_scrape_cache_playlist_id',
            'scraped_playlist_cache',
            ['playlist_id'],
        )
        op.create_index(
            'ix_scrape_cache_playlist_expires',
            'scraped_playlist_cache',
            ['playlist_id', 'expires_at'],
        )


def downgrade():
    op.drop_index(
        'ix_scrape_cache_playlist_expires',
        table_name='scraped_playlist_cache',
    )
    op.drop_index(
        'ix_scrape_cache_playlist_id',
        table_name='scraped_playlist_cache',
    )
    op.drop_table('scraped_playlist_cache')
