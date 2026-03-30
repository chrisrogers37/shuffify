# Phase 04: Activity Log Page

**Status**: IN PROGRESS
**Started**: 2026-03-30
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
    stats = DashboardService.get_quick_stats(user.id)
    activities = ActivityLogService.get_recent(user.id, limit=100)
    executions = DashboardService.get_recent_executions(user.id, limit=20)

    return render_template(
        "activity.html",
        stats=stats,
        activities=activities,
        executions=executions,
    )
```

Notes:
- `active_nav` not passed — navbar auto-detects from `request.endpoint` via `endpoint_map`
- `ActivityLogService.get_recent()` returns `List[ActivityLog]` model instances (not dicts)
- Use `DashboardService.get_recent_executions()` instead of raw `JobExecution.query` (separation of concerns)
- `_get_quick_stats` and `_get_recent_executions` renamed to public (remove leading underscores) since they now have two consumers

Reuse existing services:
- `DashboardService.get_quick_stats(user_id)` — returns dict with shuffles, schedules, runs, snapshots
- `ActivityLogService.get_recent(user_id, limit)` — returns List[ActivityLog] model instances
- `DashboardService.get_recent_executions(user_id, limit)` — returns enriched execution dicts

### 4b. Activity template

**New file**: `shuffify/templates/activity.html`

Structure:
1. **KPI Stats Header** — 4-column grid (moved from dashboard). Same glass card styling: `bg-white/10 border border-white/20 rounded-xl`
2. **Activity List** — Full unfiltered list of all activities with icons, descriptions, timestamps. Same rendering pattern as dashboard's activity feed but without the "since last visit" filter.
3. **Job Executions** — Recent scheduled job results with status badges.

Optional: **Filter bar** at top of activity list — All, Shuffles, Schedules, Workshop, Snapshots, Raids. Client-side JS filtering using `data-type` attributes on each activity item.

### 4c. Register route module

**File**: `shuffify/routes/__init__.py`

Add `activity` to the grouped import block at the bottom with the other route modules:

```python
from shuffify.routes import (  # noqa: E402, F401
    core,
    playlists,
    shuffle,
    workshop,
    upstream_sources,
    schedules,
    settings,
    snapshots,
    playlist_pairs,
    raid_panel,
    playlist_preferences,
    activity,
)
```

### 4d. Dashboard cleanup

**File**: `shuffify/templates/dashboard.html`

Remove these sections:
- **KPI Stats Cards** (lines 85-109) — moved to Activity Log
- **"Since Your Last Visit" / Activity Feed** (lines 111-227) — replaced by Activity Log page

Also: verify whether `core.py` still needs `DashboardService.get_dashboard_data()` after removal. The onboarding hint (lines 229-248) may depend on dashboard variables — keep only what's needed.

### 4f. Make DashboardService methods public

**File**: `shuffify/services/dashboard_service.py`

Rename `_get_quick_stats` → `get_quick_stats` and `_get_recent_executions` → `get_recent_executions` (remove leading underscores). Update all internal call sites within the service. These methods now have two consumers (dashboard route and activity route) — they're public API.

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
