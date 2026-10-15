"""Closed command schemas and worker handlers for queued direct actions."""

from .direct import shuffle, snapshot_restore, undo, visibility
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
}
COMMAND_HANDLERS = {
    'shuffle': shuffle,
    'undo': undo,
    'visibility': visibility,
    'snapshot_restore': snapshot_restore,
    'workshop_commit': workshop_commit,
}
