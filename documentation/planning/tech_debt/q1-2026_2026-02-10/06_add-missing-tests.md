# Phase 6: Add Missing Tests for Error Handlers and Playlist Model

> **Status:** ✅ COMPLETE
> **Started:** 2026-02-10
> **Completed:** 2026-02-10
> **PR:** #42

| Attribute | Value |
|-----------|-------|
| **PR Title** | `test: Add direct test coverage for error handlers and Playlist model` |
| **Risk Level** | None (tests only) |
| **Estimated Effort** | 60-90 minutes |
| **Files Modified** | 2 (both new) |
| **Dependencies** | Phase 5 (dead code removal changes the Playlist model API) |
| **Blocks** | Nothing |

---

## Overview

Three core modules lack direct test coverage:
1. `shuffify/error_handlers.py` — 193 lines with 15+ error handlers, tested only indirectly
2. `shuffify/models/playlist.py` — 159 lines with data transformation logic, tested only via integration

This PR adds dedicated test files that exercise these modules directly, covering edge cases that integration tests miss.

**Why:** Error handler behavior (status codes, response format, error messages) and model transformation logic (track building, stats calculation) are critical to the user experience. Direct tests make regressions immediately visible.

**Note:** `shuffify/routes.py` is intentionally NOT included in this phase. Route testing requires full Flask app context with mocked Spotify API, which is already partially covered by `tests/test_integration.py`. Adding comprehensive route tests is a larger effort better suited for a separate PR.

---

## Files Created

| File | Tests |
|------|-------|
| `tests/test_error_handlers.py` | **NEW** — tests for all registered error handlers |
| `tests/test_models.py` | **NEW** — tests for Playlist model methods |

---

## Step-by-Step Implementation

### Step 1: Create tests/test_error_handlers.py

**File:** `tests/test_error_handlers.py` (NEW FILE)

This test file must:
1. Create a Flask test app with error handlers registered
2. Define routes that raise each exception type
3. Verify the response status code, JSON structure, and message

```python
"""
Tests for global Flask error handlers.

Verifies that each service-layer exception is caught and converted
to the correct JSON error response with appropriate HTTP status code.
"""

import pytest
from flask import Flask

from shuffify.error_handlers import register_error_handlers
from shuffify.services import (
    AuthenticationError,
    TokenValidationError,
    PlaylistError,
    PlaylistNotFoundError,
    PlaylistUpdateError,
    ShuffleError,
    InvalidAlgorithmError,
    ParameterValidationError,
    ShuffleExecutionError,
    StateError,
    NoHistoryError,
    AlreadyAtOriginalError,
)


@pytest.fixture
def app():
    """Create a test Flask app with error handlers registered."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_error_handlers(app)

    # Register routes that raise each exception type for testing
    @app.route("/raise/auth-error")
    def raise_auth_error():
        raise AuthenticationError("Test auth error")

    @app.route("/raise/token-validation-error")
    def raise_token_validation_error():
        raise TokenValidationError("Test token error")

    @app.route("/raise/playlist-not-found")
    def raise_playlist_not_found():
        raise PlaylistNotFoundError("Test not found")

    @app.route("/raise/playlist-update-error")
    def raise_playlist_update_error():
        raise PlaylistUpdateError("Test update error")

    @app.route("/raise/playlist-error")
    def raise_playlist_error():
        raise PlaylistError("Test playlist error")

    @app.route("/raise/invalid-algorithm")
    def raise_invalid_algorithm():
        raise InvalidAlgorithmError("Test invalid algorithm")

    @app.route("/raise/parameter-validation-error")
    def raise_parameter_validation_error():
        raise ParameterValidationError("Test param error")

    @app.route("/raise/shuffle-execution-error")
    def raise_shuffle_execution_error():
        raise ShuffleExecutionError("Test execution error")

    @app.route("/raise/shuffle-error")
    def raise_shuffle_error():
        raise ShuffleError("Test shuffle error")

    @app.route("/raise/no-history")
    def raise_no_history():
        raise NoHistoryError("Test no history")

    @app.route("/raise/already-at-original")
    def raise_already_at_original():
        raise AlreadyAtOriginalError("Test already original")

    @app.route("/raise/state-error")
    def raise_state_error():
        raise StateError("Test state error")

    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


class TestErrorHandlerResponseFormat:
    """Verify all error responses have the standard JSON structure."""

    def _assert_json_error(self, response, expected_status, expected_category="error"):
        """Helper to verify standard error response format."""
        assert response.status_code == expected_status
        data = response.get_json()
        assert data is not None, "Response should be JSON"
        assert "success" in data
        assert data["success"] is False
        assert "message" in data
        assert "category" in data
        assert data["category"] == expected_category

    def test_authentication_error_returns_401(self, client):
        response = client.get("/raise/auth-error")
        self._assert_json_error(response, 401)
        assert "log in" in response.get_json()["message"].lower()

    def test_token_validation_error_returns_401(self, client):
        response = client.get("/raise/token-validation-error")
        self._assert_json_error(response, 401)
        assert "session" in response.get_json()["message"].lower()

    def test_playlist_not_found_returns_404(self, client):
        response = client.get("/raise/playlist-not-found")
        self._assert_json_error(response, 404)

    def test_playlist_update_error_returns_500(self, client):
        response = client.get("/raise/playlist-update-error")
        self._assert_json_error(response, 500)

    def test_playlist_error_returns_400(self, client):
        response = client.get("/raise/playlist-error")
        self._assert_json_error(response, 400)

    def test_invalid_algorithm_returns_400(self, client):
        response = client.get("/raise/invalid-algorithm")
        self._assert_json_error(response, 400)

    def test_parameter_validation_error_returns_400(self, client):
        response = client.get("/raise/parameter-validation-error")
        self._assert_json_error(response, 400)

    def test_shuffle_execution_error_returns_500(self, client):
        response = client.get("/raise/shuffle-execution-error")
        self._assert_json_error(response, 500)

    def test_shuffle_error_returns_500(self, client):
        response = client.get("/raise/shuffle-error")
        self._assert_json_error(response, 500)

    def test_no_history_error_returns_404(self, client):
        response = client.get("/raise/no-history")
        self._assert_json_error(response, 404)

    def test_already_at_original_returns_400_with_info_category(self, client):
        response = client.get("/raise/already-at-original")
        self._assert_json_error(response, 400, expected_category="info")

    def test_state_error_returns_500(self, client):
        response = client.get("/raise/state-error")
        self._assert_json_error(response, 500)


class TestHTTPErrorHandlers:
    """Test standard HTTP error handlers."""

    def test_404_for_api_route_returns_json(self, client):
        response = client.get("/api/nonexistent")
        assert response.status_code == 404
        data = response.get_json()
        assert data is not None
        assert data["success"] is False

    def test_404_for_html_route_returns_default(self, client):
        response = client.get("/nonexistent-page")
        # Non-API routes may return HTML 404 or JSON depending on implementation
        assert response.status_code == 404
```

### Step 2: Create tests/test_models.py

**File:** `tests/test_models.py` (NEW FILE)

This test file covers the `Playlist` dataclass directly, including construction, validation, track operations, and statistics.

**Important:** This file must be written AFTER Phase 5 completes, because Phase 5 removes `get_track()`, `get_track_with_features()`, and `get_tracks_with_features()`. Do not write tests for methods that will be removed.

```python
"""
Tests for the Playlist data model.

Tests construction, validation, track operations, feature statistics,
and serialization without requiring Spotify API access.
"""

import pytest

from shuffify.models.playlist import Playlist


class TestPlaylistConstruction:
    """Test Playlist creation and validation."""

    def test_create_minimal_playlist(self):
        playlist = Playlist(id="abc123", name="My Playlist", owner_id="user1")
        assert playlist.id == "abc123"
        assert playlist.name == "My Playlist"
        assert playlist.owner_id == "user1"
        assert playlist.tracks == []
        assert playlist.audio_features == {}
        assert playlist.description is None

    def test_create_playlist_with_all_fields(self):
        tracks = [{"id": "t1", "uri": "spotify:track:t1", "name": "Track 1"}]
        features = {"t1": {"tempo": 120.0, "energy": 0.8}}
        playlist = Playlist(
            id="abc123",
            name="Full Playlist",
            owner_id="user1",
            description="A test playlist",
            tracks=tracks,
            audio_features=features,
        )
        assert playlist.description == "A test playlist"
        assert len(playlist.tracks) == 1
        assert "t1" in playlist.audio_features

    def test_empty_id_raises_value_error(self):
        with pytest.raises(ValueError, match="Playlist ID is required"):
            Playlist(id="", name="Test", owner_id="user1")

    def test_none_id_raises_error(self):
        with pytest.raises((ValueError, TypeError)):
            Playlist(id=None, name="Test", owner_id="user1")


class TestPlaylistTrackOperations:
    """Test track-related methods."""

    @pytest.fixture
    def playlist_with_tracks(self):
        tracks = [
            {"id": "t1", "uri": "spotify:track:t1", "name": "Track 1"},
            {"id": "t2", "uri": "spotify:track:t2", "name": "Track 2"},
            {"id": "t3", "uri": "spotify:track:t3", "name": "Track 3"},
        ]
        return Playlist(id="p1", name="Test", owner_id="u1", tracks=tracks)

    def test_get_track_uris(self, playlist_with_tracks):
        uris = playlist_with_tracks.get_track_uris()
        assert uris == [
            "spotify:track:t1",
            "spotify:track:t2",
            "spotify:track:t3",
        ]

    def test_get_track_uris_skips_missing_uri(self):
        tracks = [
            {"id": "t1", "uri": "spotify:track:t1", "name": "Track 1"},
            {"id": "t2", "name": "Track 2"},  # No URI
        ]
        playlist = Playlist(id="p1", name="Test", owner_id="u1", tracks=tracks)
        uris = playlist.get_track_uris()
        assert uris == ["spotify:track:t1"]

    def test_get_track_uris_empty_playlist(self):
        playlist = Playlist(id="p1", name="Test", owner_id="u1")
        assert playlist.get_track_uris() == []

    def test_len_returns_track_count(self, playlist_with_tracks):
        assert len(playlist_with_tracks) == 3

    def test_len_empty_playlist(self):
        playlist = Playlist(id="p1", name="Test", owner_id="u1")
        assert len(playlist) == 0

    def test_getitem_returns_track(self, playlist_with_tracks):
        track = playlist_with_tracks[0]
        assert track["name"] == "Track 1"

    def test_getitem_out_of_range_raises(self, playlist_with_tracks):
        with pytest.raises(IndexError):
            _ = playlist_with_tracks[10]

    def test_iter_yields_tracks(self, playlist_with_tracks):
        names = [t["name"] for t in playlist_with_tracks]
        assert names == ["Track 1", "Track 2", "Track 3"]


class TestPlaylistFeatures:
    """Test audio feature methods."""

    def test_has_features_true(self):
        playlist = Playlist(
            id="p1",
            name="Test",
            owner_id="u1",
            audio_features={"t1": {"tempo": 120.0}},
        )
        assert playlist.has_features() is True

    def test_has_features_false(self):
        playlist = Playlist(id="p1", name="Test", owner_id="u1")
        assert playlist.has_features() is False

    def test_get_feature_stats_computes_correctly(self):
        features = {
            "t1": {"tempo": 100.0, "energy": 0.5, "valence": 0.3, "danceability": 0.6},
            "t2": {"tempo": 140.0, "energy": 0.9, "valence": 0.7, "danceability": 0.8},
        }
        playlist = Playlist(
            id="p1", name="Test", owner_id="u1", audio_features=features
        )
        stats = playlist.get_feature_stats()

        assert stats["tempo"]["min"] == 100.0
        assert stats["tempo"]["max"] == 140.0
        assert stats["tempo"]["avg"] == 120.0
        assert stats["energy"]["min"] == 0.5
        assert stats["energy"]["max"] == 0.9
        assert stats["energy"]["avg"] == pytest.approx(0.7)

    def test_get_feature_stats_empty_features(self):
        playlist = Playlist(id="p1", name="Test", owner_id="u1")
        assert playlist.get_feature_stats() == {}

    def test_get_feature_stats_partial_features(self):
        """Test when some tracks have features and others don't."""
        features = {
            "t1": {"tempo": 120.0, "energy": 0.5},
            # Missing valence and danceability
        }
        playlist = Playlist(
            id="p1", name="Test", owner_id="u1", audio_features=features
        )
        stats = playlist.get_feature_stats()
        assert "tempo" in stats
        assert stats["tempo"]["avg"] == 120.0


class TestPlaylistSerialization:
    """Test to_dict and string representation."""

    def test_to_dict_contains_all_fields(self):
        tracks = [{"id": "t1", "uri": "spotify:track:t1", "name": "Track 1"}]
        playlist = Playlist(
            id="p1",
            name="Test Playlist",
            owner_id="u1",
            description="desc",
            tracks=tracks,
            audio_features={"t1": {"tempo": 120}},
        )
        d = playlist.to_dict()
        assert d["id"] == "p1"
        assert d["name"] == "Test Playlist"
        assert d["owner_id"] == "u1"
        assert d["description"] == "desc"
        assert len(d["tracks"]) == 1
        assert "t1" in d["audio_features"]

    def test_to_dict_with_defaults(self):
        playlist = Playlist(id="p1", name="Test", owner_id="u1")
        d = playlist.to_dict()
        assert d["description"] is None
        assert d["tracks"] == []
        assert d["audio_features"] == {}

    def test_str_representation(self):
        tracks = [{"id": f"t{i}", "uri": f"uri:{i}", "name": f"T{i}"} for i in range(5)]
        playlist = Playlist(id="p1", name="My Mix", owner_id="u1", tracks=tracks)
        s = str(playlist)
        assert "My Mix" in s
        assert "p1" in s
        assert "5 tracks" in s
```

---

## Verification Checklist

- [ ] `tests/test_error_handlers.py` exists and all tests pass
- [ ] `tests/test_models.py` exists and all tests pass
- [ ] Run only new tests: `pytest tests/test_error_handlers.py tests/test_models.py -v`
- [ ] Full test suite still passes: `pytest tests/ -v`
- [ ] Lint passes: `flake8 tests/`
- [ ] No tests reference removed methods (`get_track`, `get_track_with_features`, `get_tracks_with_features`)

---

## What NOT To Do

- **Do NOT write tests for `Playlist.get_track()`, `get_track_with_features()`, or `get_tracks_with_features()`** — these are removed in Phase 5. If Phase 5 has not been completed yet, wait.
- **Do NOT write tests for routes** in this PR. Route testing is a separate, larger effort.
- **Do NOT mock Flask's error handling system.** Use a real Flask test app with real error handlers registered. Mocking would test the mock, not the actual handler behavior.
- **Do NOT test Pydantic `ValidationError` handling** by importing Pydantic errors directly — that handler is tested via the existing integration tests and requires form data parsing.
- **Do NOT add test dependencies.** All required packages (`pytest`, `flask`) are already in `requirements/dev.txt`.
- **Do NOT import from `shuffify.error_handlers` in test_models.py** or vice versa. Keep each test file focused on its module.
