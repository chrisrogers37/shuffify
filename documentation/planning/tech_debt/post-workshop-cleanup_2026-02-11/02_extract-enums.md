# Phase 02: Extract Enums for Job Types and Schedule Values

**PR Title:** `refactor: Extract job type and schedule value string literals to enums`
**Risk:** Medium (modifies 6 files across schemas, services, models, scheduler)
**Effort:** ~2 hours
**Files Changed:** ~6 files in shuffify/

---

## Objective

Replace scattered hardcoded string literals for job types and schedule values with Python `StrEnum` classes. Currently, the same string values ("shuffle", "raid", "daily", "weekly", etc.) are duplicated across 6 files with no single source of truth.

### Current state — string literals scattered across:

| File | Line(s) | Strings |
|------|---------|---------|
| `shuffify/schemas/schedule_requests.py` | 18 | `VALID_JOB_TYPES = {"raid", "shuffle", "raid_and_shuffle"}` |
| `shuffify/schemas/schedule_requests.py` | 19-26 | `VALID_SCHEDULE_TYPES`, `VALID_INTERVAL_VALUES` |
| `shuffify/schemas/schedule_requests.py` | 37-38 | `default="interval"`, `default="daily"` |
| `shuffify/schemas/schedule_requests.py` | 100, 106, 112, 120 | `"raid"`, `"shuffle"`, `"interval"`, `"cron"` comparisons |
| `shuffify/scheduler.py` | 258-292 | `"interval"`, `"cron"`, `"every_6h"`, `"daily"`, `"weekly"`, etc. |
| `shuffify/services/job_executor_service.py` | 267-275 | `"raid"`, `"shuffle"`, `"raid_and_shuffle"` |
| `shuffify/models/db.py` | 282, 285 | `default="interval"`, `default="daily"` |

### Target state

A single `shuffify/enums.py` module with 3 enum classes, imported everywhere.

---

## Implementation

### Step 1: Create `shuffify/enums.py`

```python
"""
Enums for job types, schedule types, and interval values.

Single source of truth for string constants used across schemas,
models, services, and the scheduler.
"""

from enum import StrEnum


class JobType(StrEnum):
    """Types of scheduled jobs."""
    RAID = "raid"
    SHUFFLE = "shuffle"
    RAID_AND_SHUFFLE = "raid_and_shuffle"


class ScheduleType(StrEnum):
    """Schedule trigger types."""
    INTERVAL = "interval"
    CRON = "cron"


class IntervalValue(StrEnum):
    """Predefined interval values for interval schedules."""
    EVERY_6H = "every_6h"
    EVERY_12H = "every_12h"
    DAILY = "daily"
    EVERY_3D = "every_3d"
    WEEKLY = "weekly"
```

**Why `StrEnum`?** Because `StrEnum` members compare equal to their string values (`JobType.RAID == "raid"` is `True`), making this a **backwards-compatible** change. No existing comparisons, serialization, or database values need to change.

### Step 2: Update `shuffify/schemas/schedule_requests.py`

**Before:**
```python
VALID_JOB_TYPES = {"raid", "shuffle", "raid_and_shuffle"}
VALID_SCHEDULE_TYPES = {"interval", "cron"}
VALID_INTERVAL_VALUES = {
    "every_6h",
    "every_12h",
    "daily",
    "every_3d",
    "weekly",
}
```

**After:**
```python
from shuffify.enums import JobType, ScheduleType, IntervalValue

VALID_JOB_TYPES = set(JobType)
VALID_SCHEDULE_TYPES = set(ScheduleType)
VALID_INTERVAL_VALUES = set(IntervalValue)
```

Also update the comparisons in `validate_job_requirements`:

**Before (line 100):**
```python
if self.job_type in ("raid", "raid_and_shuffle"):
```

**After:**
```python
if self.job_type in (JobType.RAID, JobType.RAID_AND_SHUFFLE):
```

**Before (line 106):**
```python
if self.job_type in ("shuffle", "raid_and_shuffle"):
```

**After:**
```python
if self.job_type in (JobType.SHUFFLE, JobType.RAID_AND_SHUFFLE):
```

**Before (line 112):**
```python
if self.schedule_type == "interval":
```

**After:**
```python
if self.schedule_type == ScheduleType.INTERVAL:
```

**Before (line 120):**
```python
if self.schedule_type == "cron":
```

**After:**
```python
if self.schedule_type == ScheduleType.CRON:
```

Update Field defaults:

**Before (lines 37-38):**
```python
schedule_type: str = Field(default="interval")
schedule_value: str = Field(default="daily")
```

**After:**
```python
schedule_type: str = Field(default=ScheduleType.INTERVAL)
schedule_value: str = Field(default=IntervalValue.DAILY)
```

### Step 3: Update `shuffify/scheduler.py`

**Before (lines 258-292):**
```python
if schedule_type == "interval":
    interval_map = {
        "every_6h": {"hours": 6},
        "every_12h": {"hours": 12},
        "daily": {"days": 1},
        "every_3d": {"days": 3},
        "weekly": {"weeks": 1},
    }
    ...
elif schedule_type == "cron":
    ...
```

**After:**
```python
from shuffify.enums import ScheduleType, IntervalValue

if schedule_type == ScheduleType.INTERVAL:
    interval_map = {
        IntervalValue.EVERY_6H: {"hours": 6},
        IntervalValue.EVERY_12H: {"hours": 12},
        IntervalValue.DAILY: {"days": 1},
        IntervalValue.EVERY_3D: {"days": 3},
        IntervalValue.WEEKLY: {"weeks": 1},
    }
    ...
elif schedule_type == ScheduleType.CRON:
    ...
```

### Step 4: Update `shuffify/services/job_executor_service.py`

**Before (lines 267-275):**
```python
if schedule.job_type == "raid":
    ...
elif schedule.job_type == "shuffle":
    ...
elif schedule.job_type == "raid_and_shuffle":
    ...
```

**After:**
```python
from shuffify.enums import JobType

if schedule.job_type == JobType.RAID:
    ...
elif schedule.job_type == JobType.SHUFFLE:
    ...
elif schedule.job_type == JobType.RAID_AND_SHUFFLE:
    ...
```

### Step 5: Update `shuffify/models/db.py`

**Before (lines 282, 285):**
```python
schedule_type = db.Column(
    db.String(10), nullable=False, default="interval"
)
schedule_value = db.Column(
    db.String(100), nullable=False, default="daily"
)
```

**After:**
```python
from shuffify.enums import ScheduleType, IntervalValue

schedule_type = db.Column(
    db.String(10), nullable=False, default=ScheduleType.INTERVAL
)
schedule_value = db.Column(
    db.String(100), nullable=False, default=IntervalValue.DAILY
)
```

---

## Why This Is Safe

1. **`StrEnum` is backwards-compatible**: `JobType.RAID == "raid"` returns `True`. Existing database records, serialized JSON, and API payloads all continue to work unchanged.

2. **No database migration needed**: Column defaults use the same string values. Existing rows compare correctly against enum members.

3. **No API contract changes**: Request/response bodies still contain plain strings. Pydantic serializes `StrEnum` values as strings automatically.

4. **Tests as safety net**: All 690 existing tests continue to pass because the string comparisons remain identical.

---

## Verification Checklist

```bash
# 1. Run full test suite (must maintain 690 passing)
pytest tests/ -v

# 2. Lint check
flake8 shuffify/

# 3. Verify no remaining hardcoded strings (should return 0 matches in modified files)
grep -rn '"shuffle"\|"raid"\|"raid_and_shuffle"' shuffify/schemas/schedule_requests.py shuffify/scheduler.py shuffify/services/job_executor_service.py shuffify/models/db.py

# 4. Verify new file exists
python -c "from shuffify.enums import JobType, ScheduleType, IntervalValue; print('OK')"
```

**Expected outcome:** All 690 tests pass, flake8 clean, no hardcoded strings in modified files.

---

## Dependencies

- **Blocks:** Phase 03 (routes Blueprint split touches some of the same files)
- **Blocked by:** None (can start immediately)
- **Safe to run in parallel with:** Phase 01, Phase 04

---

*Generated by /techdebt scan on 2026-02-11*
