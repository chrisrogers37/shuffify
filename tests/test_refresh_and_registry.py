"""
Tests for Library refresh and the algorithm registry.

Covers GET /api/library refresh and the registry's algorithm list.
"""

from unittest.mock import Mock, patch

import pytest

from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from tests.paths import api_path


class TestRegistryHiddenAlgorithms:
    """Tests for algorithm visibility in the registry."""

    def test_tempo_gradient_is_removed(self):
        """TempoGradientShuffle is not registered; asking for it is rejected."""
        with pytest.raises(ValueError, match="Unknown shuffle algorithm"):
            ShuffleRegistry.get_algorithm("TempoGradientShuffle")

    def test_artist_spacing_is_visible(self):
        """ArtistSpacingShuffle should appear in list_algorithms."""
        algorithms = ShuffleRegistry.list_algorithms()
        class_names = [a["class_name"] for a in algorithms]
        assert "ArtistSpacingShuffle" in class_names

    def test_album_sequence_is_visible(self):
        """AlbumSequenceShuffle should appear in list_algorithms."""
        algorithms = ShuffleRegistry.list_algorithms()
        class_names = [a["class_name"] for a in algorithms]
        assert "AlbumSequenceShuffle" in class_names

    def test_algorithm_count(self):
        """Should have 7 registered algorithms."""
        all_algos = ShuffleRegistry.get_available_algorithms()
        assert len(all_algos) == 7

    def test_algorithm_display_order(self):
        """Algorithms should appear in the defined order."""
        algorithms = ShuffleRegistry.list_algorithms()
        names = [a["name"] for a in algorithms]
        expected = [
            "Basic",
            "Percentage",
            "Balanced",
            "Stratified",
            "Artist Spacing",
            "Album Sequence",
            "Newest First",
        ]
        assert names == expected


class TestLibraryRefreshRoute:
    """Library refresh bypasses Spotify's cache while retaining undo history."""

    @pytest.fixture
    def spotify(self):
        from shuffify.spotify.api import SpotifyAPI

        with patch("shuffify.routes.require_auth", return_value=Mock(spec=SpotifyAPI)) as auth:
            yield auth.return_value

    @pytest.fixture
    def playlists(self):
        with patch("shuffify.services.PlaylistService.get_user_playlists", autospec=True) as read:
            read.return_value = [
                {"id": "p1", "name": "Playlist 1", "images": [], "public": True, "items": {"total": 2}},
                {"id": "p2", "name": "Playlist 2", "images": [], "public": False, "items": {"total": 3}},
            ]
            yield read

    def test_refresh_requires_auth(self, db_app):
        with db_app.test_client() as client:
            response = client.get(api_path("/library?refresh=1"))
        assert response.status_code == 401
        assert response.get_json()["success"] is False

    def test_refresh_returns_library_sections(self, auth_client, spotify, playlists):
        response = auth_client.get(api_path("/library?refresh=1"))
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert set(data["sections"]) == {"favorites", "playlists", "hidden"}
        assert [row["id"] for row in data["sections"]["playlists"]] == ["p1", "p2"]

    @pytest.mark.parametrize("query,skip_cache", [("?refresh=1", True), ("?refresh=0", False), ("", False)])
    def test_refresh_bypasses_cache_only_when_requested(self, auth_client, spotify, playlists, query, skip_cache):
        response = auth_client.get(api_path(f"/library{query}"))
        assert response.status_code == 200
        assert playlists.call_args.kwargs == {"skip_cache": skip_cache}

    def test_refresh_handles_playlist_error(self, auth_client, spotify, playlists):
        from shuffify.services import PlaylistError

        playlists.side_effect = PlaylistError("API unavailable")
        response = auth_client.get(api_path("/library?refresh=1"))
        assert response.status_code == 500
        assert response.get_json()["success"] is False

    def test_refresh_preserves_saved_undo_history(self, auth_client, spotify, playlists):
        history = {"p1": {"states": [["spotify:track:first"], ["spotify:track:second"]], "current_index": 1}}
        with auth_client.session_transaction() as session:
            session["playlist_states"] = history

        response = auth_client.get(api_path("/library?refresh=1"))

        assert response.status_code == 200
        rows = response.get_json()["sections"]["playlists"]
        assert next(row for row in rows if row["id"] == "p1")["can_undo"] is True
        with auth_client.session_transaction() as session:
            assert session["playlist_states"] == history
