# Phase 06: Service & Code Cleanup

**Status:** ✅ COMPLETE
**Started:** 2026-02-23
**Completed:** 2026-02-23

## Header

| Field | Value |
|-------|-------|
| **PR Title** | Cleanup: adopt ownership helper, consolidate imports, fix unused vars, reorganize tests |
| **Risk Level** | Low |
| **Effort** | Low (~1 hour) |
| **Files Modified** | 4 |
| **Files Moved** | 7 |
| **Files Created** | 0 |
| **Files Deleted** | 0 |

---

## Context

This phase addresses four categories of minor code hygiene issues discovered during the tech debt audit:

1. **Ownership helper underutilization** -- `get_owned_entity()` in `shuffify/services/base.py` was introduced to standardize entity-fetch-plus-ownership-check across services, but `scheduler_service.py` still uses a manual query pattern. Adopting the helper improves consistency and reduces code duplication.

2. **Duplicate inline imports** -- `scheduler_service.py` imports `JobExecution` from `shuffify.models.db` in two separate methods via inline imports. Since `JobExecution` lives in the same `db.py` module as `Schedule` (which is already imported at module level), there is no circular dependency risk. Moving the import to module level eliminates duplication.

3. **Unused variables** -- `flake8` flags two unused variable patterns in production code. Replacing them with `_` communicates intent clearly and silences linter warnings.

4. **Test file misplacement** -- Seven route-level test files live in `tests/` root instead of `tests/routes/` where the other six route test files already reside. Moving them improves discoverability and organizational consistency.

---

## Dependencies

| Dependency | Reason |
|------------|--------|
| **Phase 01** (DB commit safety) | Phase 01 modifies `scheduler_service.py` (refactoring commit patterns). Must merge first to avoid conflicts. |
| **Phase 03** (Route auth/validation) | Phase 03 modifies `routes/core.py`. Must merge first to avoid conflicts on the same file. |
| **Blocks** | Nothing. This is a leaf phase. |

---

## Detailed Implementation Plan

### Part A: Adopt Ownership Helper in `scheduler_service.py`

**File**: `shuffify/services/scheduler_service.py`

**Why**: The `get_schedule()` method manually queries `Schedule.query.filter_by(id=schedule_id, user_id=user_id).first()` and checks for `None`. The `get_owned_entity()` helper in `shuffify/services/base.py` does exactly this in a standardized way: it calls `db.session.get(entity_class, entity_id)`, checks `entity.user_id != user_id`, and raises the specified exception. Two other services (`upstream_source_service.py`, `workshop_session_service.py`) already use this helper.

**Step 1**: Add the `get_owned_entity` import to the module-level imports.

**Before** (lines 12-13):
```python
from shuffify.models.db import db, Schedule
from shuffify.services.base import safe_commit
```

**After**:
```python
from shuffify.models.db import db, Schedule
from shuffify.services.base import safe_commit, get_owned_entity
```

**Step 2**: Replace the body of `get_schedule()`.

**Before** (lines 55-75):
```python
    @staticmethod
    def get_schedule(
        schedule_id: int, user_id: int
    ) -> Schedule:
        """
        Get a single schedule, verifying ownership.

        Raises:
            ScheduleNotFoundError: If not found or wrong user.
        """
        schedule = Schedule.query.filter_by(
            id=schedule_id, user_id=user_id
        ).first()

        if not schedule:
            raise ScheduleNotFoundError(
                f"Schedule {schedule_id} not found "
                f"for user {user_id}"
            )

        return schedule
```

**After**:
```python
    @staticmethod
    def get_schedule(
        schedule_id: int, user_id: int
    ) -> Schedule:
        """
        Get a single schedule, verifying ownership.

        Raises:
            ScheduleNotFoundError: If not found or wrong user.
        """
        return get_owned_entity(
            Schedule, schedule_id, user_id, ScheduleNotFoundError
        )
```

**Behavioral note**: The error message format changes slightly. The old code produced `"Schedule {id} not found for user {user_id}"`. The helper produces `"Schedule {id} not found"` (from `base.py:113`). This is acceptable because the error message is internal (logged or raised to the route layer) and never shown to end users.

---

### Part B: Consolidate Duplicate `JobExecution` Imports

**File**: `shuffify/services/scheduler_service.py`

**Why**: `JobExecution` is imported inline at two separate locations (lines 212 and 274). Both are `from shuffify.models.db import JobExecution`. Since `Schedule` is already imported at module level from the same `db.py` module, there is zero circular dependency risk. A single module-level import is cleaner.

**Step 1**: Add `JobExecution` to the existing module-level import.

**Before** (lines 12-13, after Part A):
```python
from shuffify.models.db import db, Schedule
from shuffify.services.base import safe_commit, get_owned_entity
```

**After** (incorporating Part A's change as well):
```python
from shuffify.models.db import db, Schedule, JobExecution
from shuffify.services.base import safe_commit, get_owned_entity
```

**Step 2**: Remove the inline import on line 187 (inside `delete_schedule()`).

**Before** (lines 186-187):
```python

        from shuffify.models.db import JobExecution
```

**After**:
```python
        try:
```

(The `JobExecution` reference on line 189 continues to work because it is now resolved by the module-level import.)

**Step 3**: Remove the inline import on line 228 (inside `get_execution_history()`).

**Before** (lines 227-228):
```python

        from shuffify.models.db import JobExecution
```

**After**:

(Delete the blank line and the import line entirely. The `JobExecution` reference on line 230+ continues to work via the module-level import.)

---

### Part C: Fix Unused Variables

#### C1: `routes/core.py` line 224

**File**: `shuffify/routes/core.py`

**Why**: The `client` variable from `AuthService.authenticate_and_get_user()` is never used after assignment on line 224. The route only needs `user_data`. Replacing `client` with `_` signals intentional discard.

**Before** (line 224):
```python
        client, user_data = (
            AuthService.authenticate_and_get_user(token_data)
        )
```

**After**:
```python
        _, user_data = (
            AuthService.authenticate_and_get_user(token_data)
        )
```

#### C2: `shuffle_algorithms/artist_spacing.py` lines 119 and 121

**File**: `shuffify/shuffle_algorithms/artist_spacing.py`

**Why**: The `tiebreaker` value extracted from heap entries is a random float used only for heap ordering. After popping, it is never read. Replacing it with `_` signals intentional discard.

**Before** (line 119):
```python
                neg_count, tiebreaker, artist = entry
```

**After**:
```python
                neg_count, _, artist = entry
```

**Before** (line 121):
```python
                neg_count, tiebreaker, artist = heapq.heappop(heap)
```

**After**:
```python
                neg_count, _, artist = heapq.heappop(heap)
```

---

### Part D: Reorganize Test Files

**Why**: Six route test files already live in `tests/routes/`. Seven others are still in `tests/` root. Moving them into `tests/routes/` with consistent naming improves organization and discoverability.

**Important**: Use `git mv` (not `mv`) to preserve git history.

All seven moves:

| # | Source (tests/) | Destination (tests/routes/) |
|---|-----------------|----------------------------|
| 1 | `test_settings_route.py` | `test_settings_routes.py` |
| 2 | `test_snapshot_routes.py` | `test_snapshot_routes.py` |
| 3 | `test_raid_panel_routes.py` | `test_raid_panel_routes.py` |
| 4 | `test_workshop.py` | `test_workshop_routes.py` |
| 5 | `test_workshop_external.py` | `test_workshop_external_routes.py` |
| 6 | `test_workshop_merge.py` | `test_workshop_merge_routes.py` |
| 7 | `test_workshop_search.py` | `test_workshop_search_routes.py` |

**Commands** (run from repo root):

```bash
git mv tests/test_settings_route.py tests/routes/test_settings_routes.py
git mv tests/test_snapshot_routes.py tests/routes/test_snapshot_routes.py
git mv tests/test_raid_panel_routes.py tests/routes/test_raid_panel_routes.py
git mv tests/test_workshop.py tests/routes/test_workshop_routes.py
git mv tests/test_workshop_external.py tests/routes/test_workshop_external_routes.py
git mv tests/test_workshop_merge.py tests/routes/test_workshop_merge_routes.py
git mv tests/test_workshop_search.py tests/routes/test_workshop_search_routes.py
```

**Why no import changes are needed**: All seven test files import from `shuffify.*` (application code), not from sibling test files. Fixtures come from `tests/conftest.py` which is the parent directory's conftest -- pytest discovers it automatically for all subdirectories. The existing `tests/routes/` test files already work this way (there is no `tests/routes/__init__.py` or `tests/routes/conftest.py`).

**Why pytest will still discover them**: The `pytest.ini` configuration sets `testpaths = tests` and `python_files = test_*.py`. Pytest recursively searches all subdirectories under `tests/`, so files in `tests/routes/` are discovered automatically.

**Naming convention note**: File #1 is renamed from `test_settings_route.py` (singular) to `test_settings_routes.py` (plural) to match the convention used by all other route test files (`test_*_routes.py`). Files #4-7 gain a `_routes` suffix for the same reason.

---

## Test Plan

### No New Tests Required

All changes in this phase are refactorings that preserve existing behavior:
- Part A: `get_owned_entity()` performs the same query + ownership check
- Part B: Import location change only -- runtime behavior identical
- Part C: Unused variable cleanup -- no logic change
- Part D: File moves -- same tests, different location

### Existing Tests to Verify

Run the full test suite to confirm nothing breaks:

```bash
pytest tests/ -v
```

**Specific areas to watch**:
- `tests/services/test_scheduler_service.py` -- validates Part A (ownership check) and Part B (JobExecution operations still work)
- `tests/routes/test_settings_routes.py` (new location) -- validates Part D move
- `tests/routes/test_snapshot_routes.py` (new location) -- validates Part D move
- `tests/routes/test_raid_panel_routes.py` (new location) -- validates Part D move
- `tests/routes/test_workshop_routes.py` (new location) -- validates Part D move
- `tests/routes/test_workshop_external_routes.py` (new location) -- validates Part D move
- `tests/routes/test_workshop_merge_routes.py` (new location) -- validates Part D move
- `tests/routes/test_workshop_search_routes.py` (new location) -- validates Part D move
- `tests/algorithms/test_artist_spacing.py` -- validates Part C (artist_spacing.py change)

### Manual Verification

After running the test suite, verify:

1. **Test count is unchanged** -- compare test count before and after. Should be the same number (currently 1230).
2. **No tests collected from old locations** -- run `pytest tests/test_workshop.py` and confirm it errors with "no such file" (not silently passes with 0 tests).
3. **Lint passes** -- run `flake8 shuffify/` and confirm zero errors (the unused variable warnings from Part C should be gone).

---

## Documentation Updates

### CHANGELOG.md

Add under `## [Unreleased]`:

```markdown
### Changed
- **Scheduler Service** - Adopted `get_owned_entity()` helper for ownership verification, consolidated duplicate `JobExecution` imports to module level
- **Test Organization** - Moved 7 route test files from `tests/` root to `tests/routes/` for consistent organization

### Fixed
- **Unused Variables** - Replaced unused `client` in `routes/core.py` and unused `tiebreaker` in `artist_spacing.py` with `_`
```

### CLAUDE.md

Update the test structure section to reflect the new file locations. In the "Testing Standards" section, the test structure tree should no longer list `test_settings_route.py`, `test_snapshot_routes.py`, `test_raid_panel_routes.py`, or `test_workshop*.py` under `tests/` root. Instead, note that all route tests are in `tests/routes/`.

---

## Stress Testing & Edge Cases

### Part A: `get_owned_entity` Behavioral Difference

The helper uses `db.session.get(Schedule, schedule_id)` (primary key lookup) instead of `Schedule.query.filter_by(id=schedule_id, user_id=user_id).first()` (compound filter). Both return the same result for valid IDs. The key difference:

- **Old code**: Returns `None` if either the ID doesn't exist or the user_id doesn't match (single query).
- **New code**: Fetches by PK first, then checks `user_id` in Python (potentially two-step, but `db.session.get()` uses the identity map so it may not even hit the DB if the entity is already loaded).

Both paths raise `ScheduleNotFoundError` for the same inputs. The error message text differs slightly (see Part A behavioral note), but this is never user-facing.

### Part D: Test Discovery Edge Case

If any test file in `tests/` root has the same name as a file in `tests/routes/`, pytest could get confused. Verify there are no naming collisions:

- `test_snapshot_routes.py` exists only in `tests/` root (will move to `tests/routes/`) -- no collision with existing `tests/routes/` files.
- `test_raid_panel_routes.py` -- same, no collision.
- The four workshop files get `_routes` suffix -- no collision with existing files.
- `test_settings_route.py` becomes `test_settings_routes.py` -- no collision.

All confirmed: no naming collisions.

---

## Verification Checklist

1. [ ] Run `flake8 shuffify/` -- zero errors (unused variable warnings gone)
2. [ ] Run `pytest tests/ -v` -- all tests pass, count unchanged (1220)
3. [ ] Verify `tests/routes/` contains 13 test files (6 existing + 7 moved)
4. [ ] Verify `tests/` root no longer contains `test_settings_route.py`, `test_snapshot_routes.py`, `test_raid_panel_routes.py`, `test_workshop.py`, `test_workshop_external.py`, `test_workshop_merge.py`, `test_workshop_search.py`
5. [ ] Verify `git log --follow tests/routes/test_workshop_routes.py` shows history from before the move
6. [ ] CHANGELOG.md updated with entries under `## [Unreleased]`

---

## What NOT To Do

1. **Do NOT use `mv` instead of `git mv`** for test file moves. Plain `mv` breaks git history tracking. Always use `git mv` so `git log --follow` can trace the file back to its original location.

2. **Do NOT add `__init__.py` to `tests/routes/`**. The existing route tests in that directory work without one. Adding it can interfere with pytest's rootdir-based test discovery in some configurations.

3. **Do NOT change the error message in `ScheduleNotFoundError`** to match the old format. The helper's generic format (`"Schedule {id} not found"`) is consistent with how `upstream_source_service` and `workshop_session_service` use it. Diverging would defeat the purpose of standardization.

4. **Do NOT move `conftest.py` or create a `tests/routes/conftest.py`**. The parent `tests/conftest.py` provides all shared fixtures and is automatically discovered by pytest for all subdirectories.

5. **Do NOT reorder the import lines** beyond what is specified. Part A and Part B both modify the import block at the top of `scheduler_service.py`. The final import block should be:
   ```python
   from shuffify.models.db import db, Schedule, JobExecution
   from shuffify.services.base import safe_commit, get_owned_entity
   ```
   Keep `shuffify.models.db` before `shuffify.services.base` (standard alphabetical/hierarchical ordering).

6. **Do NOT rename test classes or test methods** inside the moved files. Only the file names change; internal content stays identical.
