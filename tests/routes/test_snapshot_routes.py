"""
Tests for snapshot routes.

Tests cover list, create, view, restore, and delete endpoints.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shuffify.enums import SnapshotType
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)
from shuffify.services.user_service import UserService
from shuffify.spotify.api import SpotifyAPI
from tests.paths import api_path
from tests.queued_operations import post_and_run, scoped_history
from tests.services.test_live_executor_outcomes import MutableSpotify, uri


class TestListSnapshots:
    """Tests for GET /playlist/<id>/snapshots."""

    @patch("shuffify.routes.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(
                api_path("/playlist/p1/snapshots")
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    @patch("shuffify.is_db_available")
    def test_db_unavailable_returns_503(
        self, mock_db, mock_auth, db_app
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = False
        with db_app.test_client() as client:
            with client.session_transaction() as sess:
                sess["spotify_token"] = {
                    "access_token": "t",
                    "expires_at": time.time() + 3600,
                }
                sess["user_data"] = {"id": "user123"}
            resp = client.get(
                api_path("/playlist/p1/snapshots")
            )
            assert resp.status_code == 503

    @patch("shuffify.routes.require_auth")
    def test_list_empty(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.get(
            api_path("/playlist/p1/snapshots")
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["snapshots"] == []

    @patch("shuffify.routes.require_auth")
    def test_list_with_snapshots(
        self, mock_auth, auth_client, db_app
    ):
        mock_auth.return_value = MagicMock()

        # Create a snapshot directly
        with db_app.app_context():
            user = UserService.get_by_spotify_id(
                "user123"
            )
            PlaylistSnapshotService.create_snapshot(
                user.id,
                "p1",
                "Test",
                ["spotify:track:a"],
                SnapshotType.MANUAL,
            )

        resp = auth_client.get(
            api_path("/playlist/p1/snapshots")
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["snapshots"]) == 1

    @patch("shuffify.routes.require_auth")
    def test_list_respects_limit_param(
        self, mock_auth, auth_client, db_app
    ):
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            user = UserService.get_by_spotify_id(
                "user123"
            )
            for i in range(5):
                PlaylistSnapshotService.create_snapshot(
                    user.id,
                    "p1",
                    f"S{i}",
                    [f"spotify:track:{i}"],
                    SnapshotType.MANUAL,
                )

        resp = auth_client.get(
            api_path("/playlist/p1/snapshots?limit=2")
        )
        data = resp.get_json()
        assert len(data["snapshots"]) == 2


class TestCreateManualSnapshot:
    """Tests for POST /playlist/<id>/snapshots."""

    @patch("shuffify.routes.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/snapshots"),
                json={},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_missing_json_body(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/snapshots"),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_invalid_request_body(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/snapshots"),
            json={"track_uris": ["invalid_uri"]},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_create_success(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/snapshots"),
            json={
                "playlist_name": "My Playlist",
                "track_uris": [
                    "spotify:track:a",
                    "spotify:track:b",
                ],
                "trigger_description": "Manual save",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["snapshot"]["track_count"] == 2
        assert (
            data["snapshot"]["snapshot_type"]
            == SnapshotType.MANUAL
        )

    @patch("shuffify.routes.require_auth")
    def test_create_empty_track_list(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/snapshots"),
            json={
                "playlist_name": "Empty",
                "track_uris": [],
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["snapshot"]["track_count"] == 0


class TestViewSnapshot:
    """Tests for GET /snapshots/<id>."""

    @patch("shuffify.routes.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(api_path("/snapshots/1"))
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_not_found(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.get(api_path("/snapshots/99999"))
        assert resp.status_code == 404

    @patch("shuffify.routes.require_auth")
    def test_view_success(
        self, mock_auth, auth_client, db_app
    ):
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            user = UserService.get_by_spotify_id(
                "user123"
            )
            snap = (
                PlaylistSnapshotService.create_snapshot(
                    user.id,
                    "p1",
                    "Test",
                    ["spotify:track:a"],
                    SnapshotType.MANUAL,
                )
            )
            snap_id = snap.id

        resp = auth_client.get(api_path(f"/snapshots/{snap_id}"))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["snapshot"]["id"] == snap_id


class TestDeleteSnapshot:
    """Tests for DELETE /snapshots/<id>."""

    @patch("shuffify.routes.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete(api_path("/snapshots/1"))
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_not_found(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.delete(api_path("/snapshots/99999"))
        assert resp.status_code == 404

    @patch("shuffify.routes.require_auth")
    def test_delete_success(
        self, mock_auth, auth_client, db_app
    ):
        mock_auth.return_value = MagicMock()

        with db_app.app_context():
            user = UserService.get_by_spotify_id(
                "user123"
            )
            snap = (
                PlaylistSnapshotService.create_snapshot(
                    user.id,
                    "p1",
                    "Test",
                    ["spotify:track:a"],
                    SnapshotType.MANUAL,
                )
            )
            snap_id = snap.id

        resp = auth_client.delete(
            api_path(f"/snapshots/{snap_id}")
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify deleted
        resp2 = auth_client.get(
            api_path(f"/snapshots/{snap_id}")
        )
        assert resp2.status_code == 404


class TestRestoreSnapshot:
    """Tests for POST /snapshots/<id>/restore."""

    @patch("shuffify.routes.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/snapshots/1/restore")
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_not_found(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/snapshots/99999/restore")
        )
        assert resp.status_code == 404

    @staticmethod
    def create_snapshot(order, *, user=None):
        user = user or UserService.get_by_spotify_id("user123")
        return PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "Saved name", order, SnapshotType.MANUAL,
        ).id

    @staticmethod
    def spotify(order):
        return MutableSpotify(SimpleNamespace(spotify_id="user123"), p1=order)

    def test_restore_success_from_empty_post_and_actual_before_history(self, auth_client):
        before, desired = [uri(3), uri(4)], [uri(1), uri(2), uri(1)]
        snap_id = self.create_snapshot(desired)
        api = self.spotify(before)
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path(f"/snapshots/{snap_id}/restore"))
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert "restored" in data["message"].lower()
        assert data["playlist_id"] == "p1"
        assert data["snapshot"]["id"] == snap_id
        assert data["snapshot"]["track_uris"] == desired
        assert api.playlists["p1"] == desired
        assert api.writes == [("update", "p1", desired)]
        assert data["operation"]["aggregate_status"] == "complete"
        assert not {"execution_id", "started_at", "execution_state"} & data.keys()
        assert scoped_history(auth_client, "p1") == {"states": [before, desired], "current_index": 1}
        with auth_client.session_transaction() as sess:
            assert "playlist_states" not in sess
        snapshots = PlaylistSnapshotService.get_snapshots(
            UserService.get_by_spotify_id("user123").id, "p1"
        )
        auto = [snap for snap in snapshots if snap.snapshot_type == SnapshotType.AUTO_PRE_COMMIT]
        assert len(auto) == 1
        assert auto[0].track_uris == before

    def test_owned_snapshot_target_ignores_untrusted_body(self, auth_client):
        desired = [uri(1)]
        snap_id = self.create_snapshot(desired)
        api = self.spotify([uri(2)])
        api.playlists["other"] = [uri(9)]
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api,
                api_path(f"/snapshots/{snap_id}/restore"),
                json={"playlist_id": "other", "track_uris": [uri(8)]},
            )
        assert resp.status_code == 200
        assert api.writes == [("update", "p1", desired)]
        assert api.playlists["other"] == [uri(9)]

    def test_noop_preserves_missing_history_and_adds_no_snapshot(self, auth_client):
        desired = [uri(1), uri(1)]
        snap_id = self.create_snapshot(desired)
        api = self.spotify(desired)
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path(f"/snapshots/{snap_id}/restore"))
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["operation"]["resources"][0]["status"] == "not_required"
        assert api.writes == []
        with auth_client.session_transaction() as sess:
            assert "playlist_states" not in sess
        assert len(PlaylistSnapshotService.get_snapshots(
            UserService.get_by_spotify_id("user123").id, "p1"
        )) == 1

    def test_unowned_snapshot_is_not_dispatched(self, auth_client):
        other = UserService.upsert_from_spotify({"id": "other", "display_name": "Other", "images": []}).user
        snap_id = self.create_snapshot([uri(1)], user=other)
        api = self.spotify([uri(2)])
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path(f"/snapshots/{snap_id}/restore"))
        data = resp.get_json()
        assert resp.status_code == 404
        assert data["operation"]["error_code"] == "snapshot_not_found"
        assert data["operation"]["resources"] == []
        assert api.writes == []

    def test_empty_snapshot_is_400_without_spotify_reads(self, auth_client):
        snap_id = self.create_snapshot([])
        api = MagicMock(spec=SpotifyAPI)
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path(f"/snapshots/{snap_id}/restore"))
        assert resp.status_code == 400
        assert resp.get_json()["operation"]["error_code"] == "empty_snapshot"
        api.get_playlist.assert_not_called()
        api.update_playlist_tracks.assert_not_called()

    def test_lost_reply_retains_intent_and_journals_compensation_without_advancing_history(self, auth_client):
        before, desired = [uri(3)], [uri(1), uri(2), uri(1)]
        snap_id = self.create_snapshot(desired)
        api = self.spotify(before)

        def apply_then_lose(fake, playlist_id, order):
            fake.playlists[playlist_id] = list(order)
            fake.revisions[playlist_id] += 1
            raise TimeoutError("private Spotify detail")

        api.faults[("update", "p1")] = apply_then_lose
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path(f"/snapshots/{snap_id}/restore"))
        data = resp.get_json()
        assert resp.status_code == 200
        assert resp.operation["state"] == "needs_attention"
        assert data["success"] is False
        assert data["snapshot"]["id"] == snap_id
        assert data["operation"]["aggregate_status"] == "unknown"
        assert data["operation"]["retryable"] is False
        entry = data["operation"]["resources"][0]
        assert entry["desired_track_uris"] == desired
        assert entry["baseline_track_uris"] == before
        assert "current_track_uris" not in entry
        # Both responses are lost: the journal authorizes compensation only
        # after recognizing its own desired order, then requires review.
        assert api.playlists["p1"] == before
        assert api.writes == [("update", "p1", desired), ("update", "p1", before)]
        assert "private Spotify detail" not in str(data)
        with auth_client.session_transaction() as sess:
            assert "playlist_states" not in sess

    def test_history_failure_preserves_verified_spotify_facts(self, auth_client):
        desired = [uri(1)]
        snap_id = self.create_snapshot(desired)
        api = self.spotify([uri(2)])
        with patch("shuffify.routes.require_auth", return_value=api), patch(
            "shuffify.services.state_service.StateService.record_verified_change",
            side_effect=RuntimeError("private session detail"),
        ):
            resp = post_and_run(auth_client, api, api_path(f"/snapshots/{snap_id}/restore"))
        data = resp.get_json()
        assert resp.status_code == 200
        assert resp.operation["state"] == "needs_attention"
        assert data["success"] is False
        assert data["operation"]["aggregate_status"] == "partial"
        assert data["operation"]["resources"][0]["status"] == "complete"
        assert data["operation"]["bookkeeping"][0]["status"] == "failed"
        assert data["operation"]["retryable"] is False
        assert "playlist_state" not in data
        assert api.writes == [("update", "p1", desired)]
        assert "private session detail" not in str(data)

    @pytest.mark.parametrize("status,missing_user", [(401, False), (503, False), (401, True)])
    def test_preflight_failure_has_direct_dto(self, auth_client, status, missing_user):
        api = MagicMock(spec=SpotifyAPI)
        with patch(
            "shuffify.routes.require_auth", return_value=None if status == 401 and not missing_user else api
        ), patch("shuffify.is_db_available", return_value=status != 503), patch(
            "shuffify.routes.get_db_user", return_value=None
        ), patch("shuffify.routes.snapshots.PlaylistSnapshotService.get_snapshot") as lookup, patch(
            "shuffify.services.live_playlist_service.LivePlaylistService"
        ) as service:
            resp = post_and_run(auth_client, api, api_path("/snapshots/123/restore"))
        lookup.assert_not_called()
        service.assert_not_called()
        data = resp.get_json()
        assert resp.status_code == status
        assert data["success"] is False
        assert data["operation"]["kind"] == "snapshot_restore"
        assert data["operation"]["aggregate_status"] == "partial"
        assert data["operation"]["error_code"] == (
            "authentication_required" if status == 401 else "database_unavailable"
        )
        assert data["operation"]["retryable"] is True
        assert data["operation"]["resources"] == data["operation"]["bookkeeping"] == []
        assert not {"execution_id", "started_at", "execution_state"} & data.keys()
        api.get_playlist.assert_not_called()
        api.update_playlist_tracks.assert_not_called()
