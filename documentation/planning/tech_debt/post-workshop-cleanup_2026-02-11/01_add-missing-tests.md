# Phase 01: Add Missing Test Coverage

**Status:** ✅ COMPLETE
**Started:** 2026-02-11
**Completed:** 2026-02-11

**PR Title:** `test: Add coverage for schedule schemas, scheduler, and algorithm utils`
**Risk:** Low (tests/ only — no production code changes)
**Effort:** ~2 hours
**Files Changed:** tests/ only (new files)

---

## Objective

Add comprehensive test coverage for 3 modules that were shipped during the Workshop Enhancement Suite without dedicated tests:

1. `shuffify/schemas/schedule_requests.py` — 189 lines, 0 tests
2. `shuffify/scheduler.py` — 327 lines, 0 tests
3. `shuffify/shuffle_algorithms/utils.py` — 77 lines, 0 tests

These modules are currently exercised indirectly through integration paths, but have no unit-level coverage to catch regressions.

---

## Item 1: Tests for `schedule_requests.py`

**File to create:** `tests/schemas/test_schedule_requests.py`

### What to test

#### `ScheduleCreateRequest`

| Test Case | Description | Expected |
|-----------|-------------|----------|
| Valid shuffle schedule | `job_type="shuffle"`, `algorithm_name="basic"`, all required fields | Passes validation |
| Valid raid schedule | `job_type="raid"`, `source_playlist_ids=["abc"]` | Passes validation |
| Valid raid_and_shuffle | Both `source_playlist_ids` and `algorithm_name` provided | Passes validation |
| Invalid job_type | `job_type="invalid"` | `ValidationError` with message about valid types |
| Missing source for raid | `job_type="raid"`, no `source_playlist_ids` | `ValidationError`: "source_playlist_ids required" |
| Missing algorithm for shuffle | `job_type="shuffle"`, no `algorithm_name` | `ValidationError`: "algorithm_name required" |
| Invalid algorithm_name | `algorithm_name="nonexistent"` | `ValidationError` with valid options |
| Invalid schedule_type | `schedule_type="monthly"` | `ValidationError` |
| Invalid interval value | `schedule_type="interval"`, `schedule_value="hourly"` | `ValidationError` with valid intervals |
| Valid cron expression | `schedule_type="cron"`, `schedule_value="0 6 * * 1"` | Passes validation |
| Invalid cron (wrong fields) | `schedule_type="cron"`, `schedule_value="0 6 *"` | `ValidationError`: "5 fields" |
| Empty target_playlist_id | `target_playlist_id=""` | `ValidationError` (min_length=1) |
| Whitespace trimming | `job_type="  shuffle  "` | Normalized to `"shuffle"` |
| Empty algorithm_name string | `algorithm_name="  "` | Normalized to `None` |

#### `ScheduleUpdateRequest`

| Test Case | Description | Expected |
|-----------|-------------|----------|
| Valid partial update | Only `is_enabled=False` | Passes validation |
| None fields are optional | All fields None | Passes validation |
| Invalid job_type on update | `job_type="invalid"` | `ValidationError` |
| Invalid algorithm on update | `algorithm_name="nonexistent"` | `ValidationError` |
| Extra fields ignored | `extra_field="value"` | Ignored (Config: extra="ignore") |

### Implementation notes

- Use `pytest.raises(ValidationError)` for error cases
- Access error details via `exc.errors()` to verify specific messages
- The `validate_algorithm_name` validator calls `ShuffleRegistry.get_available_algorithms()`, so tests must run within a context where algorithms are registered (they auto-register on import via `shuffify/shuffle_algorithms/__init__.py`)

---

## Item 2: Tests for `scheduler.py`

**File to create:** `tests/test_scheduler.py`

### What to test

#### `_parse_schedule(schedule_type, schedule_value)`

This is a pure function — easy to test in isolation.

| Test Case | Input | Expected Output |
|-----------|-------|-----------------|
| Interval: every_6h | `("interval", "every_6h")` | `("interval", {"hours": 6})` |
| Interval: every_12h | `("interval", "every_12h")` | `("interval", {"hours": 12})` |
| Interval: daily | `("interval", "daily")` | `("interval", {"days": 1})` |
| Interval: every_3d | `("interval", "every_3d")` | `("interval", {"days": 3})` |
| Interval: weekly | `("interval", "weekly")` | `("interval", {"weeks": 1})` |
| Interval: unknown value | `("interval", "unknown")` | `("interval", {"days": 1})` (default) |
| Cron: valid 5-field | `("cron", "30 6 * * 1")` | `("cron", {"minute": "30", "hour": "6", "day": "*", "month": "*", "day_of_week": "1"})` |
| Cron: invalid (3 fields) | `("cron", "0 6 *")` | `("cron", {"hour": 0, "minute": 0})` (default) |
| Unknown schedule_type | `("monthly", "1")` | `("interval", {"days": 1})` (default) |

#### `init_scheduler(app)`

| Test Case | Description | Expected |
|-----------|-------------|----------|
| Scheduler disabled | `app.config["SCHEDULER_ENABLED"] = False` | Returns `None` |
| Werkzeug reloader child | `app.debug=True`, `WERKZEUG_RUN_MAIN` not set | Returns `None` |
| Already running | Call twice in a row | Second call returns existing instance |
| Successful init | Valid app with DB config | Returns `BackgroundScheduler` instance |

#### `add_job_for_schedule(schedule, app)` / `remove_job_for_schedule(schedule_id)`

| Test Case | Description | Expected |
|-----------|-------------|----------|
| Add job | Pass valid Schedule model mock | Job registered with correct ID |
| Add replaces existing | Add same schedule twice | No error, replaces existing |
| Remove existing job | Remove after add | Job removed from scheduler |
| Remove nonexistent job | Remove with no matching job | No error (graceful) |
| Add with no scheduler | `_scheduler` is None | Raises `RuntimeError` |

#### `_execute_scheduled_job(app, schedule_id)`

| Test Case | Description | Expected |
|-----------|-------------|----------|
| Successful execution | Mock `JobExecutorService.execute` | Called with `schedule_id` |
| Execution failure | `JobExecutorService.execute` raises | Error logged, no crash |

#### Event listeners

| Test Case | Description | Expected |
|-----------|-------------|----------|
| `_on_job_executed` | Mock event | Logs info message |
| `_on_job_error` | Mock event with exception | Logs error message |
| `_on_job_missed` | Mock event | Logs warning message |

### Implementation notes

- `_parse_schedule` is a module-level function — import directly: `from shuffify.scheduler import _parse_schedule`
- For `init_scheduler`, create a minimal Flask app fixture with `SQLALCHEMY_DATABASE_URI` set to `sqlite:///:memory:`
- Mock `Schedule.query` for `_register_existing_jobs` tests
- Set/reset the global `_scheduler` variable between tests to avoid state leakage — use `shuffify.scheduler._scheduler = None` in teardown
- The `WERKZEUG_RUN_MAIN` check reads `os.environ` — use `monkeypatch.setenv` / `monkeypatch.delenv`

---

## Item 3: Tests for `shuffle_algorithms/utils.py`

**File to create:** `tests/algorithms/test_utils.py`

### What to test

#### `extract_uris(tracks)`

| Test Case | Input | Expected Output |
|-----------|-------|-----------------|
| Normal tracks | `[{"uri": "a"}, {"uri": "b"}]` | `["a", "b"]` |
| Empty list | `[]` | `[]` |
| Track without uri key | `[{"uri": "a"}, {"name": "b"}]` | `["a"]` |
| Track with empty uri | `[{"uri": "a"}, {"uri": ""}]` | `["a"]` (empty string is falsy) |
| All tracks have uris | 10 tracks | 10 URIs in order |

#### `split_keep_first(uris, keep_first)`

| Test Case | Input | Expected Output |
|-----------|-------|-----------------|
| Keep 0 | `(["a","b","c"], 0)` | `([], ["a","b","c"])` |
| Keep 2 | `(["a","b","c","d"], 2)` | `(["a","b"], ["c","d"])` |
| Keep all | `(["a","b"], 5)` | `(["a","b"], [])` |
| Negative keep_first | `(["a","b"], -1)` | `([], ["a","b"])` |
| Empty list | `([], 3)` | `([], [])` |

#### `split_into_sections(items, section_count)`

| Test Case | Input | Expected Output |
|-----------|-------|-----------------|
| Even split | `(["a","b","c","d"], 2)` | `[["a","b"], ["c","d"]]` |
| Uneven split | `(["a","b","c","d","e"], 2)` | `[["a","b","c"], ["d","e"]]` |
| More sections than items | `(["a","b"], 5)` | `[["a"], ["b"]]` (clamped) |
| Single section | `(["a","b","c"], 1)` | `[["a","b","c"]]` |
| Empty list | `([], 3)` | `[]` |
| 7 items, 3 sections | `(list("abcdefg"), 3)` | `[["a","b","c"], ["d","e","f"], ["g"]]` |

### Implementation notes

- These are pure functions — no mocking needed, no app context required
- Verify that `split_into_sections` distributes remainders correctly (first sections get +1)
- Test that `extract_uris` preserves order

---

## Verification Checklist

After implementation:

```bash
# Run new tests only
pytest tests/schemas/test_schedule_requests.py tests/test_scheduler.py tests/algorithms/test_utils.py -v

# Run full suite to confirm no regressions
pytest tests/ -v

# Lint check
flake8 shuffify/
```

**Expected outcome:** All new tests pass, all existing 690 tests still pass, flake8 clean.

---

## Dependencies

- **Blocks:** None (Phase 01 is independent)
- **Blocked by:** None (can start immediately)
- **Safe to run in parallel with:** Phase 02, Phase 04

---

*Generated by /techdebt scan on 2026-02-11*
