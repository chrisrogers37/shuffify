"""Crash every HTTP call and the durable acceptance/rollback boundaries of a compound operation."""

from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from shuffify.services.operations.journal import OperationAborted
from shuffify.spotify.http_client import SpotifyHTTPClient
from tests.services.operations.test_composite_journal import api
from tests.services.operations.test_crash_recovery import Crash, World, uri


class CompoundWorld(World):
    def __init__(self, size):
        super().__init__([uri(10 + index % 19) for index in range(max(1, size))])
        self.playlists['production'] = deepcopy(self.playlists['target'])
        self.playlists['production']['track_uris'] = [uri(900), uri(900)]
        self.original = deepcopy(self.playlists)
        self.values = [uri(300)] + [uri(10 + index % 19) for index in range(size - 1)] if size else []
        self.local_rows = []

    def accept_collect(self, journal):
        proof = journal.verify_step('collect')
        document = deepcopy(self.saved)
        result = {'staged_ids': [11, 12]}
        document.setdefault('runtime', {}).setdefault('local_steps', {})['accept.collect'] = result
        document = journal.accept_step_document(document, 'collect', proof=proof, result=result)
        self.point('local.before')
        self.point('checkpoint.before')
        # The local effects, receipt, and accepted boundary share one commit point.
        self.saved = deepcopy(document)
        self.local_rows.append({'operation': 'collect', 'staged_ids': [11, 12]})
        self.point('checkpoint.after')
        self.point('local.after')
        journal.reload_latest()


def run_compound(world):
    journal, client = world.journal(), api()
    http = SpotifyHTTPClient('fixture')

    def request(method, url, **kwargs):
        result = world.dispatch(method, urlsplit(url).path.removeprefix('/v1'), kwargs['json'])
        return SimpleNamespace(status_code=200, content=b'json' if result else b'', json=lambda: result)

    http._session.request = request
    journal.dispatch = lambda method, path, body: http._request(method, path, json=body)
    try:
        with journal.observe():
            if journal.doc.get('abort') or journal.doc['phase'] == 'compensating':
                return journal.compensate()
            if journal.doc['status'] == 'failed':
                return journal.outcome()
            # Recovery reconciles every frozen resource before the handler continues.
            if journal.doc['plans']:
                assert journal.resume()['state'] == 'succeeded'
            accepted = journal.accepted_step('collect')
            if accepted is None:
                journal.begin_step('collect')
                journal.ensure_add('target', [uri(300), uri(300)], expected_baseline=world.original['target'])
                client.playlist_add_items('target', [uri(300), uri(300)])
                world.accept_collect(journal)
                accepted = journal.accepted_step('collect')
            else:
                journal.skip_to(accepted['end'])
            world.point('output.before')
            assert accepted['result'] == {'staged_ids': [11, 12]}
            world.point('output.after')
            collected = deepcopy(journal.doc['plans'][accepted['end'] - 1]['desired'])
            collected['snapshot_id'] = journal.doc['plans'][accepted['end'] - 1]['batches'][-1]['snapshot_id']
            journal.begin_step('drip')
            journal.ensure_add('production', world.values, position=0,
                               expected_baseline=world.original['production'])
            client.playlist_add_items('production', world.values, position=0)
            journal.ensure_remove('target', world.values, expected_baseline=collected)
            client.playlist_remove_items('target', world.values)
            with pytest.raises(OperationAborted):
                journal.request_abort('drip_failed')
            return journal.compensate()
    finally:
        http.close()


@pytest.mark.parametrize('size', [0, 1, 99, 100, 101, 250, 1000])
def test_every_compound_crash_retains_the_accepted_collect_and_only_rolls_back_drip(size):
    probe = CompoundWorld(size)
    expected_state = 'rolled_back' if size else 'failed'
    assert run_compound(probe)['state'] == expected_state
    assert 'local.before' in probe.trace
    assert 'output.after' in probe.trace
    assert 'spotify.applied' in probe.trace

    # Every HTTP call is cut before dispatch and after application/response for every size.
    # Zero, one, and multi-batch 101 additionally cut every read and durable checkpoint;
    # this covers all state transitions without repeating the large JSON-copy cost at 1000.
    cuts = [index for index, label in enumerate(probe.trace, 1)
            if size in {0, 1, 101} or label.startswith(('spotify.', 'local.', 'output.'))]
    for cut in cuts:
        world = CompoundWorld(size)
        world.cut = cut
        with pytest.raises(Crash):
            run_compound(world)
        world.cut = None
        outcome = run_compound(world)
        assert outcome['state'] == expected_state, (size, cut, probe.trace[cut - 1], outcome)
        assert world.playlists['target']['track_uris'] == (
            world.original['target']['track_uris'] + [uri(300), uri(300)]
        ), (size, cut)
        assert world.playlists['production']['track_uris'] == [uri(900), uri(900)], (size, cut)
        assert world.local_rows == [{'operation': 'collect', 'staged_ids': [11, 12]}], (size, cut)
        assert world.saved['runtime']['local_steps']['accept.collect'] == {'staged_ids': [11, 12]}
        assert world.saved['boundaries']['accepted'][0]['name'] == 'collect'
        assert world.saved['resources']['target']['baseline']['track_uris'] == world.original['target']['track_uris']
        # Repeated recovery after terminal rollback cannot introduce forward or compensation writes.
        written = deepcopy(world.writes)
        assert run_compound(world)['state'] == expected_state
        assert world.writes == written
