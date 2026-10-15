"""Worker payloads cannot escape their leased target or frozen owner."""

from types import SimpleNamespace

import pytest

from shuffify.services.operations.commands.direct import shuffle, snapshot_restore, undo, visibility

URI = 'spotify:track:' + 'a' * 22


class GuardRuntime:
    def __init__(self, payload, targets=('target',), user_id=9):
        self.operation = SimpleNamespace(payload=payload, targets=list(targets))
        self.lease = SimpleNamespace(targets=tuple(targets))
        self.user = SimpleNamespace(id=user_id)

    @property
    def api(self):
        pytest.fail('Malformed command constructed Spotify')

    @property
    def history(self):
        pytest.fail('Malformed command loaded history')


COMMANDS = [
    (shuffle, {'playlist_id': 'target', 'algorithm': 'BasicShuffle', 'params': {}}),
    (undo, {'playlist_id': 'target', 'selection': {'states': [[URI], []], 'current_index': 1}}),
    (visibility, {'playlist_id': 'target', 'public': True}),
    (snapshot_restore, {'playlist_id': 'target', 'snapshot': {
        'id': 17, 'user_id': 9, 'playlist_id': 'target', 'track_uris': [URI],
    }}),
]


@pytest.mark.parametrize('handler,command', COMMANDS)
@pytest.mark.parametrize('version', [True, False, None, '1', 2])
def test_invalid_envelope_version_is_rejected_before_any_external_or_history_access(handler, command, version):
    with pytest.raises(ValueError):
        handler(GuardRuntime({'version': version, 'command': command}))


@pytest.mark.parametrize('handler,command', COMMANDS)
@pytest.mark.parametrize('targets', [[], ['other'], ['target', 'other'], ['target', 'target']])
def test_command_target_must_match_the_complete_lease(handler, command, targets):
    with pytest.raises(ValueError):
        handler(GuardRuntime({'version': 1, 'command': command}, targets=targets))


def test_frozen_snapshot_must_belong_to_the_runtime_user():
    handler, command = COMMANDS[-1]
    with pytest.raises(ValueError):
        handler(GuardRuntime({'version': 1, 'command': command}, user_id=10))


@pytest.mark.parametrize('handler,command', [COMMANDS[0], COMMANDS[1], COMMANDS[3]])
def test_a_stale_captured_history_keeps_its_domain_failure_without_constructing_spotify(handler, command):
    from shuffify.services.state_service import StaleHistoryError

    class StaleRuntime(GuardRuntime):
        @property
        def history(self):
            raise StaleHistoryError('history changed')

        def assert_history_revision(self, target):
            raise StaleHistoryError('history changed')

        def check_pending_control(self):
            pass

    result = handler(StaleRuntime({'version': 1, 'command': command}))
    assert result.state == 'failed'
    assert result.result['operation']['error_code'] == 'history_changed'
    assert result.result['operation']['resources'] == []
