"""
Tests for playlist pair routes.

Tests cover authentication, error handling, and basic CRUD
for the /playlist/<id>/pair endpoints.
"""

from unittest.mock import MagicMock, patch

import pytest

from shuffify.models.db import JobExecution
from shuffify.services.workshop_finalize_service import WorkshopFinalizeResult
from tests.paths import api_path
from tests.playlist_setup_fixtures import SetupSpotify
from tests.queued_operations import post_and_run

# =============================================================================
# Authentication Tests
# =============================================================================


class TestPairAuthRequired:
    """All pair endpoints require authentication."""

    @patch("shuffify.routes.require_auth")
    def test_get_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(api_path("/playlist/p1/pair"))
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_create_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/pair"),
                json={"create_new": True},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_delete_pair_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete(api_path("/playlist/p1/pair"))
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_archive_tracks_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/pair/archive"),
                json={"track_uris": ["spotify:track:x"]},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_unarchive_tracks_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/pair/unarchive"),
                json={"track_uris": ["spotify:track:x"]},
            )
            assert resp.status_code == 401


# =============================================================================
# GET /playlist/<id>/pair
# =============================================================================


class TestGetPair:
    """Tests for GET /playlist/<id>/pair."""

    @patch("shuffify.routes.require_auth")
    def test_no_pair_returns_paired_false(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.get(api_path("/playlist/p1/pair"))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["paired"] is False


# =============================================================================
# POST /playlist/<id>/pair
# =============================================================================


class TestCreatePairRoute:
    """Tests for POST /playlist/<id>/pair."""

    @patch("shuffify.routes.require_auth")
    def test_invalid_body_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/pair"),
            json={"create_new": False},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_no_json_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.post(
            api_path("/playlist/p1/pair"),
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_unverified_creation_is_reported_unknown_and_pairs_nothing(
        self, mock_auth, auth_client
    ):
        """A lost answer from Spotify leaves the archive playlist's existence unknown."""
        from shuffify.models.db import PlaylistPair
        from shuffify.services.playlist_setup_service import CREATION_UNKNOWN
        from shuffify.spotify.exceptions import SpotifyWriteUnverifiedError

        api = SetupSpotify(playlists={"p1": {"name": "Mix"}})
        api.create_failure = SpotifyWriteUnverifiedError("timed out")
        mock_auth.return_value = api

        resp = post_and_run(auth_client, api,
            api_path("/playlist/p1/pair"),
            json={"create_new": True, "production_playlist_name": "Mix"},
        )

        assert resp.status_code == 200 and resp.accepted.status_code == 202
        assert resp.operation["state"] == "needs_attention"
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "creation_unknown"
        assert data["outcome"] == "unknown"
        assert data["message"] == CREATION_UNKNOWN
        assert len([call for call in api.calls if call[0] == "create_user_playlist"]) == 1
        assert PlaylistPair.query.count() == 0

    @patch("shuffify.routes.require_auth")
    def test_creation_answered_without_an_id_is_reported_unknown_and_pairs_nothing(
        self, mock_auth, auth_client
    ):
        """An empty 2xx from Spotify may still have created the archive playlist."""
        from shuffify.models.db import PlaylistPair
        from shuffify.services.playlist_setup_service import CREATION_UNKNOWN
        api = SetupSpotify(playlists={"p1": {"name": "Mix"}})

        def no_id(name, public=False, description=""):
            api.calls.append(("create_user_playlist", name, public, description))

        api.create_user_playlist = no_id
        mock_auth.return_value = api

        resp = post_and_run(auth_client, api,
            api_path("/playlist/p1/pair"),
            json={"create_new": True, "production_playlist_name": "Mix"},
        )

        assert resp.status_code == 200 and resp.accepted.status_code == 202
        assert resp.operation["state"] == "needs_attention"
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "creation_unknown"
        assert data["outcome"] == "unknown"
        assert data["message"] == CREATION_UNKNOWN
        assert len([call for call in api.calls if call[0] == "create_user_playlist"]) == 1
        assert not [call for call in api.calls if call[0] == "update_playlist_details"]
        assert PlaylistPair.query.count() == 0


# =============================================================================
# PATCH /playlist/<id>/pair
# =============================================================================


class TestUpdatePairAuth:
    """Authentication tests for PATCH pair endpoint."""

    @patch("shuffify.routes.require_auth")
    def test_update_pair_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.patch(
                api_path("/playlist/p1/pair"),
                json={"auto_archive_on_remove": True},
            )
            assert resp.status_code == 401


class TestUpdatePairValidation:
    """Validation tests for PATCH pair endpoint."""

    @patch("shuffify.routes.require_auth")
    def test_no_fields_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.patch(
            api_path("/playlist/p1/pair"),
            json={},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_no_json_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()
        resp = auth_client.patch(
            api_path("/playlist/p1/pair"),
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    @patch(
        "shuffify.routes.playlist_pairs"
        ".PlaylistPairService"
    )
    def test_pair_not_found_returns_404(
        self, mock_svc, mock_auth, auth_client
    ):
        from shuffify.services.playlist_pair_service import (
            PlaylistPairNotFoundError,
        )

        mock_auth.return_value = MagicMock()
        mock_svc.update_pair.side_effect = (
            PlaylistPairNotFoundError("No pair found")
        )
        resp = auth_client.patch(
            api_path("/playlist/p1/pair"),
            json={"auto_archive_on_remove": True},
        )
        assert resp.status_code == 404


# =============================================================================
# POST /playlist/<id>/pair/finalize-restore
# =============================================================================


class TestFinalizeRestoreAuth:
    """Authentication tests for finalize-restore endpoint."""

    @patch("shuffify.routes.require_auth")
    def test_finalize_restore_unauth(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                api_path("/playlist/p1/pair/finalize-restore"),
                json={
                    "track_uris": [
                        "spotify:track:"
                        "a1b2c3d4e5f6g7h8i9j0k1"
                    ]
                },
            )
            assert resp.status_code == 401


class TestArchiveFinalizers:
    @pytest.mark.parametrize("endpoint,method,count_key", [
        ("finalize-restore", "finalize_restore", "restored_count"),
        ("archive", "append_archive", "archived_count"),
    ])
    @pytest.mark.parametrize("status,http_status,retryable", [
        ("complete", 200, False), ("failed", 409, False),
        ("failed", 502, True), ("unknown", 502, False),
    ])
    @patch("shuffify.routes.playlist_pairs.WorkshopFinalizeService")
    @patch("shuffify.routes.require_auth")
    def test_captured_request_and_exact_result_forwarded(
        self, auth, service, auth_client, endpoint, method, count_key,
        status, http_status, retryable,
    ):
        payload = {
            "source_playlist_id": "archive123",
            "target_track_uris": ["spotify:track:" + "a" * 22],
            "track_uris": ["spotify:track:" + "a" * 22] * 2,
            "inspect_only": True,
        }
        if endpoint == "archive":
            payload["baseline_track_uris"] = ["spotify:track:" + "b" * 22] * 2
        result = WorkshopFinalizeResult(
            "archive123", "Archive", status, "Result", http_status=http_status,
            retryable=retryable, extra={count_key: 2 if status == "complete" else 0},
        )
        finalize = getattr(service.return_value, method)
        finalize.return_value = result
        with patch("shuffify.routes.playlist_pairs.log_activity") as activity:
            response = auth_client.post(api_path(f"/playlist/p1/pair/{endpoint}"), json=payload)
        assert response.status_code == http_status
        expected = result.to_dict()
        if not expected["success"]:
            expected["category"] = "error"
        assert response.json == expected
        service.assert_called_once_with(auth.return_value)
        assert finalize.call_args.args[0] == "p1"
        assert finalize.call_args.kwargs == payload
        activity.assert_not_called()

    @pytest.mark.parametrize("endpoint", ["archive", "finalize-restore"])
    @pytest.mark.parametrize("payload", [None, {}, [], {"track_uris": []}, {
        "track_uris": ["spotify:track:" + "a" * 22],
    }])
    @patch("shuffify.routes.playlist_pairs.WorkshopFinalizeService")
    @patch("shuffify.routes.require_auth")
    def test_incomplete_captured_task_rejected(self, auth, service, auth_client, endpoint, payload):
        response = auth_client.post(api_path(f"/playlist/p1/pair/{endpoint}"), json=payload)
        assert response.status_code == 400
        assert response.json["status"] == "failed"
        assert response.json["retryable"] is False
        service.assert_not_called()


class TestArchiveFinalizerListBounds:
    @pytest.mark.parametrize("endpoint,field", [
        ("archive", "track_uris"),
        ("archive", "target_track_uris"),
        ("archive", "baseline_track_uris"),
        ("finalize-restore", "track_uris"),
        ("finalize-restore", "target_track_uris"),
    ])
    @pytest.mark.parametrize("inspect_only", [False, True])
    @patch("shuffify.routes.finalizer_queue.enqueue_command")
    @patch("shuffify.routes.playlist_pairs.WorkshopFinalizeService")
    @patch("shuffify.routes.require_auth")
    def test_over_spotify_playlist_limit_rejected(
        self, auth, service, enqueue, auth_client, inspect_only, endpoint, field,
    ):
        payload = {
            "inspect_only": inspect_only,
            "source_playlist_id": "archive123",
            "target_track_uris": ["spotify:track:" + "a" * 22],
            "track_uris": ["spotify:track:" + "a" * 22] * 2,
        }
        if endpoint == "archive":
            payload["baseline_track_uris"] = ["spotify:track:" + "b" * 22] * 2
        payload[field] = [f"spotify:track:{i:022d}" for i in range(10_001)]
        response = auth_client.post(api_path(f"/playlist/p1/pair/{endpoint}"), json=payload)
        assert response.status_code == 400
        assert response.json["success"] is False
        assert response.json["error_code"] == "preflight_failed"
        assert response.json["status"] == ("unknown" if inspect_only else "failed")
        assert response.json["retryable"] is False
        count_key = "archived_count" if endpoint == "archive" else "restored_count"
        assert response.json[count_key] == (None if inspect_only else 0)
        service.assert_not_called()
        enqueue.assert_not_called()
        auth.assert_not_called()
        assert JobExecution.query.count() == 0

    @pytest.mark.parametrize("endpoint,kind,method", [
        ("archive", "workshop_archive_append", "append_archive"),
        ("finalize-restore", "workshop_finalize_restore", "finalize_restore"),
    ])
    @pytest.mark.parametrize("inspect_only", [False, True])
    @patch("shuffify.routes.finalizer_queue.enqueue_command", return_value=({}, 202))
    @patch("shuffify.routes.playlist_pairs.WorkshopFinalizeService")
    @patch("shuffify.routes.require_auth")
    def test_playlist_limits_forwarded(
        self, auth, service, enqueue, auth_client, endpoint, kind, method, inspect_only,
    ):
        uris = [f"spotify:track:{i:022d}" for i in range(10_000)]
        values = {
            "source_playlist_id": "archive123",
            "target_track_uris": uris,
            "track_uris": uris[::-1],
        }
        if endpoint == "archive":
            values["baseline_track_uris"] = uris[1:] + uris[:1]
        finalize = getattr(service.return_value, method)
        finalize.return_value = WorkshopFinalizeResult("archive123", "Archive", "complete", "Result")
        payload = {**values, **({"inspect_only": True} if inspect_only else {})}
        response = auth_client.post(api_path(f"/playlist/p1/pair/{endpoint}"), json=payload)
        if inspect_only:
            assert response.status_code == 200
            service.assert_called_once_with(auth.return_value)
            finalize.assert_called_once()
            assert finalize.call_args.args[0] == "p1"
            assert finalize.call_args.kwargs == payload
            enqueue.assert_not_called()
        else:
            assert response.status_code == 202
            enqueue.assert_called_once()
            _user, actual_kind, intent, _build = enqueue.call_args.args
            assert actual_kind == kind
            assert intent == {"playlist_id": "p1", **values}
            auth.assert_not_called()


@pytest.mark.parametrize("path,selection,needs_baseline", [
    ("pair/archive", "track_uris", True),
    ("pair/finalize-restore", "track_uris", False),
    ("pending-raids/finalize", "track_ids", False),
])
def test_finalizers_distinguish_empty_orders_from_missing_orders_and_empty_selections(
    auth_client, path, selection, needs_baseline,
):
    payload = {"source_playlist_id": "source1", "target_track_uris": [],
               selection: [1] if selection == "track_ids" else ["spotify:track:" + "a" * 22]}
    if needs_baseline:
        payload["baseline_track_uris"] = []
    path = api_path(f"/playlist/p1/{path}")
    with patch("shuffify.routes.finalizer_queue.enqueue_command", autospec=True, return_value=({}, 202)) as enqueue:
        assert auth_client.post(path, json=payload).status_code == 202
        assert enqueue.call_args.args[2] == {"playlist_id": "p1", **payload}
        enqueue.reset_mock()
        for field in payload.keys() - {"source_playlist_id"}:
            missing = {key: value for key, value in payload.items() if key != field}
            assert auth_client.post(path, json=missing).status_code == 400
        assert auth_client.post(path, json={**payload, selection: []}).status_code == 400
        enqueue.assert_not_called()
