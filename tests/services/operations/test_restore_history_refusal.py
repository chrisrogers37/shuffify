"""A pre-dispatch history refusal must not indefinitely reserve the parent restoration."""

from shuffify.models.db import ActivityLog
from shuffify.services.operations.history import HistoryError, HistoryStore
from shuffify.services.operations.restore_attempts import conclusively_no_dispatch
from tests.services.operations.test_restore_command import restoration as restoration
from tests.services.operations.test_routine_commands import routine as routine


def test_history_failure_before_composite_is_conclusive_and_permits_a_later_restore(restoration, monkeypatch):
    row = restoration.enqueue_restore()

    def unavailable(*args, **kwargs):
        raise HistoryError('The confirmed history record is unavailable')

    with monkeypatch.context() as patches:
        patches.setattr(HistoryStore, 'session_for', unavailable)
        finished, refused = restoration.run_restore(row)
    assert finished and refused.state == 'failed'
    assert restoration.world.writes == [] and ActivityLog.query.count() == 0
    assert not refused.journal.get('plans') and not refused.journal.get('boundaries', {}).get('accepted')
    assert conclusively_no_dispatch(refused)
    assert not restoration.repository.get(restoration.parent_id).journal.get('runtime', {}).get('restoration')
    next_row = restoration.enqueue_restore()
    assert next_row.id != row.id
    finished, completed = restoration.run_restore(next_row)
    assert finished and completed.state == 'succeeded'
    assert len(restoration.world.writes) == 1 and ActivityLog.query.count() == 1
