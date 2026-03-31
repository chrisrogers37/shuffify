# Phase 03: Extract Core Route Helpers (OAuth callback, index)

`📋 PENDING`

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `refactor: Extract callback and index route helpers for readability` |
| **Risk Level** | Medium |
| **Estimated Effort** | Medium (3-4 hours) |
| **Dependencies** | Phase 02 (Decompose Monster Functions) |
| **Blocks** | Nothing |

### Files Modified

| File | Change Type |
|------|-------------|
| `shuffify/routes/core.py` | Extract helper functions, slim down `callback()` and `index()` |
| `tests/routes/test_core_routes.py` | Add unit tests for new helpers |

---

## Problem

The OAuth `callback()` function at `shuffify/routes/core.py` (lines 206-351) is 145 lines with 7+ nesting levels. It performs five distinct responsibilities:

1. OAuth error checking (lines 213-233)
2. Token exchange and user validation (lines 237-246)
3. User upsert and refresh token storage (lines 248-293)
4. Login event recording (lines 295-315)
5. Activity logging (lines 319-333)

A key inefficiency: `UserService.get_by_spotify_id(user_data["id"])` is called **three separate times** (lines 272, 297, 321) within the same request.

The `index()` function (lines 49-133) is 84 lines with nested try/except blocks mixing Spotify API calls, database queries, and template context construction.

---

## Step-by-Step Implementation

### Step 1: Extract `_store_encrypted_refresh_token(db_user, token_data)`

Extract the refresh token encryption block (lines 264-293) into a standalone helper. Accepts `db_user` as parameter (eliminates redundant DB lookup). Non-blocking: failures logged as warnings.

### Step 2: Extract `_record_login_event(db_user)`

Extract the login history recording block (lines 295-315). Accepts `db_user` as parameter. Non-blocking.

### Step 3: Extract `_setup_user_session(token_data, user_data)`

Orchestrator that consolidates user upsert, token storage, login recording, and activity logging. Performs a **single** `get_by_spotify_id` lookup (down from 3). Returns `is_new_user` bool.

```python
def _setup_user_session(token_data, user_data):
    """Post-auth session setup: upsert, store token, record login, log activity."""
    is_new_user = False
    try:
        result = UserService.upsert_from_spotify(user_data)
        is_new_user = result.is_new
    except Exception as e:
        logger.warning("Failed to upsert user: %s", e)

    db_user = None
    try:
        db_user = UserService.get_by_spotify_id(user_data["id"])
    except Exception as e:
        logger.warning("Failed to look up user: %s", e)

    _store_encrypted_refresh_token(db_user, token_data)
    _record_login_event(db_user)

    if db_user:
        log_activity(
            user_id=db_user.id,
            activity_type=ActivityType.LOGIN,
            description="Logged in via Spotify OAuth",
        )

    return is_new_user
```

### Step 4: Rewrite `callback()` as thin orchestrator (~55 lines, down from 145)

```python
@main.route("/callback")
def callback():
    """Handle OAuth callback from Spotify."""
    error = request.args.get("error")
    if error:
        logger.error("OAuth error: %s", error)
        flash(f"OAuth Error: {request.args.get('error_description', 'Unknown error')}", "error")
        return redirect(url_for("main.index"))

    code = request.args.get("code")
    if not code:
        flash("No authorization code received.", "error")
        return redirect(url_for("main.index"))

    try:
        token_data = AuthService.exchange_code_for_token(code)
        session["spotify_token"] = token_data
        _, user_data = AuthService.authenticate_and_get_user(token_data)
        session["user_data"] = user_data

        is_new_user = _setup_user_session(token_data, user_data)
        session["is_new_user"] = is_new_user
        session.modified = True
        return redirect(url_for("main.index"))

    except AuthenticationError as e:
        logger.error("Authentication failed: %s", e)
        session.pop("spotify_token", None)
        session.pop("user_data", None)
        flash("Error connecting to Spotify.", "error")
        return redirect(url_for("main.index"))
```

### Step 5: Extract `_load_dashboard_context(token_data)` for `index()`

Returns dict with keys: `playlists`, `hidden_playlists`, `user`, `algorithms`, `dashboard`, `preferences`. Fetches playlists, algorithms, dashboard data, and preferences. Dashboard/preferences are non-blocking.

### Step 6: Rewrite `index()` as thin orchestrator (~25 lines, down from 84)

### Step 7: Add tests for new helpers

Add `TestSetupUserSession` and `TestLoadDashboardContext` test classes. Verify:
- `_setup_user_session` calls `get_by_spotify_id` exactly once (consolidation check)
- DB failure during upsert does not raise
- `_load_dashboard_context` returns expected context dict

---

## Verification Checklist

```bash
# 1. Lint
./venv/bin/python -m flake8 shuffify/routes/core.py

# 2. Core route tests
./venv/bin/python -m pytest tests/routes/test_core_routes.py -v

# 3. Full test suite
./venv/bin/python -m pytest tests/ -v

# 4. Manual smoke test
# - Visit http://localhost:8000/ (landing page)
# - Login via Spotify OAuth
# - Verify dashboard loads with playlists
# - Logout and login again
```

---

## What NOT To Do

1. **Do NOT move helpers to a separate file.** Keep in `core.py` as `_`-prefixed private functions, matching the pattern in `workshop.py` (`_load_playlist_by_url`).
2. **Do NOT change the public API or HTTP behavior.** Same responses, same session keys, same redirects.
3. **Do NOT change the `logout()` function.** It's only 43 lines — acceptable as-is.
4. **Do NOT add helpers to `shuffify/routes/__init__.py`.** These are private to `core.py`.
5. **Do NOT change error handling semantics.** Token encryption failure, login history failure, and dashboard data failure must all remain non-blocking (caught and logged as warnings).
6. **Do NOT refactor `login()`.** Already clean at 28 lines.
