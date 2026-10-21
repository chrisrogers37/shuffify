"""A read-only Undo review sees confirmed history without polling or cookie mirroring."""

from types import SimpleNamespace
from unittest.mock import patch

from shuffify.services.operations.commands.registry import COMMAND_HANDLERS
from shuffify.services.state_service import StateService
from shuffify.worker import OperationWorker
from tests.mutable_spotify import MutableSpotify, uri
from tests.queued_operations import worker_api


def test_undo_review_reads_the_completed_operation_without_polling(auth_client):
    provider = MutableSpotify(SimpleNamespace(spotify_id="user123"), target=[uri(1), uri(2)])
    with auth_client.session_transaction() as session:
        StateService.initialize_playlist_state(session, "target", [uri(1), uri(2)])
        stale = session["playlist_states"]
        token = dict(session["spotify_token"])
    accepted = auth_client.post("/api/shuffle/target", data={"algorithm": "BasicShuffle"})
    assert accepted.status_code == 202
    runner = OperationWorker(auth_client.application, handlers=COMMAND_HANDLERS, heartbeat_interval=0,
                             api_factory=lambda _user: worker_api(provider, token))
    with patch("shuffify.services.live_playlist_service.ShuffleService.execute", return_value=[uri(2), uri(1)]):
        assert runner.run_once(accepted.json["operation_id"]) is True
    # This is the first page request after completion; no operation poll has occurred.
    with patch("shuffify.routes.require_auth", return_value=provider):
        review = auth_client.post("/api/playlist/target/live-context", json={"kind": "undo"})
    assert review.status_code == 200
    assert review.json["context"]["action"]["desired_track_uris"] == [uri(1), uri(2)]
    assert review.json["context"]["current"]["track_uris"] == [uri(2), uri(1)]
    with auth_client.session_transaction() as session:
        assert session["playlist_states"] == stale
