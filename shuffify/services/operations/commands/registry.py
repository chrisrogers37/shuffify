"""Closed command schemas and worker handlers for queued playlist actions."""

from .archive_move import UnarchiveCommand, unarchive
from .asset_schemas import AssetCommand
from .assets import HANDLERS as ASSET_HANDLERS
from .direct import shuffle, snapshot_restore, undo, visibility
from .dismiss_schemas import PendingDismissCommand
from .dismissal import pending_dismiss
from .finalizer_schemas import ArchiveAppendCommand, FinalizeRaidsCommand, FinalizeRestoreCommand
from .finalizers import archive_append, finalize_raids, finalize_restore
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
    "pending_dismiss": PendingDismissCommand,
    **{kind: AssetCommand for kind in ASSET_HANDLERS},
    'unarchive': UnarchiveCommand,
    'shuffle': ShuffleCommand,
    'undo': UndoCommand,
    'visibility': VisibilityCommand,
    'snapshot_restore': SnapshotRestoreCommand,
    'workshop_commit': WorkshopCommitCommand,
    'schedule_run': ScheduleRunCommand,
    'raid_now': RaidNowCommand,
    'drip_now': DripNowCommand,
    'raid_and_drip': RaidAndDripCommand,
    'workshop_finalize_raids': FinalizeRaidsCommand,
    'workshop_archive_append': ArchiveAppendCommand,
    'workshop_finalize_restore': FinalizeRestoreCommand,
}
COMMAND_HANDLERS = {
    "pending_dismiss": pending_dismiss,
    **ASSET_HANDLERS,
    'unarchive': unarchive,
    'shuffle': shuffle,
    'undo': undo,
    'visibility': visibility,
    'snapshot_restore': snapshot_restore,
    'workshop_commit': workshop_commit,
    'schedule_run': schedule_run,
    'raid_now': raid_now,
    'drip_now': drip_now,
    'raid_and_drip': raid_and_drip,
    'workshop_finalize_raids': finalize_raids,
    'workshop_archive_append': archive_append,
    'workshop_finalize_restore': finalize_restore,
}
