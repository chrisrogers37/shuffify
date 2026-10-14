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
from shuffify.services import PlaylistAccessError, PlaylistError, PlaylistNotFoundError
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import SpotifyError, SpotifyTokenExpiredError

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


class TestWorkshopPage:
    """Tests for GET /workshop/<playlist_id>."""

    def test_workshop_redirects_when_not_authenticated(self, client):
        """Unauthenticated users should be redirected to index."""
        response = client.get("/workshop/playlist123")
        assert response.status_code in (302, 200)

    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch("shuffify.routes.workshop.ShuffleService")
    def test_workshop_renders_with_valid_playlist(
        self,
        mock_shuffle_svc,
        mock_playlist_svc,
        mock_auth_svc,
        mock_get_db_user,
        authenticated_client,
        sample_user,
    ):
        """Workshop page should render successfully with playlist data."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()
        mock_auth_svc.get_user_data.return_value = sample_user
        mock_get_db_user.return_value = Mock(id=1, spotify_id="u1")

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

    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch("shuffify.routes.workshop.ShuffleService")
    def test_workshop_escapes_malicious_track_uri(
        self,
        mock_shuffle_svc,
        mock_playlist_svc,
        mock_auth_svc,
        mock_get_db_user,
        authenticated_client,
        sample_user,
    ):
        """track.uri is JS-escaped (tojson) in onclick handlers — XSS guard."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()
        mock_auth_svc.get_user_data.return_value = sample_user
        mock_get_db_user.return_value = Mock(id=1, spotify_id="u1")

        malicious_uri = "spotify:track:x'));alert(document.cookie);//"
        playlist = Playlist(
            id="playlist123",
            name="Workshop Test Playlist",
            owner_id="user123",
            description="A test playlist",
            tracks=[
                {
                    "id": "track1",
                    "name": "Track 1",
                    "uri": malicious_uri,
                    "duration_ms": 180000,
                    "is_local": False,
                    "artists": ["Artist 1"],
                    "artist_urls": ["https://open.spotify.com/artist/artist1"],
                    "album_name": "Album 1",
                    "album_image_url": "https://example.com/album1.jpg",
                    "track_url": "https://open.spotify.com/track/track1",
                }
            ],
        )
        mock_ps_instance = Mock()
        mock_ps_instance.get_playlist.return_value = playlist
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
        # tojson JS-escapes the single quote (') rather than HTML-escaping
        # it (&#39;), so it cannot break out of the onclick JS string.
        assert b"\\u0027" in response.data
        # Single-quote-delimited inline handler calls must not appear. (The
        # data-uri="" attribute legitimately HTML-escapes the quote — that is
        # the safe attribute context, read back cleanly via element.dataset.)
        assert b"toggleTrackLock('" not in response.data
        assert b"deleteTrack('" not in response.data

    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.AuthService", autospec=True)
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch("shuffify.routes.workshop.ShuffleService")
    def test_workshop_renders_when_spotify_profile_is_unavailable(
        self,
        mock_shuffle_svc,
        mock_playlist_svc,
        mock_auth_svc,
        mock_get_db_user,
        authenticated_client,
    ):
        """A failed profile fetch must not 500 an authenticated user.

        require_auth_page swallows SpotifyError from the profile fetch and
        continues with spotify_profile=None deliberately -- a Spotify hiccup
        should not bounce an authenticated user back to the login page. Every
        profile dereference on the page has to survive the None that produces.
        """
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_api.return_value = Mock()
        mock_auth_svc.get_user_data.side_effect = SpotifyError(
            "Spotify profile fetch failed"
        )
        mock_get_db_user.return_value = Mock(id=1, spotify_id="u1")

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
        # tojson on an unguarded Undefined raises TypeError, which reaches the
        # generic 500 page. null is the honest value for "current user
        # unknown", and the one consumer (the isNonOwned ownership check)
        # degrades to treating playlists as not-yours rather than crashing.
        assert b"currentUserId: null" in response.data

    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.AuthService", autospec=True)
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch("shuffify.routes.workshop.ShuffleService")
    def test_workshop_emits_the_real_user_id_when_profile_is_available(
        self,
        mock_shuffle_svc,
        mock_playlist_svc,
        mock_auth_svc,
        mock_get_db_user,
        authenticated_client,
        sample_user,
    ):
        """The null-guard must not cost the real id.

        The other half of the pair. Asserting only the None case is satisfied
        by deleting the line or hardcoding null, which would break the
        ownership check for every user whose profile loaded fine.
        """
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_api.return_value = Mock()
        mock_auth_svc.get_user_data.return_value = sample_user
        mock_get_db_user.return_value = Mock(id=1, spotify_id="u1")

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
        assert b'currentUserId: "user123"' in response.data

    @patch("shuffify.routes.workshop.PlaylistPreferenceService")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.AuthService", autospec=True)
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch("shuffify.routes.workshop.ShuffleService")
    def test_workshop_names_its_neighbouring_playlists(
        self,
        mock_shuffle_svc,
        mock_playlist_svc,
        mock_auth_svc,
        mock_get_db_user,
        mock_preferences,
        authenticated_client,
        sample_user,
    ):
        """The previous and next links name their playlists from the route, escaped, so the
        page reads no playlists on load to name them; a neighbour without a name keeps the
        plain label, and the first playlist has no previous link."""
        self._logged_in(mock_auth_svc, mock_get_db_user, sample_user)
        mock_preferences.get_user_preferences.return_value = []
        mock_ps_instance = Mock()
        mock_ps_instance.get_playlist.return_value = _make_mock_playlist()
        mock_playlist_svc.return_value = mock_ps_instance
        mock_shuffle_svc.list_algorithms.return_value = [
            {"name": "Basic", "class_name": "BasicShuffle", "description": "Random shuffle", "parameters": {}}
        ]

        mock_ps_instance.get_user_playlists.return_value = [
            {"id": "earlier", "name": "Earlier <b>Mix</b>"},
            {"id": "playlist123", "name": "Workshop Test Playlist"},
            {"id": "later", "name": "Later & Loud"},
        ]
        html = authenticated_client.get("/workshop/playlist123").get_data(as_text=True)
        assert 'aria-label="Previous playlist: Earlier &lt;b&gt;Mix&lt;/b&gt;"' in html
        assert 'aria-label="Next playlist: Later &amp; Loud"' in html
        # Playlist details repeats them as links with the same names.
        assert ">Previous playlist: Earlier &lt;b&gt;Mix&lt;/b&gt;</a>" in html
        assert ">Next playlist: Later &amp; Loud</a>" in html
        assert "<b>Mix</b>" not in html

        mock_ps_instance.get_user_playlists.return_value = [
            {"id": "playlist123", "name": "Workshop Test Playlist"},
            {"id": "later"},
        ]
        html = authenticated_client.get("/workshop/playlist123").get_data(as_text=True)
        assert 'id="workshop-prev-playlist"' not in html
        assert 'aria-label="Next playlist"' in html
        assert ">Next playlist</a>" in html

    @staticmethod
    def _logged_in(mock_auth_svc, mock_get_db_user, sample_user):
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_api.return_value = Mock()
        mock_auth_svc.get_user_data.return_value = sample_user
        mock_get_db_user.return_value = Mock(id=1, spotify_id="u1")

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (
                PlaylistNotFoundError("Playlist not found: playlist123"),
                "That playlist doesn't exist or was deleted.",
            ),
            (
                PlaylistAccessError("Spotify refused this request for the playlist."),
                "You can only open your own or collaborative playlists in the Workshop.",
            ),
            (
                PlaylistError("Failed to fetch playlist: Server error 503 after 5 attempts"),
                "Couldn't load that playlist from Spotify. Please try again.",
            ),
        ],
    )
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.AuthService", autospec=True)
    @patch("shuffify.routes.workshop.PlaylistService")
    def test_a_playlist_it_cannot_load_keeps_the_user_logged_in(
        self,
        mock_playlist_svc,
        mock_auth_svc,
        mock_get_db_user,
        authenticated_client,
        sample_user,
        error,
        message,
    ):
        """A missing playlist, someone else's, or a Spotify outage is not an
        expired session: back to the Workshop hub, still logged in,
        with a message that fits."""
        self._logged_in(mock_auth_svc, mock_get_db_user, sample_user)
        mock_playlist_svc.return_value.get_playlist.side_effect = error

        response = authenticated_client.get("/workshop/playlist123")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/workshop")
        with authenticated_client.session_transaction() as sess:
            assert "spotify_token" in sess
            assert sess["_flashes"] == [("error", message)]

    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.AuthService", autospec=True)
    @patch("shuffify.routes.workshop.PlaylistService")
    def test_a_session_spotify_refuses_still_logs_the_user_out(
        self,
        mock_playlist_svc,
        mock_auth_svc,
        mock_get_db_user,
        authenticated_client,
        sample_user,
    ):
        """A token Spotify refuses, after a refresh that failed, is the one
        failure that ends the session."""
        self._logged_in(mock_auth_svc, mock_get_db_user, sample_user)
        mock_playlist_svc.return_value.get_playlist.side_effect = SpotifyTokenExpiredError(
            "Token expired or invalid"
        )

        response = authenticated_client.get("/workshop/playlist123")

        assert response.status_code == 200
        assert b"Your session has expired. Please log in again." in response.data
        with authenticated_client.session_transaction() as sess:
            assert "spotify_token" not in sess


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
            "/workshop/playlist123/preview-shuffle",
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
                "/workshop/playlist123/commit",
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
        self, authenticated_client, authorized, status
    ):
        from shuffify.services.workshop_commit_service import WorkshopTargetResult

        service, _ = authorized
        uris = ["spotify:track:" + "a" * 22]
        service.return_value.commit.return_value = WorkshopTargetResult(
            status,
            "Confirmed",
            extra={"saved_track_uris": uris},
        )
        response = authenticated_client.post(
            "/workshop/playlist123/commit",
            json={"baseline_track_uris": [], "track_uris": uris},
        )
        assert response.status_code == 200
        assert response.get_json()["saved_track_uris"] == uris
        assert service.return_value.commit.call_args.args[3:] == ([], uris)

    def test_missing_baseline_is_explicit_and_never_calls_service(
        self, authenticated_client, authorized
    ):
        service, _ = authorized
        response = authenticated_client.post(
            "/workshop/playlist123/commit", json={"track_uris": []}
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
            "/workshop/playlist123/commit", json=payload
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
            "/workshop/playlist123/commit",
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

    @pytest.mark.parametrize(
        "status,code,http",
        [
            ("not_written", "stale_draft", 409),
            ("not_written", "operation_in_progress", 409),
            ("partial", "partial_write", 502),
            ("unknown", "write_unverified", 502),
        ],
    )
    def test_failure_outcomes_are_not_flattened(
        self, authenticated_client, authorized, status, code, http
    ):
        from shuffify.services.workshop_commit_service import WorkshopTargetResult

        service, _ = authorized
        service.return_value.commit.return_value = WorkshopTargetResult(
            status,
            "Draft retained",
            http,
            code,
            True,
        )
        response = authenticated_client.post(
            "/workshop/playlist123/commit",
            json={"baseline_track_uris": [], "track_uris": []},
        )
        assert response.status_code == http
        assert response.get_json()["error_code"] == code
        assert response.get_json()["target_status"] == status
        assert "saved_track_uris" not in response.get_json()

    @pytest.mark.parametrize(
        "error,status",
        [
            (PlaylistAccessError("Cannot edit this playlist"), 403),
            (SpotifyTokenExpiredError("Expired"), 401),
            (ServiceUnavailable("Response failed after dispatch"), 503),
        ],
    )
    def test_existing_permission_and_auth_handling(
        self, authenticated_client, authorized, error, status
    ):
        service, _ = authorized
        service.return_value.commit.side_effect = error
        response = authenticated_client.post(
            "/workshop/playlist123/commit",
            json={"baseline_track_uris": [], "track_uris": []},
        )
        assert response.status_code == status
        assert "target_status" not in (response.get_json() or {})

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
        response = authenticated_client.get("/workshop/playlist123/current-state")
        assert response.status_code == 200
        assert response.get_json()["current_track_uris"] == []
        service.return_value.commit.assert_not_called()
        with authenticated_client.session_transaction() as session:
            assert session["retained_draft"] == ["draft"]

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/workshop/playlist123/commit"),
            ("get", "/workshop/playlist123/current-state"),
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
