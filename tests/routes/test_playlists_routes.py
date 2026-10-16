"""
Tests for playlist routes.

Tests cover Library refresh, get-playlist, get-stats,
and user-playlists API.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.paths import api_path
from tests.queued_operations import post_and_run
from tests.services.test_live_metadata_service import MetadataSpotify


class TestLibraryRefresh:
    """GET /api/library?refresh=1 refreshes the Library's ordered sections."""

    @patch("shuffify.routes.require_auth", return_value=None)
    def test_unauth_returns_401(self, mock_auth, db_app):
        with db_app.test_client() as client:
            response = client.get(api_path("/library?refresh=1"))
        assert response.status_code == 401

    @patch("shuffify.services.PlaylistService.get_user_playlists", autospec=True)
    @patch("shuffify.routes.require_auth")
    def test_success_returns_playlist_sections(self, mock_auth, read_playlists, auth_client):
        from shuffify.spotify.api import SpotifyAPI

        mock_auth.return_value = MagicMock(spec=SpotifyAPI)
        read_playlists.return_value = [{"id": "p1", "name": "Playlist 1", "images": [], "public": True}]

        response = auth_client.get(api_path("/library?refresh=1"))

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert [row["id"] for row in data["sections"]["playlists"]] == ["p1"]
        assert read_playlists.call_args.kwargs == {"skip_cache": True}

    @patch("shuffify.services.PlaylistService.get_user_playlists", autospec=True)
    @patch("shuffify.routes.require_auth")
    def test_playlist_error_returns_500(self, mock_auth, read_playlists, auth_client):
        from shuffify.services import PlaylistError
        from shuffify.spotify.api import SpotifyAPI

        mock_auth.return_value = MagicMock(spec=SpotifyAPI)
        read_playlists.side_effect = PlaylistError("API unavailable")

        response = auth_client.get(api_path("/library?refresh=1"))

        assert response.status_code == 500
        assert response.get_json()["success"] is False


class TestApiUserPlaylists:
    """Tests for GET /api/user-playlists."""

    @patch("shuffify.routes.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(api_path("/api/user-playlists"))
            assert resp.status_code == 401

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.require_auth")
    def test_returns_formatted_playlists(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = [
            {
                "id": "p1",
                "name": "Playlist 1",
                "tracks": {"total": 10},
                "images": [
                    {"url": "https://example.com/img.jpg"}
                ],
            }
        ]
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get(api_path("/api/user-playlists"))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 1
        assert data["playlists"][0]["track_count"] == 10
        assert (
            data["playlists"][0]["image_url"] is not None
        )

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.require_auth")
    def test_track_count_in_spotifys_current_shape(
        self, mock_auth, mock_ps_class, auth_client
    ):
        """Spotify's current playlist shape names the track count "items"."""
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = [
            {"id": "p1", "name": "Current Shape", "items": {"total": 10}}
        ]
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get(api_path("/api/user-playlists"))
        assert resp.get_json()["playlists"][0]["track_count"] == 10

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.require_auth")
    def test_playlist_without_images(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = [
            {
                "id": "p1",
                "name": "No Image",
                "tracks": {"total": 5},
            }
        ]
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get(api_path("/api/user-playlists"))
        data = resp.get_json()
        assert data["playlists"][0]["image_url"] is None

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.require_auth")
    def test_playlist_error_returns_500(
        self, mock_auth, mock_ps_class, auth_client
    ):
        from shuffify.services import PlaylistError

        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.side_effect = (
            PlaylistError("fail")
        )
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get(api_path("/api/user-playlists"))
        assert resp.status_code == 500

    @pytest.mark.parametrize(("query", "skip_cache"), [
        ("", False),
        ("?refresh=1", True),
        ("?refresh=0", False),
        ("?refresh=true", False),
        ("?refresh=", False),
    ])
    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.require_auth")
    def test_only_refresh_1_bypasses_the_cache(
        self, mock_auth, mock_ps_class, auth_client, query, skip_cache
    ):
        """The Workshop asks for ?refresh=1 after a creation whose outcome is unknown."""
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = []
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get(f"/api/user-playlists{query}")

        assert resp.status_code == 200
        mock_ps.get_user_playlists.assert_called_once_with(skip_cache=skip_cache)


class TestVisibilityOutcomes:
    """The legacy visibility route keeps verified metadata and safe failures."""

    @pytest.mark.parametrize("public", [True, False])
    def test_verified_boolean_and_untouched_session(self, auth_client, public):
        api = MetadataSpotify(not public)
        history = {"p1": {"states": [["spotify:track:kept"]], "current_index": 0}}
        with auth_client.session_transaction() as sess:
            sess["playlist_states"] = history
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path("/playlist/p1/toggle-visibility"), json={"public": public})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["public"] is public
        assert data["operation"]["aggregate_status"] == "complete"
        assert data["operation"]["resources"][0]["desired_metadata"] == {"public": public}
        assert not {"execution_id", "started_at", "execution_state"} & data.keys()
        assert api.writes == [("p1", public)]
        with auth_client.session_transaction() as sess:
            assert sess["playlist_states"] == history

    @pytest.mark.parametrize(
        "body", [{}, {"public": None}, {"public": "false"}, {"public": 0}, {"public": 1}, [], True]
    )
    def test_strict_validation_precedes_any_spotify_call(self, auth_client, body):
        api = MetadataSpotify()
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path("/playlist/p1/toggle-visibility"), json=body)
        data = resp.get_json()
        assert resp.status_code == 400
        assert data["success"] is False
        assert data["operation"]["error_code"] == "invalid_public"
        assert api.reads == api.writes == []

    def test_noop_retains_success_with_zero_writes(self, auth_client):
        api = MetadataSpotify(True)
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path("/playlist/p1/toggle-visibility"), json={"public": True})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["operation"]["resources"][0]["status"] == "not_required"
        assert api.writes == []

    def test_permission_denial_has_direct_dto(self, auth_client):
        api = MetadataSpotify()
        api.owner = "not-the-user"
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path("/playlist/p1/toggle-visibility"), json={"public": True})
        data = resp.get_json()
        assert resp.status_code == 200
        assert resp.operation["state"] == "failed"
        assert data["success"] is False
        assert data["operation"]["aggregate_status"] == "partial"
        assert data["operation"]["resources"] == []
        assert api.writes == []

    def test_write_loss_keeps_intent_and_journals_compensation_without_offering_replay(self, auth_client):
        api = MetadataSpotify()

        def lose_reply(_api):
            raise RuntimeError("private Spotify detail")

        api.after_write = lose_reply
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path("/playlist/p1/toggle-visibility"), json={"public": True})
        data = resp.get_json()
        assert resp.status_code == 200
        assert resp.operation["state"] == "needs_attention"
        assert data["success"] is False
        assert data["operation"]["aggregate_status"] == "unknown"
        assert data["operation"]["retryable"] is False
        entry = data["operation"]["resources"][0]
        assert entry["desired_metadata"] == {"public": True}
        assert "current_metadata" not in entry
        # A worker can reconcile its applied intent and journal a restoration.
        # This fake loses that response too, so the receipt still requires review.
        assert api.public is False
        assert api.writes == [("p1", True), ("p1", False)]
        assert "private Spotify detail" not in str(data)
        assert "public" not in data
        assert not {"execution_id", "started_at", "execution_state"} & data.keys()

    def test_readback_mismatch_never_returns_requested_boolean(self, auth_client):
        api = MetadataSpotify()
        api.after_write = lambda fake: setattr(fake, "public", False)
        with patch("shuffify.routes.require_auth", return_value=api):
            resp = post_and_run(auth_client, api, api_path("/playlist/p1/toggle-visibility"), json={"public": True})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is False
        assert resp.operation["state"] == "needs_attention"
        assert resp.operation["recovery"]["reason"] == "external_edit"
        entry = data["operation"]["resources"][0]
        assert entry["status"] == "unknown"
        assert entry["baseline_metadata"] == {"public": False}
        assert entry["desired_metadata"] == {"public": True}
        # The changed snapshot cannot prove which writer produced its metadata.
        # Retain the original intent, then require a fresh explicit observation.
        assert "current_metadata" not in entry
        assert "public" not in data
        assert api.public is False
        assert api.writes == [("p1", True)]

    @pytest.mark.parametrize("status,missing_user", [(401, False), (503, False), (401, True)])
    def test_preflight_error_omits_execution_identity(self, auth_client, status, missing_user):
        api = MetadataSpotify()
        with patch(
            "shuffify.routes.require_auth", return_value=None if status == 401 and not missing_user else api
        ), patch("shuffify.is_db_available", return_value=status != 503), patch(
            "shuffify.routes.get_db_user", return_value=None
        ), patch("shuffify.routes.playlists.LiveMetadataService") as service:
            resp = post_and_run(auth_client, api, api_path("/playlist/p1/toggle-visibility"), json={"public": True})
        service.assert_not_called()
        data = resp.get_json()
        assert resp.status_code == status
        assert data["success"] is False
        assert data["operation"]["kind"] == "privacy"
        assert data["operation"]["aggregate_status"] == "partial"
        assert data["operation"]["error_code"] == (
            "authentication_required" if status == 401 else "database_unavailable"
        )
        assert data["operation"]["retryable"] is True
        assert data["operation"]["resources"] == data["operation"]["bookkeeping"] == []
        assert not {"execution_id", "started_at", "execution_state"} & data.keys()
        assert api.reads == api.writes == []
