"""
Enums for job types, schedule types, and interval values.

Single source of truth for string constants used across schemas,
models, services, and the scheduler.
"""

from enum import StrEnum


class JobType(StrEnum):
    """Types of scheduled jobs."""
    RAID = "raid"
    SHUFFLE = "shuffle"
    RAID_AND_SHUFFLE = "raid_and_shuffle"


class ScheduleType(StrEnum):
    """Schedule trigger types."""
    INTERVAL = "interval"
    CRON = "cron"


class IntervalValue(StrEnum):
    """Predefined interval values for interval schedules."""
    EVERY_6H = "every_6h"
    EVERY_12H = "every_12h"
    DAILY = "daily"
    EVERY_3D = "every_3d"
    WEEKLY = "weekly"


class SnapshotType(StrEnum):
    """Types of playlist snapshots."""
    AUTO_PRE_SHUFFLE = "auto_pre_shuffle"
    AUTO_PRE_RAID = "auto_pre_raid"
    AUTO_PRE_COMMIT = "auto_pre_commit"
    MANUAL = "manual"
    SCHEDULED_PRE_EXECUTION = "scheduled_pre_execution"


class ActivityType(StrEnum):
    """Types of user activities tracked in the activity log."""
    SHUFFLE = "shuffle"
    WORKSHOP_COMMIT = "workshop_commit"
    WORKSHOP_SESSION_SAVE = "workshop_session_save"
    WORKSHOP_SESSION_DELETE = "workshop_session_delete"
    UPSTREAM_SOURCE_ADD = "upstream_source_add"
    UPSTREAM_SOURCE_DELETE = "upstream_source_delete"
    SCHEDULE_CREATE = "schedule_create"
    SCHEDULE_UPDATE = "schedule_update"
    SCHEDULE_DELETE = "schedule_delete"
    SCHEDULE_TOGGLE = "schedule_toggle"
    SCHEDULE_RUN = "schedule_run"
    SNAPSHOT_CREATE = "snapshot_create"
    SNAPSHOT_RESTORE = "snapshot_restore"
    SNAPSHOT_DELETE = "snapshot_delete"
    ARCHIVE_TRACKS = "archive_tracks"
    UNARCHIVE_TRACKS = "unarchive_tracks"
    PAIR_CREATE = "pair_create"
    PAIR_DELETE = "pair_delete"
    SETTINGS_CHANGE = "settings_change"
    LOGIN = "login"
    LOGOUT = "logout"
