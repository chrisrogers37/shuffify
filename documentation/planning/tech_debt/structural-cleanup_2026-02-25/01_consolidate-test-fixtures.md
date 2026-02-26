# Phase 01: Consolidate Test Fixtures into conftest.py

`✅ COMPLETE` Started: 2026-02-26 | Completed: 2026-02-26 | PR: #TBD

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `refactor: Consolidate duplicated db_app/auth_client test fixtures into conftest.py` |
| **Risk Level** | Low |
| **Estimated Effort** | Low (1-2 hours) |
| **Dependencies** | None |
| **Blocks** | Nothing |

### Files Modified

| File | Change Type |
|------|-------------|
| `tests/conftest.py` | Add `db_app` and `auth_client` fixtures |
| 25 test files (see list below) | Remove local `db_app` / `auth_client` / `app_ctx` fixture definitions |

---

## Problem

The `db_app` fixture is copy-pasted across **25 test files**, and `auth_client` across **12 route test files**. Each definition is ~25-45 lines. This is ~580+ lines of pure duplication.

### Files with duplicated `db_app` fixture (23 files)

**Route tests (12 files):**
- `tests/routes/test_core_routes.py:17`
- `tests/routes/test_playlists_routes.py:17`
- `tests/routes/test_shuffle_routes.py:16`
- `tests/routes/test_schedules_routes.py:17`
- `tests/routes/test_settings_routes.py:14`
- `tests/routes/test_snapshot_routes.py:20`
- `tests/routes/test_upstream_sources_routes.py:17`
- `tests/routes/test_playlist_pairs_routes.py:17`
- `tests/routes/test_raid_panel_routes.py:17`
- `tests/routes/test_playlist_preferences_routes.py:17`
- `tests/routes/test_error_page_rendering.py:21`
- `tests/templates/test_dashboard_overlay.py:130`

**Service tests (11 files):**
- `tests/services/test_playlist_pair_service.py:21`
- `tests/services/test_playlist_snapshot_service.py:22`
- `tests/services/test_base.py:34`
- `tests/services/test_activity_log_service.py:20`
- `tests/services/test_upstream_source_service.py:19`
- `tests/services/test_user_settings_service.py:19`
- `tests/services/test_raid_sync_service.py:29`
- `tests/services/test_workshop_session_service.py:21`
- `tests/services/test_user_service.py:19`
- `tests/services/test_login_history_service.py:21`
- `tests/services/test_playlist_preference_service.py:22`

**Model tests (2 files):**
- `tests/models/test_playlist_preference_model.py:15`
- `tests/models/test_db_models.py:18`

### Canonical `db_app` fixture pattern (copied across all files)

```python
@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite."""
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

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
```

### Variations

Most files use the identical pattern above. Minor variations found:

1. **`test_schedules_routes.py`, `test_raid_panel_routes.py`, `test_playlist_preferences_routes.py`**: Add `app.config["SCHEDULER_ENABLED"] = False` — this should be included in the shared fixture since it's harmless for non-scheduler tests.

2. **`test_core_routes.py`**: Adds `UserService.upsert_from_spotify()` call inside `db_app` — this is test-specific setup and should remain local (but as a separate fixture that depends on `db_app`, not by duplicating `db_app`).

3. **`test_settings_routes.py`**: Has a `test_user` fixture and passes it to `auth_client` — slight auth_client variation.

---

## Step-by-Step Implementation

### Step 1: Add `db_app` fixture to `tests/conftest.py`

Add the shared fixture at the bottom of `tests/conftest.py`, after the existing `# Flask App Fixtures` section.

```python
# =============================================================================
# Database App Fixtures (shared across route/service/model tests)
# =============================================================================

@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for DB-dependent tests.

    This fixture creates a fresh database for each test, ensuring
    complete isolation. Use this instead of the lighter `app` fixture
    when tests need to write to the database.
    """
    import os
    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"
    os.environ.pop("DATABASE_URL", None)

    from shuffify import create_app
    from shuffify.models.db import db

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True
    app.config["SCHEDULER_ENABLED"] = False

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def app_ctx(db_app):
    """Provide app context for service/model tests."""
    with db_app.app_context():
        yield


@pytest.fixture
def auth_client(db_app):
    """Authenticated test client with valid session token.

    Includes a pre-seeded user in the database matching the session
    user_data. Use for route tests that require authentication.
    """
    from shuffify.services.user_service import UserService

    with db_app.app_context():
        UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })

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

**Note**: The `auth_client` fixture includes `UserService.upsert_from_spotify()` because most route tests need a DB user matching the session. Files that previously called this inside `db_app` (like `test_core_routes.py`) can now simply use `auth_client` directly.

### Step 2: Remove local `db_app` definitions from all 23 files

For each file, delete the local `db_app` fixture function entirely. pytest will automatically discover the shared version from `conftest.py`.

**For route test files** that also define `auth_client`: delete both `db_app` and `auth_client`.

**For service/model test files** that also define `app_ctx`: delete both `db_app` and `app_ctx`.

**Important**: Keep any file-specific fixtures that depend on `db_app` but add unique setup (e.g., `test_user`, `other_user`, specialized data seeding).

### Step 3: Handle the `test_settings_routes.py` variation

`test_settings_routes.py` has a slightly different `auth_client` that takes a `test_user` parameter. This file should keep a **local override** of `auth_client`:

```python
@pytest.fixture
def auth_client(db_app, test_user):
    """Authenticated client with pre-created test_user."""
    # ... file-specific version that differs from shared
```

pytest's fixture scoping rules mean the local fixture takes precedence over conftest.

### Step 4: Handle `test_core_routes.py` user seeding

`test_core_routes.py` currently calls `UserService.upsert_from_spotify()` inside its `db_app`. Since the shared `auth_client` now does this, the `auth_client` tests just work. For tests that use `db_app` directly (without `auth_client`), the user seeding should be moved to a local `test_user` fixture.

### Step 5: Verify imports

After removing local fixtures, check each file still has the imports it needs. Some files import `db` only for the fixture — those imports can be removed. Run flake8 to catch unused imports.

---

## Verification Checklist

```bash
# 1. Lint
./venv/bin/python -m flake8 tests/

# 2. Full test suite — all 1296 tests must pass
./venv/bin/python -m pytest tests/ -v

# 3. Verify no local db_app definitions remain (except conftest.py)
grep -rn "def db_app" tests/ --include="*.py" | grep -v conftest.py
# Should return empty

# 4. Verify no local auth_client definitions remain (except conftest.py and settings)
grep -rn "def auth_client" tests/ --include="*.py" | grep -v conftest.py
# Should only show test_settings_routes.py (legitimate override)
```

---

## What NOT To Do

1. **Do NOT merge the existing `app` fixture with `db_app`.** They serve different purposes: `app` is lighter (no drop/create cycle per test), while `db_app` provides full isolation. Some test files use `app`, others use `db_app`. Keep both.

2. **Do NOT change test file imports or test logic.** Only remove fixture definitions. Every test assertion should remain byte-for-byte identical.

3. **Do NOT change the `db_app` behavior.** The shared version must behave identically to the copies. The only addition is `SCHEDULER_ENABLED = False` (which is already present in some copies and harmless for all).

4. **Do NOT try to consolidate `test_user`, `other_user`, or other data-seeding fixtures.** These vary per file and are legitimately different. Only `db_app`, `app_ctx`, and `auth_client` are consolidation targets.

5. **Do NOT use session-scoped fixtures.** Each test needs a fresh database to ensure isolation. The `db_app` fixture must remain function-scoped (default).
