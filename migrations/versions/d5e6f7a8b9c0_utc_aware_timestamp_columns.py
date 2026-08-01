"""Store timestamps as timezone-aware UTC instants (SR-033)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-08-01 00:00:01.000000

Every timestamp column in the schema is written as `datetime.now(timezone.utc)`
but was declared as a naive `DateTime`, so PostgreSQL stored `timestamp without
time zone` and handed the value back naive. This converts them to `timestamptz`.

The stored values are already UTC instants -- that is what the application has
always written -- so the conversion states that explicitly with
`USING col AT TIME ZONE 'UTC'`, which PostgreSQL applies without shifting any
value. Omitting it would make PostgreSQL reinterpret each naive value in the
server's `TimeZone` setting, silently shifting every timestamp in the database
on any server not set to UTC.

`batch_alter_table` is what keeps this runnable on SQLite: it has no
`ALTER COLUMN`, and a bare one aborts the whole chain (#504).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


# (table, column, nullable) for every timestamp column in the schema.
TIMESTAMP_COLUMNS = [
    ("users", "last_login_at", True),
    ("users", "created_at", False),
    ("users", "updated_at", False),
    ("user_settings", "created_at", False),
    ("user_settings", "updated_at", False),
    ("workshop_sessions", "created_at", False),
    ("workshop_sessions", "updated_at", False),
    ("upstream_sources", "last_resolved_at", True),
    ("upstream_sources", "created_at", False),
    ("schedules", "last_run_at", True),
    ("schedules", "created_at", False),
    ("schedules", "updated_at", False),
    ("job_executions", "started_at", False),
    ("job_executions", "completed_at", True),
    ("login_history", "logged_in_at", False),
    ("login_history", "logged_out_at", True),
    ("playlist_snapshots", "created_at", False),
    ("activity_log", "created_at", False),
    ("playlist_pairs", "created_at", False),
    ("playlist_pairs", "updated_at", False),
    ("raid_playlist_links", "created_at", False),
    ("raid_playlist_links", "updated_at", False),
    ("playlist_preferences", "created_at", False),
    ("playlist_preferences", "updated_at", False),
    ("track_locks", "created_at", False),
    ("track_locks", "expires_at", True),
    ("pending_raid_tracks", "created_at", False),
    ("pending_raid_tracks", "resolved_at", True),
    ("scraped_playlist_cache", "scraped_at", False),
    ("scraped_playlist_cache", "expires_at", False),
]


def _convert(target_type, using_suffix):
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    for table, column, nullable in TIMESTAMP_COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                column,
                type_=target_type,
                existing_nullable=nullable,
                # SQLite rewrites the table wholesale under batch mode and has
                # no USING clause; only PostgreSQL needs (or accepts) one.
                postgresql_using=(f"{column} {using_suffix}" if is_postgres else None),
            )


def upgrade():
    _convert(sa.DateTime(timezone=True), "AT TIME ZONE 'UTC'")


def downgrade():
    # The inverse reading: render each instant in UTC, then drop the offset,
    # returning exactly the naive values that were there before the upgrade.
    _convert(sa.DateTime(timezone=False), "AT TIME ZONE 'UTC'")
