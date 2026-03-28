"""Add CHECK constraints, composite index, and fix drift

Revision ID: c3d4e5f6a7b8
Revises: 30c29ec43ceb
Create Date: 2026-03-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = '30c29ec43ceb'
branch_labels = None
depends_on = None


def upgrade():
    # --- Composite index on schedules ---
    op.create_index(
        'ix_schedules_user_target_type',
        'schedules',
        ['user_id', 'target_playlist_id', 'job_type'],
    )

    # --- CHECK constraints ---
    # Schedule job_type and schedule_type
    op.create_check_constraint(
        'ck_schedules_job_type',
        'schedules',
        "job_type IN ('raid', 'shuffle', 'raid_and_shuffle', "
        "'raid_and_drip', 'rotate', 'drip')",
    )
    op.create_check_constraint(
        'ck_schedules_schedule_type',
        'schedules',
        "schedule_type IN ('interval', 'cron')",
    )

    # UpstreamSource source_type and raid_count
    op.create_check_constraint(
        'ck_upstream_source_type',
        'upstream_sources',
        "source_type IN ('own', 'external', 'search_query')",
    )
    op.create_check_constraint(
        'ck_upstream_raid_count_range',
        'upstream_sources',
        "raid_count >= 1 AND raid_count <= 100",
    )

    # UserSettings theme and max_snapshots
    op.create_check_constraint(
        'ck_user_settings_theme',
        'user_settings',
        "theme IN ('light', 'dark', 'system')",
    )
    op.create_check_constraint(
        'ck_user_settings_max_snapshots',
        'user_settings',
        "max_snapshots_per_playlist >= 1 "
        "AND max_snapshots_per_playlist <= 50",
    )

    # PlaylistSnapshot snapshot_type
    op.create_check_constraint(
        'ck_snapshot_type',
        'playlist_snapshots',
        "snapshot_type IN ("
        "'auto_pre_shuffle', 'auto_pre_raid', "
        "'auto_pre_commit', 'auto_pre_rotate', "
        "'auto_pre_drip', 'manual', "
        "'scheduled_pre_execution')",
    )

    # LoginHistory login_type
    op.create_check_constraint(
        'ck_login_history_type',
        'login_history',
        "login_type IN ('oauth_initial', "
        "'oauth_refresh', 'session_resume')",
    )

    # PendingRaidTrack status
    op.create_check_constraint(
        'ck_pending_raid_status',
        'pending_raid_tracks',
        "status IN ('pending', 'promoted', 'dismissed')",
    )

    # RaidPlaylistLink drip_count
    op.create_check_constraint(
        'ck_raid_link_drip_count',
        'raid_playlist_links',
        "drip_count >= 1 AND drip_count <= 50",
    )

    # --- Fix pending_raid_tracks drift ---
    # ORM says NOT NULL but migration created as nullable
    op.alter_column(
        'pending_raid_tracks',
        'track_name',
        existing_type=sa.String(500),
        nullable=False,
    )
    op.alter_column(
        'pending_raid_tracks',
        'created_at',
        existing_type=sa.DateTime(),
        nullable=False,
    )
    # ORM says String(1024) but migration created as String(500)
    op.alter_column(
        'pending_raid_tracks',
        'track_image_url',
        existing_type=sa.String(500),
        type_=sa.String(1024),
        nullable=True,
    )


def downgrade():
    # Revert column changes
    op.alter_column(
        'pending_raid_tracks',
        'track_image_url',
        existing_type=sa.String(1024),
        type_=sa.String(500),
        nullable=True,
    )
    op.alter_column(
        'pending_raid_tracks',
        'created_at',
        existing_type=sa.DateTime(),
        nullable=True,
    )
    op.alter_column(
        'pending_raid_tracks',
        'track_name',
        existing_type=sa.String(500),
        nullable=True,
    )

    # Drop CHECK constraints
    op.drop_constraint(
        'ck_raid_link_drip_count', 'raid_playlist_links',
        type_='check',
    )
    op.drop_constraint(
        'ck_pending_raid_status', 'pending_raid_tracks',
        type_='check',
    )
    op.drop_constraint(
        'ck_login_history_type', 'login_history',
        type_='check',
    )
    op.drop_constraint(
        'ck_snapshot_type', 'playlist_snapshots',
        type_='check',
    )
    op.drop_constraint(
        'ck_user_settings_max_snapshots', 'user_settings',
        type_='check',
    )
    op.drop_constraint(
        'ck_user_settings_theme', 'user_settings',
        type_='check',
    )
    op.drop_constraint(
        'ck_upstream_raid_count_range', 'upstream_sources',
        type_='check',
    )
    op.drop_constraint(
        'ck_upstream_source_type', 'upstream_sources',
        type_='check',
    )
    op.drop_constraint(
        'ck_schedules_schedule_type', 'schedules',
        type_='check',
    )
    op.drop_constraint(
        'ck_schedules_job_type', 'schedules',
        type_='check',
    )

    # Drop composite index
    op.drop_index(
        'ix_schedules_user_target_type', 'schedules',
    )
