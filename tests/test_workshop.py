"""
Tests for the Playlist Workshop routes.

Covers the workshop page render, preview-shuffle endpoint,
and commit endpoint.
"""

import json
from unittest.mock import patch, Mock

from shuffify.models.playlist import Playlist


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
                "artist_urls": [
                    f"https://open.spotify.com/artist/artist{i}"
                ],
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


class TestWorkshopPage:
    """Tests for GET /workshop/<playlist_id>."""

    def test_workshop_redirects_when_not_authenticated(self, client):
        """Unauthenticated users should be redirected to index."""
        response = client.get("/workshop/playlist123")
        assert response.status_code in (302, 200)

    @patch("shuffify.routes.workshop.AuthService")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch("shuffify.routes.workshop.ShuffleService")
    def test_workshop_renders_with_valid_playlist(
        self,
        mock_shuffle_svc,
        mock_playlist_svc,
        mock_auth_svc,
        authenticated_client,
        sample_user,
    ):
        """Workshop page should render successfully with playlist data."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()
        mock_auth_svc.get_user_data.return_value = sample_user

        mock_ps_instance = Mock()
        mock_ps_instance.get_playlist.return_value = _make_mock_playlist()
        mock_playlist_svc.return_value = mock_ps_instance

        mock_shuffle_svc.list_algorithms.return_value = [
            {
                "name": "Basic",
                "class_name": "BasicShuffle",
                "description": "Random shuffle",
                "parameters": {},
            }
        ]

        response = authenticated_client.get("/workshop/playlist123")
        assert response.status_code == 200
        assert b"Workshop Test Playlist" in response.data
        assert b"Track 1" in response.data
        assert b"Save to Spotify" in response.data


# =============================================================================
# Preview Shuffle Route Tests
# =============================================================================


class TestWorkshopPreviewShuffle:
    """Tests for POST /workshop/<playlist_id>/preview-shuffle."""

    @patch("shuffify.routes.workshop.AuthService")
    @patch("shuffify.routes.workshop.ShuffleService")
    def test_preview_shuffle_returns_shuffled_uris(
        self,
        mock_shuffle_svc,
        mock_auth_svc,
        authenticated_client,
    ):
        """Preview shuffle should return reordered URIs without API call."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_playlist = _make_mock_playlist()
        shuffled = [
            f"spotify:track:track{i}" for i in [3, 1, 5, 2, 4]
        ]
        mock_shuffle_svc.execute.return_value = shuffled
        algo_mock = Mock()
        algo_mock.name = "Basic"
        mock_shuffle_svc.get_algorithm.return_value = algo_mock

        # Send tracks from client (no API call)
        response = authenticated_client.post(
            "/workshop/playlist123/preview-shuffle",
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
            "/workshop/playlist123/preview-shuffle",
            data=json.dumps({"algorithm": "BasicShuffle"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @patch("shuffify.routes.workshop.AuthService")
    def test_preview_shuffle_requires_json_body(
        self, mock_auth_svc, authenticated_client
    ):
        """Preview without JSON body should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/playlist123/preview-shuffle",
            data="not valid json",
            content_type="application/json",
        )
        assert response.status_code == 400


# =============================================================================
# Commit Route Tests
# =============================================================================


class TestWorkshopCommit:
    """Tests for POST /workshop/<playlist_id>/commit."""

    @patch("shuffify.routes.workshop.AuthService")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch("shuffify.routes.workshop.ShuffleService")
    @patch("shuffify.routes.workshop.StateService")
    def test_commit_saves_to_spotify(
        self,
        mock_state_svc,
        mock_shuffle_svc,
        mock_playlist_svc,
        mock_auth_svc,
        authenticated_client,
    ):
        """Commit should call update_playlist_tracks on Spotify."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_playlist = _make_mock_playlist()
        mock_ps_instance = Mock()
        mock_ps_instance.get_playlist.return_value = mock_playlist
        mock_ps_instance.update_playlist_tracks.return_value = True
        mock_playlist_svc.return_value = mock_ps_instance

        mock_shuffle_svc.shuffle_changed_order.return_value = True

        mock_state_svc.ensure_playlist_initialized.return_value = Mock()
        mock_state_svc.record_new_state.return_value = Mock(
            to_dict=lambda: {"states": [], "current_index": 1}
        )

        new_uris = [
            f"spotify:track:track{i}" for i in [5, 4, 3, 2, 1]
        ]
        response = authenticated_client.post(
            "/workshop/playlist123/commit",
            data=json.dumps({"track_uris": new_uris}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_ps_instance.update_playlist_tracks.assert_called_once_with(
            "playlist123", new_uris
        )

    @patch("shuffify.routes.workshop.AuthService")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch("shuffify.routes.workshop.ShuffleService")
    @patch("shuffify.routes.workshop.StateService")
    def test_commit_unchanged_order_returns_no_op(
        self,
        mock_state_svc,
        mock_shuffle_svc,
        mock_playlist_svc,
        mock_auth_svc,
        authenticated_client,
    ):
        """Committing an unchanged order should not call Spotify API."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_playlist = _make_mock_playlist()
        mock_ps_instance = Mock()
        mock_ps_instance.get_playlist.return_value = mock_playlist
        mock_playlist_svc.return_value = mock_ps_instance

        mock_shuffle_svc.shuffle_changed_order.return_value = False
        mock_state_svc.ensure_playlist_initialized.return_value = Mock()

        same_uris = [
            f"spotify:track:track{i}" for i in range(1, 6)
        ]
        response = authenticated_client.post(
            "/workshop/playlist123/commit",
            data=json.dumps({"track_uris": same_uris}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_ps_instance.update_playlist_tracks.assert_not_called()

    def test_commit_requires_auth(self, client):
        """Unauthenticated commit should return 401."""
        response = client.post(
            "/workshop/playlist123/commit",
            data=json.dumps({"track_uris": ["spotify:track:x"]}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @patch("shuffify.routes.workshop.AuthService")
    def test_commit_validates_uri_format(
        self, mock_auth_svc, authenticated_client
    ):
        """Commit with invalid URI format should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/playlist123/commit",
            data=json.dumps({"track_uris": ["not-a-valid-uri"]}),
            content_type="application/json",
        )
        assert response.status_code == 400
