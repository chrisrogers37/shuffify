"""Add unique constraint on scraped_playlist_cache.playlist_id

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-22 12:00:00.000000

"""
from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def _constraint_exists(table_name, constraint_name):
    """Check if a constraint exists on a table."""
    bind = op.get_bind()
    insp = inspect(bind)
    constraints = insp.get_unique_constraints(table_name)
    return any(
        c['name'] == constraint_name for c in constraints
    )


def upgrade():
    if not _constraint_exists(
        'scraped_playlist_cache',
        'uq_scrape_cache_playlist_id',
    ):
        # Remove duplicate rows first (keep newest)
        op.execute("""
            DELETE FROM scraped_playlist_cache
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM scraped_playlist_cache
                GROUP BY playlist_id
            )
        """)
        op.create_unique_constraint(
            'uq_scrape_cache_playlist_id',
            'scraped_playlist_cache',
            ['playlist_id'],
        )


def downgrade():
    op.drop_constraint(
        'uq_scrape_cache_playlist_id',
        'scraped_playlist_cache',
        type_='unique',
    )
