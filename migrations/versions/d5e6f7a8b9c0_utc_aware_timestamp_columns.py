"""Store timestamps as timezone-aware UTC instants (SR-033)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-08-01 00:00:01.000000

Every timestamp column in the schema is written as `datetime.now(timezone.utc)`
but was declared as a naive `DateTime`, so PostgreSQL stored `timestamp without
time zone` and handed the value back naive. This converts them to `timestamptz`.

The stored values are already UTC instants -- that is what the application has
always written. PostgreSQL reads a naive value in the session's `TimeZone`
when converting, so the session is pinned to UTC for the duration
(`SET LOCAL timezone = 'UTC'`) and every value converts in place, unshifted.

Pinning the session rather than writing `USING col AT TIME ZONE 'UTC'` is what
keeps this cheap. Both are correct, but PostgreSQL 12+ skips the table rewrite
for `timestamp` -> `timestamptz` when the session is UTC, and an explicit
`USING` clause defeats that optimization unconditionally -- it forces the
general-purpose rewrite path. Measured on one 200k-row table: 27,320 ms with
`USING` and a changed `relfilenode` (rewritten), against 77 ms and an
unchanged `relfilenode` (catalog only). Across 15 tables that is the
difference between a brief lock and minutes of `ACCESS EXCLUSIVE`.

`batch_alter_table` is what keeps this runnable on SQLite: it has no
`ALTER COLUMN`, and a bare one aborts the whole chain (#504). It is not what
caused the rewrite.
"""

import sqlalchemy as sa
from alembic import op

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


def _convert(target_type):
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # Interpret the stored naive values as UTC, which is what the
        # application has always written. PostgreSQL reads a naive value in
        # the session's TimeZone when converting, so without this the
        # conversion shifts every timestamp by the server's offset.
        #
        # SET LOCAL rather than SET: it is scoped to the migration's
        # transaction and reverts on commit, so it cannot leak into whatever
        # connection the pool hands out next.
        op.execute("SET LOCAL timezone = 'UTC'")

        # Each ALTER takes ACCESS EXCLUSIVE. Briefly -- see below -- but it
        # still has to acquire it, and behind a long-running query it would
        # queue while blocking every reader behind it. Fail fast instead.
        op.execute("SET LOCAL lock_timeout = '5s'")

    for table, column, nullable in TIMESTAMP_COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                column,
                type_=target_type,
                existing_nullable=nullable,
            )


def upgrade():
    _convert(sa.DateTime(timezone=True))


def downgrade():
    # The inverse reading under the same UTC session: render each instant in
    # UTC and drop the offset, returning the naive values that were there
    # before the upgrade.
    _convert(sa.DateTime(timezone=False))
