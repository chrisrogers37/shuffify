"""Writer preparation binds the baseline and survives swallowed service controls."""

from copy import deepcopy

import pytest

from shuffify.services.operations.journal import DurableJournal, NeedsAttention, OperationAborted
from tests.services.operations.test_crash_recovery import World, uri


def test_prepared_baseline_mismatch_never_freezes_a_new_write():
    world = World([uri(1)])
    expected = world.read('target')
    world.playlists['target']['track_uris'] = [uri(99)]
    world.playlists['target']['snapshot_id'] = 'external'
    journal = world.journal()
    with pytest.raises(NeedsAttention):
        journal.ensure_replace('target', [uri(2)], expected_baseline=expected)
    assert world.writes == []
    assert world.saved['status'] == 'needs_attention'


def test_metadata_scope_never_reads_tracks_and_rejects_item_mutations():
    world = World([uri(1)])
    reads = []

    def metadata(resource):
        reads.append(resource)
        state = world.playlists[resource]
        return {'metadata': deepcopy(state['metadata']), 'snapshot_id': state['snapshot_id']}

    def forbidden(*args):
        raise AssertionError('Privacy must not read track items')

    journal = DurableJournal(lambda: world.saved, world.checkpoint, forbidden, world.dispatch, read_metadata=metadata)
    journal.freeze_details('target', public=True)
    assert journal.resume()['state'] == 'succeeded'
    assert reads
    assert world.playlists['target']['track_uris'] == [uri(1)]
    assert world.saved['resources']['target']['scope'] == 'metadata'
    with pytest.raises(NeedsAttention):
        journal.freeze_replace('target', [uri(2)])


def test_abort_direction_survives_a_swallowed_exception_and_process_restart():
    world = World([uri(1)])
    journal = world.journal()
    journal.freeze_replace('target', [uri(2)])
    journal.resume()
    with pytest.raises(OperationAborted):
        journal.request_abort('service_requested_compensation')
    written = deepcopy(world.writes)
    recovered = world.journal()
    with pytest.raises(OperationAborted):
        recovered.resume()
    with pytest.raises(OperationAborted):
        recovered.check_pending_control()
    assert world.writes == written
    assert recovered.compensate()['state'] == 'rolled_back'
    assert world.playlists['target']['track_uris'] == [uri(1)]


def test_metadata_scope_does_not_treat_a_track_only_snapshot_change_as_a_conflict():
    world = World([uri(1)])

    def metadata(resource):
        state = world.playlists[resource]
        return {'metadata': deepcopy(state['metadata']), 'snapshot_id': state['snapshot_id']}
    journal = DurableJournal(lambda: world.saved, world.checkpoint, world.read, world.dispatch, read_metadata=metadata)
    journal.freeze_details('target', public=True)
    world.playlists['target']['track_uris'] = [uri(9)]
    world.playlists['target']['snapshot_id'] = 'external-track-edit'
    assert journal.resume()['state'] == 'succeeded'
    assert world.playlists['target']['track_uris'] == [uri(9)]
