"""Queue payloads carry explicit intent and exact review comparison fields."""

import pytest
from pydantic import ValidationError

from shuffify.services.operations.commands.schemas import (
    ShuffleCommand,
    SnapshotRestoreCommand,
    UndoCommand,
    VisibilityCommand,
    WorkshopCommitCommand,
)

URI = 'spotify:track:' + 'a' * 22


def test_shuffle_normalizes_only_relevant_algorithm_parameters():
    command = ShuffleCommand(playlist_id='target', algorithm='BasicShuffle', params={'keep_first': 2})
    assert command.model_dump() == {
        'playlist_id': 'target', 'algorithm': 'BasicShuffle', 'params': {'keep_first': 2}, 'reviewed': None,
    }
    with pytest.raises(ValidationError):
        ShuffleCommand(playlist_id='target', algorithm='missing', params={})
    with pytest.raises(ValidationError):
        ShuffleCommand(playlist_id='target', algorithm='BasicShuffle', params={'keep_first': -1})


@pytest.mark.parametrize('public', [None, 1, 0, 'true', 'false'])
def test_visibility_requires_an_actual_boolean(public):
    with pytest.raises(ValidationError):
        VisibilityCommand(playlist_id='target', public=public)


@pytest.mark.parametrize('schema,fields', [
    (ShuffleCommand, {'algorithm': 'BasicShuffle', 'params': {}}),
    (UndoCommand, {'selection': {'states': [[URI], []], 'current_index': 1}}),
    (VisibilityCommand, {'public': True}),
])
def test_review_binding_shape_is_exact(schema, fields):
    with pytest.raises(ValidationError):
        schema(playlist_id='target', reviewed={'reviewed_scope': 'bad'}, **fields)
    with pytest.raises(ValidationError):
        schema(playlist_id='target', reviewed={'reviewed_scope': 'a' * 64, 'unexpected': 'b' * 64}, **fields)


def test_undo_selection_and_snapshot_orders_preserve_duplicate_occurrences():
    undo = UndoCommand(playlist_id='target', selection={'states': [[URI, URI], []], 'current_index': 1})
    assert undo.selection.desired_uris == [URI, URI]
    snapshot = SnapshotRestoreCommand(playlist_id='target', snapshot={
        'id': 1, 'user_id': 9, 'playlist_id': 'target', 'track_uris': [URI, URI],
    })
    assert snapshot.snapshot.track_uris == [URI, URI]
    with pytest.raises(ValidationError):
        SnapshotRestoreCommand(playlist_id='other', snapshot=snapshot.snapshot)
    with pytest.raises(ValidationError):
        UndoCommand(playlist_id='target', selection={'states': [[URI]], 'current_index': 0})


def test_workshop_accepts_empty_arrays_but_rejects_invalid_uris_and_unknown_fields():
    assert WorkshopCommitCommand(playlist_id='target', baseline_track_uris=[], track_uris=[]).track_uris == []
    with pytest.raises(ValidationError):
        WorkshopCommitCommand(playlist_id='target', baseline_track_uris=[], track_uris=['local:bad'])
    with pytest.raises(ValidationError):
        WorkshopCommitCommand(playlist_id='target', baseline_track_uris=[], track_uris=[], user_id=42)
