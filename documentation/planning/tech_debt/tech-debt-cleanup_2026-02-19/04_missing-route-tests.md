# Phase 4: Missing Route Tests -- Detailed Remediation Plan

## PR Metadata

| Field | Value |
|---|---|
| **PR title** | `Test: Add missing route-level tests for 5 untested modules` |
| **Risk level** | Low (test files only, no source changes) |
| **Estimated effort** | 4-5 hours |
| **Files created** | 5 new test files in `tests/routes/` |
| **Dependencies** | Phase 1 (route cleanup should be completed first to avoid churn) |
| **Blocks** | None |

## 1. Current State of Affairs

### Existing test coverage by route module

| Route Module | Endpoints | Dedicated Test File | Status |
|---|---|---|---|
| `core.py` | 7 | None (partial overlap in `test_health_db.py`, `test_app_factory.py`) | **NEEDS TEST FILE** |
| `playlists.py` | 4 | None | **NEEDS TEST FILE** |
| `shuffle.py` | 2 | None | **NEEDS TEST FILE** |
| `workshop.py` | 11 | `test_workshop*.py` (4 files at root level) | Covered |
| `settings.py` | 2 | `test_settings_route.py` | Covered |
| `upstream_sources.py` | 3 | None | **NEEDS TEST FILE** |
| `schedules.py` | 8 | None | **NEEDS TEST FILE** |
| `snapshots.py` | 5 | `test_snapshot_routes.py` | Covered |
| `playlist_pairs.py` | 6 | `tests/routes/test_playlist_pairs_routes.py` | Covered |
| `raid_panel.py` | 5 | `test_raid_panel_routes.py` | Covered |

Current total test count: **1099** (as of this plan).

### Overlap inventory for core routes

The following tests already exist and overlap with `core.py` endpoints:

- `/health` -- fully tested in `/Users/chris/Projects/shuffify/tests/test_health_db.py` (7 tests). Do NOT duplicate. Reference from the new core test file but add supplementary tests only.
- `/` -- one incidental hit in `test_app_factory.py` line 305 (`client.get("/")`), used for security header testing. Not a proper route test.
- `/login`, `/callback`, `/logout`, `/terms`, `/privacy` -- zero dedicated tests anywhere.

## 2. Testing Patterns and Conventions (Extracted from Existing Code)

Every new test file MUST follow these patterns exactly. Deviating from them makes the suite inconsistent and harder to maintain.

### Pattern A: File-level `db_app` fixture

Every test file that needs database access defines its own local `db_app` fixture. It does NOT use the `app` fixture from `conftest.py` (that one does not create a user or call `db.drop_all()`). The canonical pattern lives in `/Users/chris/Projects/shuffify/tests/test_snapshot_routes.py` lines 19-47 and `/Users/chris/Projects/shuffify/tests/test_raid_panel_routes.py` lines 16-44. Use this exact structure:

```python
@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"
    os.environ.pop("DATABASE_URL", None)

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///:memory:"
    )
    app.config["TESTING"] = True
    app.config["SCHEDULER_ENABLED"] = False

    with app.app_context():
        db.drop_all()
        db.create_all()
        UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield app
        db.session.remove()
        db.drop_all()
```

**CRITICAL**: The `db.drop_all()` before `db.create_all()` ensures a clean slate. The `yield` inside `app.app_context()` keeps the context open for the entire test. The cleanup at the end removes the session and drops all tables.

### Pattern B: `auth_client` fixture

Every test file defines an `auth_client` that pre-populates the session with a valid token and user_data. The canonical pattern is from `/Users/chris/Projects/shuffify/tests/test_snapshot_routes.py` lines 50-66:

```python
@pytest.fixture
def auth_client(db_app):
    """Authenticated test client with session user data."""
    with db_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "expires_at": time.time() + 3600,
                "refresh_token": "test_refresh",
            }
            sess["user_data"] = {
                "id": "user123",
                "display_name": "Test User",
            }
        yield client
```

The `user_data["id"]` value `"user123"` MUST match the `spotify_id` used in `db_app` fixture's `UserService.upsert_from_spotify(...)` call so that `get_db_user()` in the route code can resolve the session user to a database row.

### Pattern C: Mocking `require_auth`

Routes that call `require_auth()` need it patched at the route module level. The patch target follows the pattern `"shuffify.routes.<module_name>.require_auth"`. For unauthenticated tests, set `mock_auth.return_value = None`. For authenticated tests, set `mock_auth.return_value = MagicMock()`. See `/Users/chris/Projects/shuffify/tests/test_snapshot_routes.py` line 72 for example.

**IMPORTANT**: Some routes (like `schedules.py` line 51-53) use `is_authenticated()` instead of `require_auth()`. Those routes need `is_authenticated` patched, OR the session token can be omitted entirely (the function checks the session directly via `AuthService.validate_session_token`).

### Pattern D: Test class organization

Tests are grouped by endpoint into classes. Each class name describes the endpoint being tested. See examples:
- `TestListSnapshots` for `GET /playlist/<id>/snapshots`
- `TestRaidAuthRequired` for grouped auth guard tests
- `TestCreatePairRoute` for `POST /playlist/<id>/pair`

### Pattern E: Auth guard test

Every endpoint MUST have a test verifying that an unauthenticated request returns either `401` or `302` (redirect). The exact behavior depends on whether the route uses `require_auth()` (returns 401 JSON) or `is_authenticated()` (returns 302 redirect).

### Pattern F: Imports

Standard imports block for route test files:

```python
import time
import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db
from shuffify.services.user_service import UserService
```

Additional imports as needed per file (services, enums, etc.).

## 3. Detailed Implementation Instructions

### Pre-requisites

1. Activate the virtual environment: `source venv/bin/activate`
2. Verify the full test suite currently passes: `pytest tests/ -v`
3. Verify `tests/routes/` directory exists (it does; it currently contains only `test_playlist_pairs_routes.py` and `__pycache__/`)
4. Note: There is NO `__init__.py` in `tests/routes/` -- pytest discovers tests there via directory traversal. Do NOT create an `__init__.py`.

---

### Test File 1: `tests/routes/test_shuffle_routes.py`

**Source file**: `/Users/chris/Projects/shuffify/shuffify/routes/shuffle.py` (204 lines, 2 endpoints)

**Endpoints to test**:

#### `POST /shuffle/<playlist_id>` (lines 32-162)

Code path analysis:
1. **Auth guard** (line 35-37): `require_auth()` returns `None` -> 401 JSON
2. **Parse request** (line 39-41): `parse_shuffle_request(request.form.to_dict())` -- can raise `ValidationError` (caught by global handler)
3. **Get algorithm** (line 43-44): `ShuffleService.get_algorithm(...)` -- can raise `InvalidAlgorithmError`
4. **Get playlist** (line 48-52): `PlaylistService(client)` then `.get_playlist(playlist_id)` -- can raise `PlaylistNotFoundError`
5. **Validate tracks** (line 52): `playlist_service.validate_playlist_has_tracks(playlist)` -- can raise `PlaylistError`
6. **Auto-snapshot** (lines 57-82): Only runs if `is_db_available()` and user exists with auto-snapshot enabled. Catches all exceptions internally.
7. **State management** (lines 85-92): `StateService.ensure_playlist_initialized(...)` and `StateService.get_current_uris(...)`
8. **Execute shuffle** (lines 100-105): `ShuffleService.execute(...)` -- can raise `ShuffleExecutionError`
9. **Check if changed** (lines 107-116): If shuffle did not change order, returns `{"success": False, "message": "Shuffle did not change the playlist order.", "category": "info"}`
10. **Update Spotify** (lines 118-119): `playlist_service.update_playlist_tracks(...)` -- can raise `PlaylistUpdateError`
11. **Success path** (lines 158-162): Returns `json_success(...)` with playlist and playlist_state dicts

Mocking requirements:
- `shuffify.routes.shuffle.require_auth` -- controls auth
- `shuffify.routes.shuffle.PlaylistService` -- avoid Spotify API calls
- `shuffify.routes.shuffle.ShuffleService` -- control shuffle output
- `shuffify.routes.shuffle.StateService` -- control state management
- `shuffify.routes.shuffle.is_db_available` -- control snapshot behavior
- `shuffify.routes.shuffle.parse_shuffle_request` -- or let it parse real form data (recommended for validation tests)

Test cases:

```python
"""
Tests for shuffle routes.

Tests cover POST /shuffle/<playlist_id> and POST /undo/<playlist_id>.
"""

import time
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from shuffify.models.db import db
from shuffify.services.user_service import UserService


@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"
    os.environ.pop("DATABASE_URL", None)

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True
    app.config["SCHEDULER_ENABLED"] = False

    with app.app_context():
        db.drop_all()
        db.create_all()
        UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def auth_client(db_app):
    """Authenticated test client with session user data."""
    with db_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "expires_at": time.time() + 3600,
                "refresh_token": "test_refresh",
            }
            sess["user_data"] = {
                "id": "user123",
                "display_name": "Test User",
            }
        yield client


class TestShuffleAuth:
    """Auth guard tests for shuffle endpoints."""

    @patch("shuffify.routes.shuffle.require_auth")
    def test_shuffle_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/shuffle/playlist123",
                data={"algorithm": "BasicShuffle"},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.shuffle.require_auth")
    def test_undo_unauth(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post("/undo/playlist123")
            assert resp.status_code == 401


class TestShuffleEndpoint:
    """Tests for POST /shuffle/<playlist_id>."""

    @patch("shuffify.routes.shuffle.is_db_available")
    @patch("shuffify.routes.shuffle.StateService")
    @patch("shuffify.routes.shuffle.ShuffleService")
    @patch("shuffify.routes.shuffle.PlaylistService")
    @patch("shuffify.routes.shuffle.require_auth")
    def test_successful_shuffle(
        self,
        mock_auth,
        mock_ps_class,
        mock_ss,
        mock_state,
        mock_db_avail,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()
        mock_db_avail.return_value = False  # skip auto-snapshot

        # Mock playlist
        mock_playlist = MagicMock()
        mock_playlist.name = "Test Playlist"
        mock_playlist.tracks = [
            {"uri": f"spotify:track:t{i}"} for i in range(5)
        ]
        mock_playlist.to_dict.return_value = {"id": "p1", "name": "Test Playlist"}

        mock_ps = MagicMock()
        mock_ps.get_playlist.return_value = mock_playlist
        mock_ps_class.return_value = mock_ps

        # Mock shuffle
        mock_algorithm = MagicMock()
        mock_algorithm.name = "Basic Shuffle"
        mock_ss.get_algorithm.return_value = mock_algorithm
        shuffled = [f"spotify:track:t{i}" for i in range(4, -1, -1)]
        mock_ss.execute.return_value = shuffled
        mock_ss.shuffle_changed_order.return_value = True
        mock_ss.prepare_tracks_for_shuffle.return_value = mock_playlist.tracks

        # Mock state
        mock_state.get_current_uris.return_value = [
            f"spotify:track:t{i}" for i in range(5)
        ]
        mock_state_obj = MagicMock()
        mock_state_obj.to_dict.return_value = {"current_index": 1}
        mock_state.record_new_state.return_value = mock_state_obj

        resp = auth_client.post(
            "/shuffle/playlist123",
            data={"algorithm": "BasicShuffle"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        mock_ps.update_playlist_tracks.assert_called_once()

    @patch("shuffify.routes.shuffle.StateService")
    @patch("shuffify.routes.shuffle.ShuffleService")
    @patch("shuffify.routes.shuffle.PlaylistService")
    @patch("shuffify.routes.shuffle.require_auth")
    def test_shuffle_no_change_returns_info(
        self,
        mock_auth,
        mock_ps_class,
        mock_ss,
        mock_state,
        auth_client,
    ):
        """When shuffle produces same order, should return success=False with info."""
        mock_auth.return_value = MagicMock()

        mock_playlist = MagicMock()
        mock_playlist.tracks = [
            {"uri": "spotify:track:t1"}
        ]
        mock_ps = MagicMock()
        mock_ps.get_playlist.return_value = mock_playlist
        mock_ps_class.return_value = mock_ps

        uris = ["spotify:track:t1"]
        mock_ss.execute.return_value = uris
        mock_ss.shuffle_changed_order.return_value = False
        mock_ss.prepare_tracks_for_shuffle.return_value = mock_playlist.tracks
        mock_state.get_current_uris.return_value = uris

        resp = auth_client.post(
            "/shuffle/playlist123",
            data={"algorithm": "BasicShuffle"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is False
        assert "did not change" in data["message"]


class TestUndoEndpoint:
    """Tests for POST /undo/<playlist_id>."""

    @patch("shuffify.routes.shuffle.StateService")
    @patch("shuffify.routes.shuffle.PlaylistService")
    @patch("shuffify.routes.shuffle.require_auth")
    def test_successful_undo(
        self,
        mock_auth,
        mock_ps_class,
        mock_state,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()

        original_uris = ["spotify:track:t1", "spotify:track:t2"]
        mock_state.undo.return_value = original_uris

        mock_playlist = MagicMock()
        mock_playlist.to_dict.return_value = {"id": "p1"}
        mock_ps = MagicMock()
        mock_ps.get_playlist.return_value = mock_playlist
        mock_ps_class.return_value = mock_ps

        mock_state.get_state_info.return_value = {
            "current_index": 0
        }

        resp = auth_client.post("/undo/playlist123")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "restored" in data["message"].lower()

    @patch("shuffify.routes.shuffle.StateService")
    @patch("shuffify.routes.shuffle.PlaylistService")
    @patch("shuffify.routes.shuffle.require_auth")
    def test_undo_update_fails_reverts_state(
        self,
        mock_auth,
        mock_ps_class,
        mock_state,
        auth_client,
    ):
        """When Spotify update fails during undo, state should be reverted."""
        mock_auth.return_value = MagicMock()
        mock_state.undo.return_value = ["spotify:track:t1"]

        from shuffify.services import PlaylistUpdateError
        mock_ps = MagicMock()
        mock_ps.update_playlist_tracks.side_effect = PlaylistUpdateError("API fail")
        mock_ps_class.return_value = mock_ps

        resp = auth_client.post("/undo/playlist123")
        # PlaylistUpdateError is caught by error handler -> 500
        assert resp.status_code == 500
        mock_state.revert_undo.assert_called_once()
```

Total test count for this file: approximately **7-9 tests** (2 auth + 2 shuffle + 3 undo including edge cases).

---

### Test File 2: `tests/routes/test_playlists_routes.py`

**Source file**: `/Users/chris/Projects/shuffify/shuffify/routes/playlists.py` (109 lines, 4 endpoints)

**Endpoints to test**:

#### `POST /refresh-playlists` (lines 16-41)
- Auth guard (line 18-19)
- Success path: calls `PlaylistService(client).get_user_playlists(skip_cache=True)`, returns JSON with playlists
- Error path: catches `PlaylistError`, returns 500

#### `GET /playlist/<playlist_id>` (lines 44-59)
- Auth guard (line 46-47)
- Parses `features` query param via `PlaylistQueryParams`
- Returns `playlist.to_dict()` as JSON

#### `GET /playlist/<playlist_id>/stats` (lines 62-71)
- Auth guard (line 64-65)
- Returns stats dict as JSON

#### `GET /api/user-playlists` (lines 74-108)
- Auth guard (line 76-77)
- Returns formatted playlists list
- Catches `PlaylistError`, returns 500

Mocking requirements:
- `shuffify.routes.playlists.require_auth`
- `shuffify.routes.playlists.PlaylistService`

Test cases:

```python
"""
Tests for playlist routes.

Tests cover refresh-playlists, get-playlist, get-stats, and user-playlists API.
"""

import time
import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db
from shuffify.services.user_service import UserService


# db_app and auth_client fixtures (same pattern as above)
# ...


class TestRefreshPlaylists:
    """Tests for POST /refresh-playlists."""

    @patch("shuffify.routes.playlists.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post("/refresh-playlists")
            assert resp.status_code == 401

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_success_returns_playlists(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = [
            {"id": "p1", "name": "Playlist 1"}
        ]
        mock_ps_class.return_value = mock_ps

        resp = auth_client.post("/refresh-playlists")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 1
        mock_ps.get_user_playlists.assert_called_once_with(
            skip_cache=True
        )

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_playlist_error_returns_500(
        self, mock_auth, mock_ps_class, auth_client
    ):
        from shuffify.services import PlaylistError
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.side_effect = PlaylistError("fail")
        mock_ps_class.return_value = mock_ps

        resp = auth_client.post("/refresh-playlists")
        assert resp.status_code == 500


class TestGetPlaylist:
    """Tests for GET /playlist/<playlist_id>."""

    @patch("shuffify.routes.playlists.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/playlist/p1")
            assert resp.status_code == 401

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_returns_playlist_dict(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_playlist = MagicMock()
        mock_playlist.to_dict.return_value = {
            "id": "p1", "name": "My Playlist"
        }
        mock_ps = MagicMock()
        mock_ps.get_playlist.return_value = mock_playlist
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get("/playlist/p1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == "p1"

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_features_param_passed_through(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_playlist = MagicMock()
        mock_playlist.to_dict.return_value = {"id": "p1"}
        mock_ps = MagicMock()
        mock_ps.get_playlist.return_value = mock_playlist
        mock_ps_class.return_value = mock_ps

        auth_client.get("/playlist/p1?features=true")
        mock_ps.get_playlist.assert_called_once_with("p1", True)


class TestGetPlaylistStats:
    """Tests for GET /playlist/<playlist_id>/stats."""

    @patch("shuffify.routes.playlists.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/playlist/p1/stats")
            assert resp.status_code == 401

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_returns_stats(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_playlist_stats.return_value = {
            "avg_tempo": 120.0
        }
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get("/playlist/p1/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "avg_tempo" in data


class TestApiUserPlaylists:
    """Tests for GET /api/user-playlists."""

    @patch("shuffify.routes.playlists.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/api/user-playlists")
            assert resp.status_code == 401

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
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
                "images": [{"url": "https://example.com/img.jpg"}],
            }
        ]
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get("/api/user-playlists")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 1
        assert data["playlists"][0]["track_count"] == 10
        assert data["playlists"][0]["image_url"] is not None

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_playlist_without_images(
        self, mock_auth, mock_ps_class, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = [
            {"id": "p1", "name": "No Image", "tracks": {"total": 5}}
        ]
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get("/api/user-playlists")
        data = resp.get_json()
        assert data["playlists"][0]["image_url"] is None

    @patch("shuffify.routes.playlists.PlaylistService")
    @patch("shuffify.routes.playlists.require_auth")
    def test_playlist_error_returns_500(
        self, mock_auth, mock_ps_class, auth_client
    ):
        from shuffify.services import PlaylistError
        mock_auth.return_value = MagicMock()
        mock_ps = MagicMock()
        mock_ps.get_user_playlists.side_effect = PlaylistError("fail")
        mock_ps_class.return_value = mock_ps

        resp = auth_client.get("/api/user-playlists")
        assert resp.status_code == 500
```

Total test count for this file: approximately **12-14 tests**.

---

### Test File 3: `tests/routes/test_schedules_routes.py`

**Source file**: `/Users/chris/Projects/shuffify/shuffify/routes/schedules.py` (497 lines, 8 endpoints)

**Endpoints to test**:

| Endpoint | Method | Line | Auth mechanism | Response type |
|---|---|---|---|---|
| `/schedules` | GET | 50 | `is_authenticated()` | HTML (render_template) |
| `/schedules/create` | POST | 102 | `require_auth()` | JSON |
| `/schedules/<int:schedule_id>` | PUT | 198 | `require_auth()` | JSON |
| `/schedules/<int:schedule_id>` | DELETE | 278 | `require_auth()` | JSON |
| `/schedules/<int:schedule_id>/toggle` | POST | 319 | `require_auth()` | JSON |
| `/schedules/<int:schedule_id>/run` | POST | 387 | `require_auth()` | JSON |
| `/schedules/<int:schedule_id>/history` | GET | 428 | `require_auth()` | JSON |
| `/playlist/<playlist_id>/rotation-status` | GET | 448 | `require_auth()` | JSON |

**KEY OBSERVATION**: The `GET /schedules` route at line 50 uses `is_authenticated()` (not `require_auth()`), meaning unauthenticated access returns a **302 redirect** to `main.index`, not a 401 JSON. This is because it renders an HTML page, not an API response. The patch target for this route is `shuffify.routes.schedules.is_authenticated`.

Mocking requirements:
- `shuffify.routes.schedules.require_auth`
- `shuffify.routes.schedules.is_authenticated` (for `GET /schedules` only)
- `shuffify.routes.schedules.get_db_user`
- `shuffify.routes.schedules.SchedulerService`
- `shuffify.routes.schedules.JobExecutorService`
- `shuffify.routes.schedules.AuthService` (for `GET /schedules`)
- `shuffify.routes.schedules.PlaylistService` (for `GET /schedules`)
- `shuffify.routes.schedules.ShuffleService` (for `GET /schedules`)
- `shuffify.scheduler.add_job_for_schedule` and `shuffify.scheduler.remove_job_for_schedule` -- these are imported inside the function body via `from shuffify.scheduler import ...` so they need to be patched at `shuffify.scheduler.add_job_for_schedule`

**IMPORTANT DETAIL**: The `create_schedule` route (line 115) checks `db_user.encrypted_refresh_token`. If it is falsy, the route returns a 400 error telling the user to re-login. The test must set up the mock `db_user` with this attribute.

Test cases to include:

1. `TestSchedulesAuth` -- auth guards for all 8 endpoints
2. `TestGetSchedulesPage` -- renders HTML with authenticated session
3. `TestCreateSchedule` -- success with valid JSON, missing JSON body (400), missing `db_user` (401), missing encrypted_refresh_token (400), validation error from Pydantic
4. `TestUpdateSchedule` -- success, missing JSON (400), schedule not found (404 via global error handler)
5. `TestDeleteSchedule` -- success, schedule not found (404)
6. `TestToggleSchedule` -- success, toggled state text
7. `TestRunScheduleNow` -- success
8. `TestScheduleHistory` -- success returns history list
9. `TestRotationStatus` -- success with pair and without pair

Example key test (the `GET /schedules` page, which has a different auth pattern):

```python
class TestGetSchedulesPage:
    """Tests for GET /schedules."""

    def test_unauthenticated_redirects(self, db_app):
        """Unauthenticated users get redirected."""
        with db_app.test_client() as client:
            resp = client.get("/schedules")
            assert resp.status_code == 302

    @patch("shuffify.routes.schedules.ShuffleService")
    @patch("shuffify.routes.schedules.PlaylistService")
    @patch("shuffify.routes.schedules.get_db_user")
    @patch("shuffify.routes.schedules.AuthService")
    @patch("shuffify.routes.schedules.is_authenticated")
    def test_renders_page_for_authenticated_user(
        self,
        mock_is_auth,
        mock_auth_service,
        mock_get_db_user,
        mock_ps_class,
        mock_shuffle_svc,
        auth_client,
    ):
        mock_is_auth.return_value = True
        mock_client = MagicMock()
        mock_auth_service.get_authenticated_client.return_value = mock_client
        mock_auth_service.get_user_data.return_value = {
            "id": "user123",
            "display_name": "Test User",
        }

        mock_db_user = MagicMock()
        mock_db_user.id = 1
        mock_get_db_user.return_value = mock_db_user

        from shuffify.services.scheduler_service import SchedulerService
        with patch.object(
            SchedulerService, "get_user_schedules", return_value=[]
        ):
            mock_ps = MagicMock()
            mock_ps.get_user_playlists.return_value = []
            mock_ps_class.return_value = mock_ps
            mock_shuffle_svc.list_algorithms.return_value = []

            resp = auth_client.get("/schedules")
            assert resp.status_code == 200
```

Example key test (create schedule):

```python
class TestCreateSchedule:
    """Tests for POST /schedules/create."""

    @patch("shuffify.routes.schedules.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/schedules/create",
                json={"job_type": "shuffle"},
            )
            assert resp.status_code == 401

    @patch("shuffify.scheduler.add_job_for_schedule")
    @patch("shuffify.routes.schedules.SchedulerService")
    @patch("shuffify.routes.schedules.get_db_user")
    @patch("shuffify.routes.schedules.require_auth")
    def test_create_success(
        self,
        mock_auth,
        mock_get_db_user,
        mock_sched_svc,
        mock_add_job,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()
        mock_db_user = MagicMock()
        mock_db_user.id = 1
        mock_db_user.spotify_id = "user123"
        mock_db_user.encrypted_refresh_token = "encrypted_value"
        mock_get_db_user.return_value = mock_db_user

        mock_schedule = MagicMock()
        mock_schedule.id = 1
        mock_schedule.job_type = "shuffle"
        mock_schedule.target_playlist_name = "My Playlist"
        mock_schedule.target_playlist_id = "p1"
        mock_schedule.is_enabled = True
        mock_schedule.to_dict.return_value = {"id": 1}
        mock_sched_svc.create_schedule.return_value = mock_schedule

        resp = auth_client.post(
            "/schedules/create",
            json={
                "job_type": "shuffle",
                "target_playlist_id": "p1",
                "target_playlist_name": "My Playlist",
                "algorithm_name": "BasicShuffle",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("shuffify.routes.schedules.get_db_user")
    @patch("shuffify.routes.schedules.require_auth")
    def test_missing_refresh_token_returns_400(
        self, mock_auth, mock_get_db_user, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_db_user = MagicMock()
        mock_db_user.id = 1
        mock_db_user.encrypted_refresh_token = None
        mock_get_db_user.return_value = mock_db_user

        resp = auth_client.post(
            "/schedules/create",
            json={
                "job_type": "shuffle",
                "target_playlist_id": "p1",
                "target_playlist_name": "My Playlist",
                "algorithm_name": "BasicShuffle",
            },
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.schedules.get_db_user")
    @patch("shuffify.routes.schedules.require_auth")
    def test_no_json_body_returns_400(
        self, mock_auth, mock_get_db_user, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_db_user = MagicMock()
        mock_db_user.id = 1
        mock_db_user.encrypted_refresh_token = "enc"
        mock_get_db_user.return_value = mock_db_user

        resp = auth_client.post(
            "/schedules/create",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400
```

Total test count for this file: approximately **20-25 tests**.

---

### Test File 4: `tests/routes/test_upstream_sources_routes.py`

**Source file**: `/Users/chris/Projects/shuffify/shuffify/routes/upstream_sources.py` (180 lines, 3 endpoints)

**Endpoints to test**:

| Endpoint | Method | Line | Notes |
|---|---|---|---|
| `/playlist/<id>/upstream-sources` | GET | 23 | Lists sources; checks `is_db_available()` and `session["user_data"]["id"]` |
| `/playlist/<id>/upstream-sources` | POST | 52 | Adds source; validates JSON body, checks for `source_playlist_id` |
| `/upstream-sources/<int:source_id>` | DELETE | 130 | Deletes source; catches `UpstreamSourceNotFoundError` and `UpstreamSourceError` |

**KEY OBSERVATION**: These routes check `is_db_available()` at lines 33, 64, and 140. Unauthenticated requests that pass `require_auth()` but fail the `session["user_data"]` check return 401 (lines 38-41). The auth pattern here is two-layered: first `require_auth()`, then `session.get("user_data", {}).get("id")`.

Test cases to include:

```python
class TestListUpstreamSources:
    """Tests for GET /playlist/<id>/upstream-sources."""

    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/playlist/p1/upstream-sources")
            assert resp.status_code == 401

    @patch("shuffify.routes.upstream_sources.is_db_available")
    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_db_unavailable_returns_503(
        self, mock_auth, mock_db, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = False

        resp = auth_client.get("/playlist/p1/upstream-sources")
        assert resp.status_code == 503

    @patch("shuffify.routes.upstream_sources.UpstreamSourceService")
    @patch("shuffify.routes.upstream_sources.is_db_available")
    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_list_empty_sources(
        self, mock_auth, mock_db, mock_svc, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = True
        mock_svc.list_sources.return_value = []

        resp = auth_client.get("/playlist/p1/upstream-sources")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["sources"] == []


class TestAddUpstreamSource:
    """Tests for POST /playlist/<id>/upstream-sources."""

    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/playlist/p1/upstream-sources",
                json={"source_playlist_id": "s1"},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.upstream_sources.is_db_available")
    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_no_json_body_returns_400(
        self, mock_auth, mock_db, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = True

        resp = auth_client.post(
            "/playlist/p1/upstream-sources",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.upstream_sources.is_db_available")
    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_missing_source_playlist_id_returns_400(
        self, mock_auth, mock_db, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = True

        resp = auth_client.post(
            "/playlist/p1/upstream-sources",
            json={"source_name": "something"},
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.upstream_sources.UpstreamSourceService")
    @patch("shuffify.routes.upstream_sources.is_db_available")
    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_add_success(
        self, mock_auth, mock_db, mock_svc, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = True
        mock_source = MagicMock()
        mock_source.to_dict.return_value = {"id": 1}
        mock_svc.add_source.return_value = mock_source

        resp = auth_client.post(
            "/playlist/p1/upstream-sources",
            json={"source_playlist_id": "s1"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("shuffify.routes.upstream_sources.UpstreamSourceService")
    @patch("shuffify.routes.upstream_sources.is_db_available")
    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_duplicate_source_returns_400(
        self, mock_auth, mock_db, mock_svc, auth_client
    ):
        from shuffify.services import UpstreamSourceError
        mock_auth.return_value = MagicMock()
        mock_db.return_value = True
        mock_svc.add_source.side_effect = UpstreamSourceError("duplicate")

        resp = auth_client.post(
            "/playlist/p1/upstream-sources",
            json={"source_playlist_id": "s1"},
        )
        assert resp.status_code == 400


class TestDeleteUpstreamSource:
    """Tests for DELETE /upstream-sources/<int:source_id>."""

    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_unauth_returns_401(self, mock_auth, db_app):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete("/upstream-sources/1")
            assert resp.status_code == 401

    @patch("shuffify.routes.upstream_sources.UpstreamSourceService")
    @patch("shuffify.routes.upstream_sources.is_db_available")
    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_delete_success(
        self, mock_auth, mock_db, mock_svc, auth_client
    ):
        mock_auth.return_value = MagicMock()
        mock_db.return_value = True

        resp = auth_client.delete("/upstream-sources/1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("shuffify.routes.upstream_sources.UpstreamSourceService")
    @patch("shuffify.routes.upstream_sources.is_db_available")
    @patch("shuffify.routes.upstream_sources.require_auth")
    def test_delete_not_found_returns_404(
        self, mock_auth, mock_db, mock_svc, auth_client
    ):
        from shuffify.services import UpstreamSourceNotFoundError
        mock_auth.return_value = MagicMock()
        mock_db.return_value = True
        mock_svc.delete_source.side_effect = UpstreamSourceNotFoundError("gone")

        resp = auth_client.delete("/upstream-sources/99")
        assert resp.status_code == 404
```

Total test count for this file: approximately **12-14 tests**.

---

### Test File 5: `tests/routes/test_core_routes.py`

**Source file**: `/Users/chris/Projects/shuffify/shuffify/routes/core.py` (378 lines, 7 endpoints)

**Endpoints to test**:

| Endpoint | Method | Line | Auth mechanism | Notes |
|---|---|---|---|---|
| `/` | GET | 46 | `is_authenticated()` | Returns `index.html` (unauth) or `dashboard.html` (auth) |
| `/terms` | GET | 117 | None | Static file served from `static/public/terms.html` |
| `/privacy` | GET | 122 | None | Static file served from `static/public/privacy.html` |
| `/health` | GET | 129 | None | JSON health check. **Already tested in `test_health_db.py`** -- add cross-reference but no duplicates |
| `/login` | GET | 153 | None | Requires `legal_consent` query param. Redirects to Spotify auth URL |
| `/callback` | GET | 187 | None | Exchanges code for token. Complex flow with many side effects |
| `/logout` | GET | 335 | None | Clears session, redirects to index |

**IMPORTANT**: The `/health` endpoint already has **7 dedicated tests** in `/Users/chris/Projects/shuffify/tests/test_health_db.py`. Do NOT write duplicate tests. Add a comment in the test file referencing those existing tests.

Test cases:

```python
"""
Tests for core routes.

Tests cover /, /login, /callback, /logout, /terms, /privacy.
Health endpoint (/health) is tested in tests/test_health_db.py.
"""

import time
import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db
from shuffify.services.user_service import UserService


# db_app and auth_client fixtures (same pattern)


class TestIndexRoute:
    """Tests for GET /."""

    def test_unauthenticated_shows_login_page(self, db_app):
        """Unauthenticated users see the landing page."""
        with db_app.test_client() as client:
            resp = client.get("/")
            assert resp.status_code == 200
            # index.html should be rendered (check for a known element)
            assert b"Shuffify" in resp.data or resp.status_code == 200

    @patch("shuffify.routes.core.DashboardService")
    @patch("shuffify.routes.core.ShuffleService")
    @patch("shuffify.routes.core.PlaylistService")
    @patch("shuffify.routes.core.AuthService")
    @patch("shuffify.routes.core.is_authenticated")
    def test_authenticated_shows_dashboard(
        self,
        mock_is_auth,
        mock_auth_svc,
        mock_ps_class,
        mock_shuffle_svc,
        mock_dash_svc,
        auth_client,
    ):
        mock_is_auth.return_value = True
        mock_client = MagicMock()
        mock_auth_svc.get_authenticated_client.return_value = mock_client
        mock_auth_svc.get_user_data.return_value = {
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        }

        mock_ps = MagicMock()
        mock_ps.get_user_playlists.return_value = []
        mock_ps_class.return_value = mock_ps

        mock_shuffle_svc.list_algorithms.return_value = []
        mock_dash_svc.get_dashboard_data.return_value = {}

        resp = auth_client.get("/")
        assert resp.status_code == 200

    @patch("shuffify.routes.core.AuthService")
    @patch("shuffify.routes.core.is_authenticated")
    def test_expired_session_clears_and_shows_login(
        self,
        mock_is_auth,
        mock_auth_svc,
        auth_client,
    ):
        from shuffify.services import AuthenticationError
        mock_is_auth.return_value = True
        mock_auth_svc.get_authenticated_client.side_effect = (
            AuthenticationError("expired")
        )

        resp = auth_client.get("/")
        assert resp.status_code == 200
        # Should show login page after clearing session


class TestLoginRoute:
    """Tests for GET /login."""

    def test_missing_legal_consent_redirects(self, db_app):
        """Login without legal_consent flashes error and redirects."""
        with db_app.test_client() as client:
            resp = client.get("/login")
            assert resp.status_code == 302

    @patch("shuffify.routes.core.AuthService")
    def test_with_legal_consent_redirects_to_spotify(
        self, mock_auth_svc, db_app
    ):
        mock_auth_svc.get_auth_url.return_value = (
            "https://accounts.spotify.com/authorize?test=1"
        )
        with db_app.test_client() as client:
            resp = client.get("/login?legal_consent=true")
            assert resp.status_code == 302
            assert "accounts.spotify.com" in resp.headers["Location"]


class TestCallbackRoute:
    """Tests for GET /callback."""

    def test_oauth_error_redirects_with_flash(self, db_app):
        """OAuth error parameter should redirect to index."""
        with db_app.test_client() as client:
            resp = client.get(
                "/callback?error=access_denied"
                "&error_description=User+denied+access"
            )
            assert resp.status_code == 302

    def test_missing_code_redirects(self, db_app):
        """No authorization code should redirect to index."""
        with db_app.test_client() as client:
            resp = client.get("/callback")
            assert resp.status_code == 302

    @patch("shuffify.routes.core.UserService")
    @patch("shuffify.routes.core.AuthService")
    def test_successful_callback(
        self, mock_auth_svc, mock_user_svc, db_app
    ):
        mock_auth_svc.exchange_code_for_token.return_value = {
            "access_token": "new_token",
            "token_type": "Bearer",
            "expires_at": time.time() + 3600,
            "refresh_token": "new_refresh",
        }
        mock_client = MagicMock()
        mock_auth_svc.authenticate_and_get_user.return_value = (
            mock_client,
            {
                "id": "user123",
                "display_name": "Test User",
                "images": [],
            },
        )
        mock_upsert_result = MagicMock()
        mock_upsert_result.is_new = False
        mock_user_svc.upsert_from_spotify.return_value = mock_upsert_result
        mock_user_svc.get_by_spotify_id.return_value = None

        with db_app.test_client() as client:
            resp = client.get("/callback?code=test_auth_code")
            assert resp.status_code == 302
            # Should redirect to index (dashboard)
            assert resp.headers["Location"].endswith("/")


class TestLogoutRoute:
    """Tests for GET /logout."""

    def test_logout_clears_session_and_redirects(self, auth_client):
        """Logout should clear session and redirect to index."""
        resp = auth_client.get("/logout")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")

    def test_logout_when_not_logged_in(self, db_app):
        """Logout without session should still work."""
        with db_app.test_client() as client:
            resp = client.get("/logout")
            assert resp.status_code == 302


class TestTermsRoute:
    """Tests for GET /terms."""

    def test_terms_page_returns_200(self, db_app):
        with db_app.test_client() as client:
            resp = client.get("/terms")
            assert resp.status_code == 200


class TestPrivacyRoute:
    """Tests for GET /privacy."""

    def test_privacy_page_returns_200(self, db_app):
        with db_app.test_client() as client:
            resp = client.get("/privacy")
            assert resp.status_code == 200


# NOTE: /health endpoint tests are in tests/test_health_db.py (7 tests).
# No duplicate tests here.
```

Total test count for this file: approximately **12-14 tests**.

---

## 4. Step-by-Step Implementation Instructions

### Step 1: Create the 5 test files

Create the following files (in this order, simplest first):

1. `tests/routes/test_core_routes.py`
2. `tests/routes/test_playlists_routes.py`
3. `tests/routes/test_shuffle_routes.py`
4. `tests/routes/test_upstream_sources_routes.py`
5. `tests/routes/test_schedules_routes.py`

For each file, follow this exact structure:
1. Module docstring (3-line pattern matching existing files)
2. Imports (`time`, `pytest`, `unittest.mock`, `db`, `UserService`, and any route-specific imports)
3. `db_app` fixture (copy from pattern above -- identical in every file)
4. `auth_client` fixture (copy from pattern above -- identical in every file)
5. Test classes grouped by endpoint
6. Each class contains: auth guard test, success test, error/edge case tests

### Step 2: Run tests incrementally

After creating each file, run ONLY that file to verify it passes:

```bash
pytest tests/routes/test_core_routes.py -v
pytest tests/routes/test_playlists_routes.py -v
pytest tests/routes/test_shuffle_routes.py -v
pytest tests/routes/test_upstream_sources_routes.py -v
pytest tests/routes/test_schedules_routes.py -v
```

### Step 3: Run the full suite

After all 5 files pass individually:

```bash
pytest tests/ -v
```

Verify that the total count has increased (from current 1099 to approximately 1160-1170).

### Step 4: Run lint

```bash
flake8 tests/routes/
```

Fix any lint errors before committing.

### Step 5: Commit and push

```bash
git checkout -b test/missing-route-tests
git add tests/routes/test_core_routes.py tests/routes/test_playlists_routes.py tests/routes/test_shuffle_routes.py tests/routes/test_upstream_sources_routes.py tests/routes/test_schedules_routes.py
git commit -m "Test: Add missing route-level tests for 5 untested modules"
git push -u origin test/missing-route-tests
```

### Step 6: Create PR

Use the PR title and description from the metadata section.

---

## 5. Verification Checklist

After implementation, verify each item:

- [ ] `tests/routes/test_core_routes.py` exists and all tests pass
- [ ] `tests/routes/test_playlists_routes.py` exists and all tests pass
- [ ] `tests/routes/test_shuffle_routes.py` exists and all tests pass
- [ ] `tests/routes/test_upstream_sources_routes.py` exists and all tests pass
- [ ] `tests/routes/test_schedules_routes.py` exists and all tests pass
- [ ] Each file tests the auth guard for every endpoint in its module
- [ ] Each file tests at least one success path per endpoint
- [ ] Each file tests at least one error path per endpoint
- [ ] No `__init__.py` was created in `tests/routes/` (pytest discovers without it)
- [ ] `flake8 tests/routes/` returns 0 errors
- [ ] Full test suite `pytest tests/ -v` passes with no regressions
- [ ] Total test count increased by at least 60 tests
- [ ] No source code files were modified (test-only change)
- [ ] `/health` tests are NOT duplicated (only in `test_health_db.py`)
- [ ] All mock targets use the correct module path (e.g., `shuffify.routes.shuffle.require_auth`, NOT `shuffify.routes.require_auth`)

---

## 6. What NOT To Do

1. **Do NOT use the `app`, `client`, or `authenticated_client` fixtures from `conftest.py` for these tests.** Those fixtures do not create database users. Every route test file that needs DB access must define its own `db_app` and `auth_client` fixtures with `UserService.upsert_from_spotify(...)` inside `db_app`. If you use `conftest.py`'s `app` fixture, `get_db_user()` will return `None` and most tests will fail with 401.

2. **Do NOT create an `__init__.py` in `tests/routes/`.** Pytest discovers tests without it (confirmed by the existing `test_playlist_pairs_routes.py`). Adding one can cause import resolution issues.

3. **Do NOT mock at the wrong level.** Always patch at the import location in the route module, not at the definition location. For example, use `@patch("shuffify.routes.shuffle.PlaylistService")`, NOT `@patch("shuffify.services.playlist_service.PlaylistService")`. The former patches what the route module sees; the latter patches the original but the route's import still points to the unpatched version.

4. **Do NOT duplicate the `/health` tests.** There are already 7 tests in `test_health_db.py`. Add a comment in `test_core_routes.py` referencing them, but do not re-test the same behavior.

5. **Do NOT forget to mock `shuffify.scheduler.add_job_for_schedule` in schedule route tests.** The `create_schedule` and `update_schedule` routes import it inside the function body with `from shuffify.scheduler import add_job_for_schedule`. This means you need to patch it at `shuffify.scheduler.add_job_for_schedule`, not at the route module level. If you forget, the test will try to actually start APScheduler and fail.

6. **Do NOT test Pydantic validation exhaustively in route tests.** The schemas have their own dedicated test files in `tests/schemas/`. Route tests should verify that invalid input returns a 400, but they do not need to test every validation rule. One or two validation-failure tests per endpoint is sufficient.

7. **Do NOT mock `get_db_user` globally.** It is used internally by many routes and reads from `session["user_data"]`. For routes that call `get_db_user()`, either: (a) let it run against the real DB (the `db_app` fixture creates the user), or (b) patch it at the route module level (e.g., `shuffify.routes.schedules.get_db_user`). Option (a) is preferred because it tests the real integration; option (b) is needed when you want to simulate "user not found" scenarios.

8. **Do NOT use `follow_redirects=True` in tests for redirect endpoints** (like `/login`, `/callback`, `/logout`). Test the redirect status code (302) and the `Location` header instead. Following redirects makes the test depend on the target page's behavior, which is outside the scope of the route being tested.

9. **Do NOT forget `session["user_data"]` in the `auth_client` fixture.** Several routes (upstream_sources.py lines 37, 75, 143; schedules.py via `get_db_user()`) read `session["user_data"]["id"]` directly. If this key is missing, the route returns 401 even though `require_auth()` succeeds. The `auth_client` fixture MUST include both `spotify_token` and `user_data`.

10. **Do NOT modify any source files.** This phase is test-only. If you find a bug in a route while writing tests, document it in the PR description but do not fix it. Fixes go in a separate PR.

---

### Critical Files for Implementation

- `/Users/chris/Projects/shuffify/tests/test_snapshot_routes.py` - Primary pattern reference: shows the exact fixture structure, auth mocking, and JSON assertion patterns to replicate in all 5 new files
- `/Users/chris/Projects/shuffify/tests/routes/test_playlist_pairs_routes.py` - Subdirectory pattern reference: proves that tests in `tests/routes/` are discovered without `__init__.py` and shows the class-per-endpoint grouping convention
- `/Users/chris/Projects/shuffify/shuffify/routes/__init__.py` - Shared helpers: defines `require_auth()`, `is_authenticated()`, `json_error()`, `json_success()`, `get_db_user()` which are the primary functions to mock in route tests
- `/Users/chris/Projects/shuffify/shuffify/routes/schedules.py` - Most complex route module (8 endpoints, 497 lines): understanding the dual auth patterns (`is_authenticated` vs `require_auth`), the `get_db_user()` dependency, and the inline `from shuffify.scheduler import ...` pattern is critical for the largest test file
- `/Users/chris/Projects/shuffify/shuffify/error_handlers.py` - Global error handlers: determines which service exceptions produce which HTTP status codes (e.g., `PlaylistUpdateError` -> 500, `ScheduleNotFoundError` -> 404), essential for writing correct assertion expectations
