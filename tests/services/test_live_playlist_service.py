"""Direct track inspection against real session history and mutable Spotify state."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from shuffify.models.db import ActivityLog, JobExecution
from shuffify.services.live_playlist_service import LivePlaylistService
from shuffify.services.user_service import UserService
from tests.mutable_spotify import MutableSpotify, uri


@pytest.fixture
def owner(db_app):
    return UserService.upsert_from_spotify({"id": "direct-owner", "display_name": "Owner", "images": []}).user


def seed_history(session, states, index=None):
    session["playlist_states"] = {
        "target": {"states": deepcopy(states), "current_index": len(states) - 1 if index is None else index}
    }
    session.modified = False


@pytest.mark.parametrize("intent", [None, [], [uri(1)], [uri(2)]])
def test_inspection_reads_only_and_cannot_infer_lost_random_intent(owner, intent):
    api = MutableSpotify(owner, target=[uri(1)])
    with (
        patch("shuffify.services.playlist_lock._target_lock") as lock,
        patch("shuffify.services.live_playlist_service.ShuffleService.execute") as shuffle,
        patch("shuffify.services.playlist_snapshot_service.PlaylistSnapshotService.create_snapshot") as snapshot,
    ):
        result = LivePlaylistService(api).inspect_tracks("target", owner, desired_track_uris=intent)
    assert result["inspection_only"] is True
    assert result["operation"]["aggregate_status"] == ("complete" if intent == [uri(1)] else "unknown")
    assert result["operation"]["retryable"] is False
    assert result["operation"]["resources"][0]["current_track_uris"] == [uri(1)]
    assert api.writes == []
    assert ActivityLog.query.count() == JobExecution.query.count() == 0
    shuffle.assert_not_called()
    snapshot.assert_not_called()
    lock.assert_not_called()


def test_track_inspection_reports_no_prewrite_baseline(owner):
    api = MutableSpotify(owner, target=[uri(1), uri(1)])
    result = LivePlaylistService(api).inspect_tracks("target", owner, desired_track_uris=[uri(1), uri(1)])
    assert result["operation"]["aggregate_status"] == "complete"
    entry = result["operation"]["resources"][0]
    assert "baseline_track_uris" not in entry
    assert entry["current_track_uris"] == entry["desired_track_uris"] == [uri(1), uri(1)]
    assert (entry["status"], entry["dispatched"], entry["affected_count"]) == ("not_required", False, 0)
