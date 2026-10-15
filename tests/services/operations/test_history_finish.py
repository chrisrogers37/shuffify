"""A verified external result survives a failure to confirm its saved history."""

from unittest.mock import patch

from shuffify.models.db import User, db
from shuffify.services.operations.history import HistoryStore, get_history_scope
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.operations.runtime import OperationRuntime
from shuffify.services.state_service import StateService
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import TokenInfo
from shuffify.worker import OperationWorker
from tests.services.operations.test_worker_journal_integration import PLAYLIST, SpotifyWorld


def test_verified_write_with_failed_history_keeps_rich_result_without_compensation(db_app, sample_token, monkeypatch):
    user = User(spotify_id="alice")
    db.session.add(user)
    db.session.commit()
    repository, store, scope = OperationRepository(), HistoryStore(), get_history_scope({})
    before, desired = ["spotify:track:before"], ["spotify:track:after"]
    world = SpotifyWorld(before)
    operation = repository.enqueue(kind="example", user_id=user.id, targets=[PLAYLIST], payload={
        "history": store.capture(user.id, scope, [PLAYLIST]),
    })

    def api_factory(_user):
        api = SpotifyAPI(TokenInfo.from_dict(sample_token), auto_refresh=False)
        monkeypatch.setattr(api._http._session, "request", world.send)
        return api

    monkeypatch.setattr(OperationRuntime, "read_resource", lambda self, target: world.observe(target))
    monkeypatch.setattr(OperationRuntime, "read_snapshot", lambda self, target: world.observe(target)["snapshot_id"])

    def handler(runtime):
        runtime.step("write", lambda: {"success": runtime.api.update_playlist_tracks(PLAYLIST, desired)})
        try:
            StateService.record_verified_change(runtime.history, PLAYLIST, before_uris=before, after_uris=desired)
        except RuntimeError:
            runtime.mark_history_unconfirmed()
        return {"success": True, "playlist": {"id": PLAYLIST}, "tracks_total": 1,
                "operation": {"resources": [{"status": "complete", "verified": True,
                                               "current_track_uris": desired}]}}

    with patch.object(StateService, "record_verified_change", autospec=True,
                      side_effect=RuntimeError("Private history failure")):
        runner = OperationWorker(db_app, repository=repository, handlers={"example": handler},
                                 api_factory=api_factory, heartbeat_interval=0)
        assert runner.run_once(operation.id) is True
    row = repository.get(operation.id)
    assert row.state == "needs_attention"
    assert row.journal["runtime"]["history_unconfirmed"] is True
    assert "history" not in row.journal
    assert row.journal["result"]["error_code"] == "history_confirmation_failed"
    assert row.journal["result"]["playlist"] == {"id": PLAYLIST}
    assert row.journal["result"]["operation"]["resources"][0]["verified"] is True
    assert world.calls == 1
    assert world.uris == desired
    assert store.load(user.id, scope, PLAYLIST) == (None, None)
    assert "Private history failure" not in str(row.journal["result"])
