"""Add missing indexes on PlaylistPreference, PendingRaidTrack, JobExecution

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-05-22 00:00:00.000000

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    # PendingRaidTrack: user_id FK was missing a standalone index
    op.create_index(
        "ix_pending_raid_tracks_user_id",
        "pending_raid_tracks",
        ["user_id"],
    )

    # JobExecution: status and started_at for dashboard/history queries
    op.create_index(
        "ix_job_executions_status",
        "job_executions",
        ["status"],
    )
    op.create_index(
        "ix_job_executions_started_at",
        "job_executions",
        ["started_at"],
    )

    # PlaylistPreference: spotify_playlist_id for cross-user lookups
    op.create_index(
        "ix_playlist_preferences_spotify_playlist_id",
        "playlist_preferences",
        ["spotify_playlist_id"],
    )


def downgrade():
    op.drop_index(
        "ix_playlist_preferences_spotify_playlist_id",
        table_name="playlist_preferences",
    )
    op.drop_index(
        "ix_job_executions_started_at",
        table_name="job_executions",
    )
    op.drop_index(
        "ix_job_executions_status",
        table_name="job_executions",
    )
    op.drop_index(
        "ix_pending_raid_tracks_user_id",
        table_name="pending_raid_tracks",
    )
