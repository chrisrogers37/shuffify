"""
Tests for the Playlist Workshop routes.

Covers the workshop page render, preview-shuffle endpoint,
and commit endpoint.
"""

import json
from unittest.mock import MagicMock, Mock, create_autospec, patch

import pytest
from werkzeug.exceptions import ServiceUnavailable

from shuffify.models.playlist import Playlist
from shuffify.services import PlaylistAccessError
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import SpotifyTokenExpiredError
from tests.paths import api_path

# =============================================================================
# Fixtures
# =============================================================================


def _make_mock_playlist():
    """A Playlist model instance for workshop tests."""
    return Playlist(
        id="playlist123",
        name="Workshop Test Playlist",
        owner_id="user123",
        description="A test playlist",
        tracks=[
            {
                "id": f"track{i}",
                "name": f"Track {i}",
                "uri": f"spotify:track:track{i}",
                "duration_ms": 180000 + (i * 1000),
                "is_local": False,
                "artists": [f"Artist {i}"],
                "artist_urls": [f"https://open.spotify.com/artist/artist{i}"],
                "album_name": f"Album {i}",
                "album_image_url": f"https://example.com/album{i}.jpg",
                "track_url": f"https://open.spotify.com/track/track{i}",
            }
            for i in range(1, 6)
        ],
    )


# =============================================================================
# Workshop Page Route Tests
# =============================================================================


# =============================================================================
# Preview Shuffle Route Tests
# =============================================================================


class TestWorkshopPreviewShuffle:
    """Tests for POST /workshop/<playlist_id>/preview-shuffle."""

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.workshop.ShuffleService")
    def test_preview_shuffle_returns_shuffled_uris(
        self,
        mock_shuffle_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Preview shuffle should return reordered URIs without API call."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.spotify_id = "user123"
        mock_get_db_user.return_value = mock_user

        mock_playlist = _make_mock_playlist()
        shuffled = [f"spotify:track:track{i}" for i in [3, 1, 5, 2, 4]]
        mock_shuffle_svc.execute.return_value = shuffled
        algo_mock = Mock()
        algo_mock.name = "Basic"
        mock_shuffle_svc.get_algorithm.return_value = algo_mock

        # Send tracks from client (no API call)
        response = authenticated_client.post(
            api_path("/workshop/playlist123/preview-shuffle"),
            data=json.dumps(
                {
                    "algorithm": "BasicShuffle",
                    "tracks": mock_playlist.tracks,
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["shuffled_uris"] == shuffled

    def test_preview_shuffle_requires_auth(self, client):
        """Unauthenticated preview should return 401."""
        response = client.post(
            api_path("/workshop/playlist123/preview-shuffle"),
            data=json.dumps({"algorithm": "BasicShuffle"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_preview_shuffle_requires_json_body(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Preview without JSON body should return 400."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        response = authenticated_client.post(
            api_path("/workshop/playlist123/preview-shuffle"),
            data="not valid json",
            content_type="application/json",
        )
        assert response.status_code == 400


# =============================================================================
# Commit Route Tests
# =============================================================================


class TestWorkshopCommit:
    """The routes validate input and retain structured target outcomes."""

    @pytest.fixture
    def provider(self):
        from types import SimpleNamespace

        from tests.services.test_live_executor_outcomes import MutableSpotify, uri

        return MutableSpotify(SimpleNamespace(spotify_id="user123"), playlist123=[uri(1), uri(1), uri(2)])

    @pytest.fixture
    def authorized(self):
        with (
            patch("shuffify.is_db_available", return_value=True),
            patch(
                "shuffify.routes.get_db_user",
                return_value=Mock(id=1, spotify_id="user123"),
            ),
            patch("shuffify.routes.require_auth", return_value=Mock()) as auth,
            patch("shuffify.routes.workshop.WorkshopCommitService") as service,
        ):
            yield service, auth

    @pytest.mark.parametrize(
        "db_available,status,message",
        [
            (False, 503, "Database is unavailable."),
            (True, 401, "User not found."),
        ],
    )
    def test_preflight_gate_is_retryable_without_calling_commit(
        self, authenticated_client, db_available, status, message
    ):
        api = create_autospec(SpotifyAPI, instance=True, spec_set=True)
        with (
            patch("shuffify.routes.require_auth", autospec=True, return_value=api),
            patch("shuffify.is_db_available", autospec=True, return_value=db_available),
            patch("shuffify.routes.get_db_user", autospec=True, return_value=None),
            patch(
                "shuffify.routes.workshop.WorkshopCommitService",
                autospec=True,
                spec_set=True,
            ) as service,
        ):
            response = authenticated_client.post(
                api_path("/workshop/playlist123/commit"),
                json={"baseline_track_uris": [], "track_uris": []},
            )
        assert response.status_code == status
        body = response.get_json()
        assert body["message"] == message
        assert body["target_status"] == "not_written"
        assert body["requires_refresh"] is False
        assert body["retryable"] is True
        service.assert_not_called()
        assert api.mock_calls == []

    @pytest.mark.parametrize("status", ["saved", "already_applied"])
    def test_commit_serializes_confirmed_revision(
        self, auth_client, provider, status
    ):
        from tests.queued_operations import post_and_run, scoped_history
        from tests.services.test_live_executor_outcomes import uri

        before, uris = [uri(1), uri(1), uri(2)], [uri(2), uri(1), uri(1)]
        baseline = before
        if status == "already_applied":
            provider.playlists["playlist123"] = list(uris)
            baseline = [uri(3)]  # Already-applied takes precedence over stale baseline.
        with patch("shuffify.routes.require_auth", autospec=True, side_effect=AssertionError("Admission used Spotify")):
            response = post_and_run(
                auth_client, provider, api_path("/workshop/playlist123/commit"),
                json={"baseline_track_uris": baseline, "track_uris": uris},
            )
        assert response.accepted.status_code == 202
        assert response.status_code == 200
        assert response.operation["state"] == "succeeded"
        body = response.get_json()
        assert body["target_status"] == status
        assert body["saved_track_uris"] == body["current_track_uris"] == uris
        assert [track["uri"] for track in body["current_tracks"]] == uris
        assert body["confirmed_batches"] == body["total_batches"] == (1 if status == "saved" else 0)
        assert body["representable"] is True
        assert body["loaded_at"]
        assert scoped_history(auth_client, "playlist123")["states"] == ([before, uris] if status == "saved" else [uris])
        assert provider.writes == ([("update", "playlist123", uris)] if status == "saved" else [])

    def test_missing_baseline_is_explicit_and_never_calls_service(
        self, authenticated_client, authorized
    ):
        service, _ = authorized
        response = authenticated_client.post(
            api_path("/workshop/playlist123/commit"), json={"track_uris": []}
        )
        assert response.status_code == 400
        body = response.get_json()
        assert body["error_code"] == "baseline_required"
        assert body["target_status"] == "not_written"
        assert body["requires_refresh"] is True
        assert body["retryable"] is False
        service.assert_not_called()

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            "invalid",
            None,
            {"baseline_track_uris": [], "track_uris": ["invalid"]},
            {"baseline_track_uris": ["invalid"], "track_uris": []},
            {"baseline_track_uris": None, "track_uris": []},
        ],
    )
    def test_malformed_body_never_calls_service(
        self, authenticated_client, authorized, payload
    ):
        service, _ = authorized
        response = authenticated_client.post(
            api_path("/workshop/playlist123/commit"), json=payload
        )
        assert response.status_code == 400
        body = response.get_json()
        assert body["target_status"] == "not_written"
        assert body["requires_refresh"] is False
        assert body["retryable"] is True
        assert body["message"]
        service.assert_not_called()

    def test_invalid_json_is_retryable_without_calling_commit(
        self, authenticated_client, authorized
    ):
        service, _ = authorized
        response = authenticated_client.post(
            api_path("/workshop/playlist123/commit"),
            data="{invalid",
            content_type="application/json",
        )
        assert response.status_code == 400
        body = response.get_json()
        assert body["message"] == "Request body must be a JSON object."
        assert body["target_status"] == "not_written"
        assert body["requires_refresh"] is False
        assert body["retryable"] is True
        service.assert_not_called()

    def test_stale_draft_outcome_is_not_flattened(self, auth_client, provider):
        from tests.queued_operations import post_and_run
        from tests.services.test_live_executor_outcomes import uri

        response = post_and_run(
            auth_client, provider, api_path("/workshop/playlist123/commit"),
            json={"baseline_track_uris": [uri(3)], "track_uris": [uri(2), uri(1)]},
        )
        assert response.accepted.status_code == 202
        assert response.operation["state"] == "failed"
        body = response.get_json()
        assert body["error_code"] == "stale_draft"
        assert body["target_status"] == "not_written"
        assert body["requires_refresh"] is True
        assert body["current_track_uris"] == [uri(1), uri(1), uri(2)]
        assert "saved_track_uris" not in body
        assert provider.writes == []

    def test_active_target_is_rejected_with_its_existing_operation_id(self, auth_client, provider):
        from tests.services.test_live_executor_outcomes import uri

        body = {"baseline_track_uris": [uri(1), uri(1), uri(2)], "track_uris": []}
        first = auth_client.post(api_path("/workshop/playlist123/commit"), json=body)
        second = auth_client.post(api_path("/workshop/playlist123/commit"), json=body)
        assert first.status_code == 202
        assert second.status_code == 409
        assert second.get_json()["error_code"] == "playlist_busy"
        assert second.get_json()["operation_id"] == first.get_json()["operation"]["id"]
        assert provider.writes == []

    def test_partial_target_result_retains_counts_after_worker_rollback(self, auth_client, provider):
        from shuffify.spotify.exceptions import SpotifyWriteRejectedError
        from tests.queued_operations import post_and_run, scoped_history
        from tests.services.test_live_executor_outcomes import uri

        before, desired = [uri(1), uri(1), uri(2)], [uri(3)] * 101

        def reject(*args):
            raise SpotifyWriteRejectedError("Fictional second-batch rejection")

        provider.faults[("add", "playlist123")] = reject
        response = post_and_run(
            auth_client, provider, api_path("/workshop/playlist123/commit"),
            json={"baseline_track_uris": before, "track_uris": desired},
        )
        assert response.accepted.status_code == 202
        assert response.operation["state"] == "rolled_back"
        body = response.get_json()
        assert body["target_status"] == "partial"
        assert body["error_code"] == "partial_write"
        assert body["confirmed_batches"] == 1
        assert body["total_batches"] == 2
        assert "saved_track_uris" not in body
        assert provider.playlists["playlist123"] == before
        assert provider.writes == [("update", "playlist123", desired[:100]), ("add", "playlist123", desired[100:]),
                                   ("update", "playlist123", before)]
        assert scoped_history(auth_client, "playlist123") is None

    def test_unknown_target_outcome_requires_review_without_replaying_write(self, auth_client, provider):
        from shuffify.spotify.exceptions import SpotifyWriteUnverifiedError
        from tests.queued_operations import post_and_run, scoped_history
        from tests.services.test_live_executor_outcomes import uri

        def unknown(*args):
            raise SpotifyWriteUnverifiedError("Fictional missing acknowledgment")

        provider.faults[("update", "playlist123")] = unknown
        response = post_and_run(
            auth_client, provider, api_path("/workshop/playlist123/commit"),
            json={"baseline_track_uris": [uri(1), uri(1), uri(2)], "track_uris": []},
        )
        assert response.accepted.status_code == 202
        assert response.operation["state"] == "needs_attention"
        body = response.get_json()
        assert body["target_status"] == "unknown"
        assert body["error_code"] == "needs_attention"
        assert body["confirmed_batches"] == 0
        assert body["total_batches"] == 1
        assert "saved_track_uris" not in body
        assert len(provider.writes) == 1
        assert scoped_history(auth_client, "playlist123") is None

    def test_empty_draft_dispatches_a_real_put_in_worker(self, auth_client, provider):
        from tests.queued_operations import post_and_run
        from tests.services.test_live_executor_outcomes import uri

        response = post_and_run(
            auth_client, provider,
            api_path("/workshop/playlist123/commit"),
            json={"baseline_track_uris": [uri(1), uri(1), uri(2)], "track_uris": []},
        )
        assert response.accepted.status_code == 202
        assert response.get_json()["target_status"] == "saved"
        assert response.get_json()["saved_track_uris"] == []
        assert response.get_json()["confirmed_batches"] == 1
        assert provider.writes == [("update", "playlist123", [])]

    @pytest.mark.parametrize(
        "error,status",
        [
            (PlaylistAccessError("Cannot edit this playlist"), 403),
            (SpotifyTokenExpiredError("Expired"), 401),
            (ServiceUnavailable("Response failed after dispatch"), 503),
        ],
    )
    def test_existing_permission_and_auth_handling(
        self, auth_client, provider, error, status
    ):
        from tests.queued_operations import post_and_run
        from tests.services.test_live_executor_outcomes import uri

        if status == 403:
            provider.owner = "another-user"
        else:
            provider.read_failure = error
        response = post_and_run(
            auth_client, provider,
            api_path("/workshop/playlist123/commit"),
            json={"baseline_track_uris": [uri(1), uri(1), uri(2)], "track_uris": []},
        )
        assert response.accepted.status_code == 202
        assert response.operation["state"] == "failed"
        body = response.get_json()
        assert body["success"] is False
        assert "saved_track_uris" not in body
        if status == 503:
            assert body["error_code"] == "upstream_read_failed"
            assert body["target_status"] == "not_written"
        else:
            assert "target_status" not in body
        assert provider.writes == []

    def test_current_state_serializes_read_only_result(
        self, authenticated_client, authorized
    ):
        from shuffify.services.workshop_commit_service import WorkshopTargetResult

        service, _ = authorized
        service.return_value.read_current.return_value = WorkshopTargetResult(
            "not_written",
            "Loaded",
            extra={"current_track_uris": [], "representable": True},
        )
        with authenticated_client.session_transaction() as session:
            session["retained_draft"] = ["draft"]
        response = authenticated_client.get(api_path("/workshop/playlist123/current-state"))
        assert response.status_code == 200
        assert response.get_json()["current_track_uris"] == []
        service.return_value.commit.assert_not_called()
        with authenticated_client.session_transaction() as session:
            assert session["retained_draft"] == ["draft"]

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", api_path("/workshop/playlist123/commit")),
            ("get", api_path("/workshop/playlist123/current-state")),
        ],
    )
    def test_requires_auth(self, client, method, path):
        with patch(
            "shuffify.routes.workshop.WorkshopCommitService",
            autospec=True,
            spec_set=True,
        ) as service:
            response = getattr(client, method)(path)
        assert response.status_code == 401
        if method == "post":
            body = response.get_json()
            assert body["message"] == "Please log in first."
            assert body["target_status"] == "not_written"
            assert body["requires_refresh"] is False
            assert body["retryable"] is True
        service.assert_not_called()
