# Phase 04: Activity Log Page

**Status**: PENDING
**Depends on**: Phase 02
**Parallel with**: Phases 03 and 05

## Objective

Create a dedicated Activity Log page. Move KPI stats from dashboard to Activity Log header. Replace "Since Your Last Visit" with full unfiltered activity history. Clean up dashboard to be tiles-only.

## Files

### New
- `shuffify/routes/activity.py`
- `shuffify/templates/activity.html`

### Modified
- `shuffify/routes/__init__.py`
- `shuffify/templates/dashboard.html`
- `shuffify/templates/partials/navbar.html`

## Changes

### 4a. Activity route

**New file**: `shuffify/routes/activity.py`

```python
@main.route("/activity")
@require_auth_and_db
def activity(client=None, user=None):
    """Activity Log page with full history and KPI stats."""
    stats = DashboardService._get_quick_stats(user.id)
    activities = ActivityLogService.get_recent(user.id, limit=100)
    executions = (
        JobExecution.query
        .filter_by(user_id=user.id)
        .order_by(JobExecution.executed_at.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "activity.html",
        stats=stats,
        activities=activities,
        executions=executions,
        active_nav='activity',
    )
```

Reuse existing services:
- `DashboardService._get_quick_stats(user_id)` — already returns shuffles, schedules, runs, snapshots
- `ActivityLogService.get_recent(user_id, limit)` — already returns formatted activity dicts
- `JobExecution` query — same pattern as `DashboardService._get_recent_executions()`

### 4b. Activity template

**New file**: `shuffify/templates/activity.html`

Structure:
1. **KPI Stats Header** — 4-column grid (moved from dashboard). Same glass card styling: `bg-white/10 border border-white/20 rounded-xl`
2. **Activity List** — Full unfiltered list of all activities with icons, descriptions, timestamps. Same rendering pattern as dashboard's activity feed but without the "since last visit" filter.
3. **Job Executions** — Recent scheduled job results with status badges.

Optional: **Filter bar** at top of activity list — All, Shuffles, Schedules, Workshop, Snapshots, Raids. Client-side JS filtering using `data-type` attributes on each activity item.

### 4c. Register route module

**File**: `shuffify/routes/__init__.py`

Add import at the bottom with the other route module imports:

```python
from . import activity  # noqa: F401
```

### 4d. Dashboard cleanup

**File**: `shuffify/templates/dashboard.html`

Remove these sections:
- **KPI Stats Cards** (lines 112-137) — moved to Activity Log
- **"Since Your Last Visit" / Activity Feed** (lines 139-255) — replaced by Activity Log page

Dashboard becomes: welcome greeting card + Manage/Refresh toolbar + playlist tiles (favorites, regular, hidden).

Also remove the `dashboard.quick_stats`, `dashboard.recent_activity`, `dashboard.activity_since_last_login` template variable usage since those sections are gone.

### 4e. Update navbar

**File**: `shuffify/templates/partials/navbar.html`

Update Activity link from placeholder to `url_for('main.activity')`.

## Verification

1. Navigate to `/activity` → KPI cards at top (Shuffles, Active Schedules, Scheduled Runs, Snapshots)
2. Full activity list below — unfiltered, showing all activity types
3. Job execution results section with status badges
4. Dashboard no longer shows stats or activity sections
5. Nav bar highlights "Activity" on the page
6. `flake8 shuffify/` and `pytest tests/ -v`

## CHANGELOG Entry

```markdown
### Added
- **Activity Log Page** - Dedicated page for full activity history and KPI stats
  - KPI stats header (shuffles, active schedules, scheduled runs, snapshots saved)
  - Complete unfiltered activity log with icons and timestamps
  - Recent job execution results with status indicators

### Changed
- **Dashboard** - Simplified to playlist tiles only (KPIs and activity moved to Activity Log)

### Removed
- **"Since Your Last Visit"** - Replaced by comprehensive Activity Log page
```
