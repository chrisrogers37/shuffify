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
