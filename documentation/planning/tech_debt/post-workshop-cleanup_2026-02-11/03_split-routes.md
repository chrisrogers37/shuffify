# Phase 03: Split routes.py into Feature-Based Blueprints

**PR Title:** `refactor: Split routes.py into feature-based Blueprint modules`
**Risk:** Medium (structural change to core routing file)
**Effort:** ~3 hours
**Files Changed:** routes.py → routes/ package, __init__.py

---

## Objective

Split the monolithic `shuffify/routes.py` (1509 lines, 35 routes) into a `shuffify/routes/` package with feature-based Blueprint modules. This improves navigability, reduces merge conflicts, and makes the codebase more approachable for new contributors.

---

## Current State

All 35 routes live in a single file with one Blueprint (`main`):

| Route Group | URL Pattern | Routes | Lines (approx) |
|-------------|-------------|--------|----------------|
| Core/Auth | `/`, `/login`, `/callback`, `/logout`, `/health`, `/terms`, `/privacy` | 7 | ~190 |
| Playlists | `/playlist/...`, `/api/user-playlists`, `/refresh-playlists` | 4 | ~100 |
| Shuffle | `/shuffle/...`, `/undo/...` | 2 | ~80 |
| Workshop | `/workshop/...` | 10 | ~590 |
| Upstream Sources | `/workshop/.../upstream-sources/...` | 3 | ~100 |
| Schedules | `/schedules/...` | 7 | ~300 |
| **Helpers** | `inject_current_year`, `is_authenticated`, `require_auth`, `json_error`, etc. | 7 funcs | ~70 |

Plus imports and module docstring: ~60 lines.

---

## Target Architecture

```
shuffify/routes/
├── __init__.py          # Blueprint registration, shared helpers
├── core.py              # /, /health, /terms, /privacy, /login, /callback, /logout
├── playlists.py         # /playlist/..., /api/user-playlists, /refresh-playlists
├── shuffle.py           # /shuffle/..., /undo/...
├── workshop.py          # /workshop/... (preview, commit, search, sessions)
├── upstream_sources.py  # /workshop/.../upstream-sources/...
└── schedules.py         # /schedules/...
```

### Blueprint Strategy: Single Blueprint, Multiple Modules

**Keep one `main` Blueprint** but split route definitions across files. This preserves all existing `url_for('main.xxx')` references in templates and redirects — zero template changes needed.

```python
# shuffify/routes/__init__.py
from flask import Blueprint

main = Blueprint("main", __name__)

# Shared helpers (used by all route modules)
# ... inject_current_year, is_authenticated, require_auth, etc.

# Import route modules to register their routes on the Blueprint
from shuffify.routes import (  # noqa: E402, F401
    core,
    playlists,
    shuffle,
    workshop,
    upstream_sources,
    schedules,
)
```

Each module imports `main` from the package and decorates routes:

```python
# shuffify/routes/core.py
from shuffify.routes import main

@main.route("/")
def index():
    ...
```

---

## Step-by-Step Implementation

### Step 1: Create `shuffify/routes/` directory

```bash
mkdir shuffify/routes
```

### Step 2: Create `shuffify/routes/__init__.py`

Move from `routes.py`:
- Blueprint creation (`main = Blueprint(...)`)
- Helper functions: `inject_current_year()`, `is_authenticated()`, `require_auth()`, `clear_session_and_show_login()`, `json_error()`, `json_success()`, `get_db_user()`
- Shared imports used by helpers

**The `@main.before_app_request` for `inject_current_year` stays here** since it applies globally.

### Step 3: Create route modules

#### `core.py` — 7 routes (~190 lines)

```
index()                     # GET /
terms()                     # GET /terms
privacy()                   # GET /privacy
health()                    # GET /health
login()                     # GET /login
callback()                  # GET /callback
logout()                    # GET /logout
```

Imports needed: `AuthService`, `UserService`, `is_db_available`

#### `playlists.py` — 4 routes (~100 lines)

```
refresh_playlists()         # POST /refresh-playlists
get_playlist(playlist_id)   # GET /playlist/<playlist_id>
get_playlist_stats(id)      # GET /playlist/<playlist_id>/stats
api_user_playlists()        # GET /api/user-playlists
```

Imports needed: `PlaylistService`, `PlaylistQueryParams`

#### `shuffle.py` — 2 routes (~80 lines)

```
shuffle(playlist_id)        # POST /shuffle/<playlist_id>
undo(playlist_id)           # POST /undo/<playlist_id>
```

Imports needed: `ShuffleService`, `StateService`, `PlaylistService`, `parse_shuffle_request`

#### `workshop.py` — 10 routes (~590 lines)

```
workshop(playlist_id)                    # GET /workshop/<playlist_id>
workshop_preview_shuffle(playlist_id)    # POST /workshop/<playlist_id>/preview-shuffle
workshop_commit(playlist_id)             # POST /workshop/<playlist_id>/commit
workshop_search()                        # POST /workshop/search
workshop_search_playlists()              # POST /workshop/search-playlists
workshop_load_external_playlist()        # POST /workshop/load-external-playlist
list_workshop_sessions(playlist_id)      # GET /workshop/<playlist_id>/sessions
save_workshop_session(playlist_id)       # POST /workshop/<playlist_id>/sessions
load_workshop_session(session_id)        # GET /workshop/sessions/<session_id>
update_workshop_session(session_id)      # PUT /workshop/sessions/<session_id>
delete_workshop_session(session_id)      # DELETE /workshop/sessions/<session_id>
```

Imports needed: `WorkshopSessionService`, `WorkshopCommitRequest`, `WorkshopSearchRequest`, `ExternalPlaylistRequest`, `parse_spotify_playlist_url`, `ShuffleService`, `PlaylistService`, `StateService`

#### `upstream_sources.py` — 3 routes (~100 lines)

```
list_upstream_sources(playlist_id)       # GET /workshop/<playlist_id>/upstream-sources
add_upstream_source(playlist_id)         # POST /workshop/<playlist_id>/upstream-sources
delete_upstream_source(source_id)        # DELETE /workshop/upstream-sources/<source_id>
```

Imports needed: `UpstreamSourceService`, `UpstreamSourceError`

#### `schedules.py` — 7 routes (~300 lines)

```
schedules()                              # GET /schedules
create_schedule()                        # POST /schedules/create
update_schedule(schedule_id)             # PUT /schedules/<id>
delete_schedule(schedule_id)             # DELETE /schedules/<id>
toggle_schedule(schedule_id)             # POST /schedules/<id>/toggle
run_schedule_now(schedule_id)            # POST /schedules/<id>/run-now
schedule_history(schedule_id)            # GET /schedules/<id>/history
```

Imports needed: `SchedulerService`, `JobExecutorService`, `ScheduleCreateRequest`, `ScheduleUpdateRequest`

### Step 4: Update `shuffify/__init__.py`

Change the Blueprint import:

**Before (line 206):**
```python
from shuffify.routes import main as main_blueprint
```

**After:**
```python
from shuffify.routes import main as main_blueprint
```

This import path stays the same because `routes/` is now a package with `main` in its `__init__.py`. **No change needed.**

### Step 5: Delete the old `shuffify/routes.py`

After all routes are migrated and tests pass, delete the original monolithic file.

### Step 6: Run tests

```bash
pytest tests/ -v
flake8 shuffify/
```

---

## Key Decisions

### Why one Blueprint, not six?

Multiple Blueprints would require updating every `url_for('main.xxx')` call in templates and routes to `url_for('core.xxx')`, `url_for('workshop.xxx')`, etc. This is high risk with no benefit for an app of this size. A single Blueprint split across modules gives us the file organization benefits without the URL-refactoring blast radius.

### Why not move templates too?

Blueprint-specific template folders add complexity. All templates stay in `shuffify/templates/` — they already work and the benefit of per-blueprint templates is negligible at this scale.

### Helper function placement

Shared helpers (`require_auth`, `json_error`, `json_success`, etc.) live in `routes/__init__.py` and are imported by each module. This avoids circular imports and keeps the pattern simple.

---

## Migration Verification

### Ensure no route is lost

```bash
# Before migration: count routes
grep -c "@main.route" shuffify/routes.py
# Expected: 35

# After migration: count routes across all modules
grep -rc "@main.route" shuffify/routes/ | awk -F: '{sum+=$2} END {print sum}'
# Expected: 35
```

### Ensure no `url_for` breaks

```bash
# List all url_for references in templates and routes
grep -rn "url_for" shuffify/templates/ shuffify/routes/ | grep "main\." | wc -l
# All should reference 'main.xxx' — no broken references
```

### Ensure no import breaks

```bash
# Verify the package imports correctly
python -c "from shuffify.routes import main; print(f'Blueprint: {main.name}, routes: {len(main.deferred_functions)}')"
```

---

## Verification Checklist

```bash
# 1. Run full test suite
pytest tests/ -v

# 2. Lint
flake8 shuffify/

# 3. Count routes (must be 35)
grep -rc "@main.route" shuffify/routes/ | awk -F: '{sum+=$2} END {print sum}'

# 4. Verify old routes.py is gone
test ! -f shuffify/routes.py && echo "Old routes.py removed" || echo "ERROR: old routes.py still exists"

# 5. Start dev server and smoke test
python run.py &
sleep 3
curl -s http://localhost:8000/health | python -m json.tool
kill %1
```

**Expected outcome:** All 690+ tests pass (plus any new from Phase 01), flake8 clean, 35 routes registered, dev server starts and health check passes.

---

## Dependencies

- **Blocks:** None
- **Blocked by:** Phase 02 (enum extraction touches some of the same files — merge Phase 02 first to avoid conflicts)
- **Safe to run in parallel with:** Phase 01 (tests only), Phase 04 (requirements only)

---

## Rollback Plan

If something goes wrong mid-migration:

```bash
# The old routes.py is in git history
git checkout HEAD -- shuffify/routes.py
rm -rf shuffify/routes/
```

The single-Blueprint approach means `__init__.py` doesn't need to change, so rollback is straightforward.

---

*Generated by /techdebt scan on 2026-02-11*
