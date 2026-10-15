"""Closed command schemas and worker handlers for queued playlist actions."""

from .direct import shuffle, snapshot_restore, undo, visibility
from .routine_schemas import DripNowCommand, RaidAndDripCommand, RaidNowCommand, ScheduleRunCommand
from .routines import drip_now, raid_and_drip, raid_now, schedule_run
from .schemas import (
    ShuffleCommand,
    SnapshotRestoreCommand,
    UndoCommand,
    VisibilityCommand,
    WorkshopCommitCommand,
)
from .workshop import workshop_commit

COMMAND_SCHEMAS = {
    'shuffle': ShuffleCommand,
    'undo': UndoCommand,
    'visibility': VisibilityCommand,
    'snapshot_restore': SnapshotRestoreCommand,
    'workshop_commit': WorkshopCommitCommand,
    'schedule_run': ScheduleRunCommand,
    'raid_now': RaidNowCommand,
    'drip_now': DripNowCommand,
    'raid_and_drip': RaidAndDripCommand,
}
COMMAND_HANDLERS = {
    'shuffle': shuffle,
    'undo': undo,
    'visibility': visibility,
    'snapshot_restore': snapshot_restore,
    'workshop_commit': workshop_commit,
    'schedule_run': schedule_run,
    'raid_now': raid_now,
    'drip_now': drip_now,
    'raid_and_drip': raid_and_drip,
}
