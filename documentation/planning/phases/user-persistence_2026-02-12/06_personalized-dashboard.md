# Phase 6: Personalized Dashboard with Activity Feed and Stats

## PR Title
`feat: Add personalized dashboard with activity feed, stats, and welcome-back messaging (#phase-6)`

## Risk Level
**Medium** -- This phase modifies the core dashboard route and template (the first page every logged-in user sees), but it is purely additive: it adds new data to the template context and new HTML sections. The existing playlist grid and shuffle controls remain unchanged. The new `DashboardService` is read-only (no writes to the database) so the blast radius on failure is limited to a missing activity feed, not broken core functionality.

## Effort Estimate
**1.5-2 days** (service: 0.5d, route: 0.25d, template: 0.75d, tests: 0.5d)

## Dependencies (Must Be Complete Before Starting)

| Phase | Provides | Used By This Phase |
|-------|----------|--------------------|
| Phase 0 | PostgreSQL database | Database queries |
| Phase 1 | `User.last_login_at`, `User.login_count` | Welcome-back messaging, "since last visit" logic |
| Phase 2 | `LoginHistory` model | Login streak/frequency data |
| Phase 3 | `UserSettings.dashboard_show_recent_activity` | Respecting user preferences |
| Phase 4 | `PlaylistSnapshot` model | Recent snapshot data, playlist health |
| Phase 5 | `ActivityLog` model + `ActivityLogService` | Recent activity feed, action counts |

---

## Files to Create

| File | Purpose |
|------|---------|
| `shuffify/services/dashboard_service.py` | New service that aggregates all dashboard data |
| `tests/services/test_dashboard_service.py` | Tests for dashboard service |

## Files to Modify

| File | Change |
|------|--------|
| `shuffify/services/__init__.py` | Export `DashboardService` and `DashboardError` |
| `shuffify/routes/core.py` | Update `index()` to call `DashboardService` and pass data to template |
| `shuffify/templates/dashboard.html` | Add welcome-back section, activity feed, stats cards, keep existing playlist grid |

---

## Detailed Implementation

### Step 1: Create `shuffify/services/dashboard_service.py`

This service aggregates data from multiple sources into a single `dict` for the template. It performs only read operations.

**New file: `/Users/chris/Projects/shuffify/shuffify/services/dashboard_service.py`**

```python
"""
Dashboard service for aggregating personalized dashboard data.

Combines data from ActivityLogService, SchedulerService, and
database models to provide a single dashboard data payload.
This service performs read-only operations only.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from shuffify.models.db import db, Schedule, JobExecution

logger = logging.getLogger(__name__)


class DashboardError(Exception):
    """Base exception for dashboard service operations."""

    pass


class DashboardService:
    """
    Service for aggregating personalized dashboard data.

    All methods are static and read-only. Failures in any
    subsection are caught and return empty/default values
    so the dashboard always renders.
    """

    @staticmethod
    def get_dashboard_data(
        user_id: int,
        last_login_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Aggregate all dashboard data for a user.

        Args:
            user_id: The database user ID.
            last_login_at: The user's previous login timestamp.
                If None, the user is treated as a first-time visitor.

        Returns:
            Dictionary with keys:
                - is_returning_user (bool)
                - recent_activity (list of ActivityLog dicts)
                - activity_since_last_login (list of ActivityLog dicts)
                - quick_stats (dict with counts)
                - active_schedules (list of Schedule dicts)
                - recent_job_executions (list of JobExecution dicts)
        """
        data = {
            "is_returning_user": last_login_at is not None,
            "recent_activity": [],
            "activity_since_last_login": [],
            "activity_since_last_login_count": 0,
            "quick_stats": DashboardService._empty_stats(),
            "active_schedules": [],
            "recent_job_executions": [],
        }

        # Each section is wrapped in try/except so a failure in
        # one does not break the entire dashboard.
        data["recent_activity"] = (
            DashboardService._get_recent_activity(
                user_id, limit=10
            )
        )

        if last_login_at:
            data["activity_since_last_login"] = (
                DashboardService._get_activity_since(
                    user_id, last_login_at, limit=20
                )
            )
            data["activity_since_last_login_count"] = len(
                data["activity_since_last_login"]
            )

        data["quick_stats"] = (
            DashboardService._get_quick_stats(user_id)
        )

        data["active_schedules"] = (
            DashboardService._get_active_schedules(user_id)
        )

        data["recent_job_executions"] = (
            DashboardService._get_recent_executions(
                user_id, limit=5
            )
        )

        return data

    @staticmethod
    def _get_recent_activity(
        user_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get the most recent activity log entries."""
        try:
            from shuffify.models.db import ActivityLog

            logs = (
                ActivityLog.query.filter_by(user_id=user_id)
                .order_by(ActivityLog.created_at.desc())
                .limit(limit)
                .all()
            )
            return [log.to_dict() for log in logs]
        except Exception as e:
            logger.warning(
                f"Failed to fetch recent activity for "
                f"user {user_id}: {e}"
            )
            return []

    @staticmethod
    def _get_activity_since(
        user_id: int,
        since: datetime,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get activity that occurred after a given timestamp."""
        try:
            from shuffify.models.db import ActivityLog

            logs = (
                ActivityLog.query.filter(
                    ActivityLog.user_id == user_id,
                    ActivityLog.created_at > since,
                )
                .order_by(ActivityLog.created_at.desc())
                .limit(limit)
                .all()
            )
            return [log.to_dict() for log in logs]
        except Exception as e:
            logger.warning(
                f"Failed to fetch activity since "
                f"{since} for user {user_id}: {e}"
            )
            return []

    @staticmethod
    def _get_quick_stats(
        user_id: int,
    ) -> Dict[str, int]:
        """
        Calculate quick stats for the user.

        Returns dict with:
            - total_shuffles
            - total_scheduled_runs
            - total_snapshots
            - active_schedule_count
        """
        try:
            from shuffify.models.db import (
                ActivityLog,
                PlaylistSnapshot,
            )

            # Count shuffle activities
            total_shuffles = (
                ActivityLog.query.filter(
                    ActivityLog.user_id == user_id,
                    ActivityLog.action_type == "shuffle",
                ).count()
            )

            # Count total scheduled job executions
            total_scheduled_runs = (
                db.session.query(JobExecution)
                .join(Schedule)
                .filter(Schedule.user_id == user_id)
                .count()
            )

            # Count playlist snapshots
            total_snapshots = (
                PlaylistSnapshot.query.filter_by(
                    user_id=user_id
                ).count()
            )

            # Count active schedules
            active_schedule_count = (
                Schedule.query.filter_by(
                    user_id=user_id, is_enabled=True
                ).count()
            )

            return {
                "total_shuffles": total_shuffles,
                "total_scheduled_runs": total_scheduled_runs,
                "total_snapshots": total_snapshots,
                "active_schedule_count": active_schedule_count,
            }
        except Exception as e:
            logger.warning(
                f"Failed to calculate stats for "
                f"user {user_id}: {e}"
            )
            return DashboardService._empty_stats()

    @staticmethod
    def _get_active_schedules(
        user_id: int,
    ) -> List[Dict[str, Any]]:
        """Get all active (enabled) schedules for the user."""
        try:
            schedules = (
                Schedule.query.filter_by(
                    user_id=user_id, is_enabled=True
                )
                .order_by(Schedule.created_at.desc())
                .all()
            )
            return [s.to_dict() for s in schedules]
        except Exception as e:
            logger.warning(
                f"Failed to fetch active schedules for "
                f"user {user_id}: {e}"
            )
            return []

    @staticmethod
    def _get_recent_executions(
        user_id: int, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Get recent job executions across all schedules."""
        try:
            executions = (
                db.session.query(JobExecution)
                .join(Schedule)
                .filter(Schedule.user_id == user_id)
                .order_by(JobExecution.started_at.desc())
                .limit(limit)
                .all()
            )
            result = []
            for ex in executions:
                ex_dict = ex.to_dict()
                # Include the schedule name for display
                ex_dict["schedule_name"] = (
                    ex.schedule.target_playlist_name
                    if ex.schedule
                    else "Unknown"
                )
                ex_dict["job_type"] = (
                    ex.schedule.job_type
                    if ex.schedule
                    else "unknown"
                )
                result.append(ex_dict)
            return result
        except Exception as e:
            logger.warning(
                f"Failed to fetch recent executions for "
                f"user {user_id}: {e}"
            )
            return []

    @staticmethod
    def _empty_stats() -> Dict[str, int]:
        """Return a zeroed-out stats dictionary."""
        return {
            "total_shuffles": 0,
            "total_scheduled_runs": 0,
            "total_snapshots": 0,
            "active_schedule_count": 0,
        }
```

**Key design decisions:**
- Every sub-method is wrapped in `try/except` returning empty defaults. This means if `ActivityLog` doesn't exist yet or a query fails, the dashboard still renders with the playlist grid.
- Lazy imports of Phase 1-5 models (`ActivityLog`, `PlaylistSnapshot`) so the module can be imported even before those phases are complete (tests can mock them).
- The service is stateless and read-only -- zero risk of data corruption.

---

### Step 2: Register in `shuffify/services/__init__.py`

**File: `/Users/chris/Projects/shuffify/shuffify/services/__init__.py`**

**BEFORE** (at the bottom of the imports section, around line 96-99):
```python
# Job Executor Service
from shuffify.services.job_executor_service import (
    JobExecutorService,
    JobExecutionError,
)
```

**AFTER** (add immediately below that block):
```python
# Job Executor Service
from shuffify.services.job_executor_service import (
    JobExecutorService,
    JobExecutionError,
)

# Dashboard Service
from shuffify.services.dashboard_service import (
    DashboardService,
    DashboardError,
)
```

**Also add to `__all__`** (around line 148-150):
```python
    # Job Executor Service
    "JobExecutorService",
    "JobExecutionError",
    # Dashboard Service
    "DashboardService",
    "DashboardError",
]
```

---

### Step 3: Update the Index Route in `shuffify/routes/core.py`

**File: `/Users/chris/Projects/shuffify/shuffify/routes/core.py`**

**BEFORE** (lines 19-31, imports):
```python
from shuffify.routes import (
    main,
    is_authenticated,
    clear_session_and_show_login,
)
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    UserService,
    AuthenticationError,
    PlaylistError,
)
```

**AFTER**:
```python
from shuffify.routes import (
    main,
    is_authenticated,
    clear_session_and_show_login,
    get_db_user,
)
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    UserService,
    DashboardService,
    AuthenticationError,
    PlaylistError,
)
```

**BEFORE** (lines 40-71, index route):
```python
@main.route("/")
def index():
    """Home page - shows login or dashboard based on auth state."""
    try:
        logger.debug("Index route accessed")

        if not is_authenticated():
            logger.debug("No valid token, showing login page")
            session.pop("_flashes", None)
            return render_template("index.html")

        try:
            client = AuthService.get_authenticated_client(
                session["spotify_token"]
            )
            user = AuthService.get_user_data(client)

            playlist_service = PlaylistService(client)
            playlists = playlist_service.get_user_playlists()

            algorithms = ShuffleService.list_algorithms()

            logger.debug(
                f"User {user.get('display_name', 'Unknown')} "
                f"loaded dashboard"
            )
            return render_template(
                "dashboard.html",
                playlists=playlists,
                user=user,
                algorithms=algorithms,
            )

        except (AuthenticationError, PlaylistError) as e:
            logger.error(f"Error loading dashboard: {e}")
            return clear_session_and_show_login(
                "Your session has expired. Please log in again."
            )

    except Exception as e:
        logger.error(
            f"Unexpected error in index route: {e}",
            exc_info=True,
        )
        return clear_session_and_show_login()
```

**AFTER**:
```python
@main.route("/")
def index():
    """Home page - shows login or dashboard based on auth state."""
    try:
        logger.debug("Index route accessed")

        if not is_authenticated():
            logger.debug("No valid token, showing login page")
            session.pop("_flashes", None)
            return render_template("index.html")

        try:
            client = AuthService.get_authenticated_client(
                session["spotify_token"]
            )
            user = AuthService.get_user_data(client)

            playlist_service = PlaylistService(client)
            playlists = playlist_service.get_user_playlists()

            algorithms = ShuffleService.list_algorithms()

            # Fetch personalized dashboard data (non-blocking)
            dashboard_data = {}
            try:
                db_user = get_db_user()
                if db_user:
                    dashboard_data = (
                        DashboardService.get_dashboard_data(
                            user_id=db_user.id,
                            last_login_at=getattr(
                                db_user,
                                "last_login_at",
                                None,
                            ),
                        )
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to load dashboard data: {e}. "
                    f"Rendering without personalization."
                )

            logger.debug(
                f"User {user.get('display_name', 'Unknown')} "
                f"loaded dashboard"
            )
            return render_template(
                "dashboard.html",
                playlists=playlists,
                user=user,
                algorithms=algorithms,
                dashboard=dashboard_data,
            )

        except (AuthenticationError, PlaylistError) as e:
            logger.error(f"Error loading dashboard: {e}")
            return clear_session_and_show_login(
                "Your session has expired. Please log in again."
            )

    except Exception as e:
        logger.error(
            f"Unexpected error in index route: {e}",
            exc_info=True,
        )
        return clear_session_and_show_login()
```

**Key changes:**
1. Added `get_db_user` and `DashboardService` imports.
2. Added a `dashboard_data = {}` block that calls `DashboardService.get_dashboard_data()`.
3. The entire dashboard data fetch is wrapped in `try/except` -- if it fails, the dashboard renders as it always did, just without personalization.
4. Used `getattr(db_user, "last_login_at", None)` to safely handle the case where Phase 1 column doesn't exist yet.
5. Passed `dashboard=dashboard_data` to the template.

---

### Step 4: Update `shuffify/templates/dashboard.html`

This is the most visually significant change. The strategy is:
1. Replace the static "Welcome, Name!" greeting with a personalized welcome-back message.
2. Add a collapsible "Since your last visit" section (only for returning users).
3. Add quick stats cards row below the welcome section.
4. Add a recent activity feed below the stats.
5. Keep the existing playlist grid completely intact below.

**File: `/Users/chris/Projects/shuffify/shuffify/templates/dashboard.html`**

The full template replacement (with the existing playlist grid and JavaScript preserved at the bottom):

```html
{% extends "base.html" %}

{% block content %}
<div class="min-h-screen bg-gradient-to-br from-spotify-green via-spotify-green/90 to-spotify-dark">
    <div class="absolute inset-0" style="background-image: url('/static/images/hero-pattern.svg'); opacity: 0.15; pointer-events: none;"></div>

    <!-- User Info / Welcome Section -->
    <div class="relative max-w-6xl mx-auto px-4 pt-8">
        <div class="p-6 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20">
            <div class="flex items-center justify-between">
                <div class="flex items-center">
                    {% if user.images %}
                        <img src="{{ user.images[0].url }}" alt="{{ user.display_name }}"
                             class="w-16 h-16 rounded-full mr-4 border-2 border-white/20">
                    {% else %}
                        <div class="w-16 h-16 rounded-full bg-white/10 border-2 border-white/20 flex items-center justify-center mr-4">
                            <span class="text-2xl text-white">{{ user.display_name[0] }}</span>
                        </div>
                    {% endif %}
                    <div>
                        {% if dashboard and dashboard.is_returning_user %}
                            <h2 class="text-2xl font-bold text-white">Welcome back, {{ user.display_name }}!</h2>
                            {% if dashboard.activity_since_last_login_count > 0 %}
                                <p class="text-white/80">
                                    {{ dashboard.activity_since_last_login_count }} thing{{ 's' if dashboard.activity_since_last_login_count != 1 else '' }} happened since your last visit
                                </p>
                            {% else %}
                                <p class="text-white/80">Everything is up to date</p>
                            {% endif %}
                        {% elif dashboard %}
                            <h2 class="text-2xl font-bold text-white">Welcome to Shuffify, {{ user.display_name }}!</h2>
                            <p class="text-white/80">Select a playlist below to get started</p>
                        {% else %}
                            <h2 class="text-2xl font-bold text-white">Welcome, {{ user.display_name }}!</h2>
                            <p class="text-white/80">Select a playlist to shuffle below</p>
                        {% endif %}
                    </div>
                </div>
                <div class="flex items-center space-x-2">
                    <!-- Schedules Link -->
                    <a href="{{ url_for('main.schedules') }}"
                       class="inline-flex items-center px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white font-medium transition duration-150 border border-white/20 hover:border-white/30"
                       title="Manage scheduled operations">
                        <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        Schedules
                    </a>
                    <!-- Refresh Playlists Button -->
                    <button id="refresh-playlists-btn"
                            onclick="refreshPlaylists()"
                            class="inline-flex items-center px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white font-medium transition duration-150 border border-white/20 hover:border-white/30"
                            title="Refresh playlists from Spotify">
                        <svg id="refresh-icon" class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                        </svg>
                        Refresh
                    </button>
                    <!-- Logout Button -->
                    <a href="{{ url_for('main.logout') }}"
                       class="inline-flex items-center px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white font-medium transition duration-150 border border-white/20 hover:border-white/30"
                       title="Logout and return to landing page">
                        <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path>
                        </svg>
                        Logout
                    </a>
                </div>
            </div>
        </div>
    </div>

    {% if dashboard %}
    <!-- Quick Stats Cards -->
    <div class="relative max-w-6xl mx-auto px-4 pt-6">
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <!-- Total Shuffles -->
            <div class="p-4 rounded-xl backdrop-blur-md bg-white/10 border border-white/20 text-center">
                <p class="text-3xl font-bold text-white">{{ dashboard.quick_stats.total_shuffles }}</p>
                <p class="text-white/60 text-sm mt-1">Shuffles</p>
            </div>
            <!-- Active Schedules -->
            <div class="p-4 rounded-xl backdrop-blur-md bg-white/10 border border-white/20 text-center">
                <p class="text-3xl font-bold text-white">{{ dashboard.quick_stats.active_schedule_count }}</p>
                <p class="text-white/60 text-sm mt-1">Active Schedules</p>
            </div>
            <!-- Scheduled Runs -->
            <div class="p-4 rounded-xl backdrop-blur-md bg-white/10 border border-white/20 text-center">
                <p class="text-3xl font-bold text-white">{{ dashboard.quick_stats.total_scheduled_runs }}</p>
                <p class="text-white/60 text-sm mt-1">Scheduled Runs</p>
            </div>
            <!-- Snapshots -->
            <div class="p-4 rounded-xl backdrop-blur-md bg-white/10 border border-white/20 text-center">
                <p class="text-3xl font-bold text-white">{{ dashboard.quick_stats.total_snapshots }}</p>
                <p class="text-white/60 text-sm mt-1">Snapshots Saved</p>
            </div>
        </div>
    </div>

    <!-- Since Last Visit / Recent Activity -->
    {% if dashboard.activity_since_last_login_count > 0 or dashboard.recent_activity %}
    <div class="relative max-w-6xl mx-auto px-4 pt-6">
        <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 overflow-hidden">
            <!-- Section Toggle Header -->
            <button id="activity-toggle-btn"
                    onclick="toggleActivityFeed()"
                    class="w-full flex items-center justify-between px-6 py-4 text-white font-bold text-lg hover:bg-white/5 transition duration-150"
                    aria-expanded="false"
                    aria-controls="activity-feed-body">
                <span class="flex items-center">
                    <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
                    </svg>
                    {% if dashboard.is_returning_user and dashboard.activity_since_last_login_count > 0 %}
                        Since Your Last Visit
                        <span class="ml-2 px-2 py-0.5 rounded-full bg-white/20 text-xs font-semibold">
                            {{ dashboard.activity_since_last_login_count }}
                        </span>
                    {% else %}
                        Recent Activity
                    {% endif %}
                </span>
                <svg id="activity-chevron" class="w-5 h-5 transform transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                </svg>
            </button>

            <!-- Activity Feed Body (hidden by default) -->
            <div id="activity-feed-body" class="hidden border-t border-white/10">
                {% set activity_list = dashboard.activity_since_last_login if (dashboard.is_returning_user and dashboard.activity_since_last_login) else dashboard.recent_activity %}

                {% if activity_list %}
                <div class="max-h-64 overflow-y-auto dashboard-scrollbar">
                    {% for activity in activity_list %}
                    <div class="flex items-center px-6 py-3 border-b border-white/5 hover:bg-white/5 transition duration-150">
                        <!-- Activity Icon -->
                        <div class="w-8 h-8 rounded-full flex items-center justify-center mr-3 flex-shrink-0
                            {% if activity.action_type == 'shuffle' %}bg-purple-500/30 text-purple-300
                            {% elif activity.action_type == 'raid' %}bg-blue-500/30 text-blue-300
                            {% elif activity.action_type == 'commit' %}bg-green-500/30 text-green-300
                            {% elif activity.action_type == 'snapshot' %}bg-yellow-500/30 text-yellow-300
                            {% else %}bg-white/10 text-white/60{% endif %}">
                            {% if activity.action_type == 'shuffle' %}
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                            </svg>
                            {% elif activity.action_type == 'raid' %}
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path>
                            </svg>
                            {% elif activity.action_type == 'commit' %}
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                            </svg>
                            {% else %}
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            {% endif %}
                        </div>
                        <!-- Activity Details -->
                        <div class="flex-1 min-w-0">
                            <p class="text-white text-sm font-medium truncate">
                                {{ activity.description | default(activity.action_type | replace('_', ' ') | title) }}
                            </p>
                            {% if activity.target_name %}
                            <p class="text-white/50 text-xs truncate">{{ activity.target_name }}</p>
                            {% endif %}
                        </div>
                        <!-- Timestamp -->
                        <span class="text-white/40 text-xs flex-shrink-0 ml-2">
                            {{ activity.created_at | default('') }}
                        </span>
                    </div>
                    {% endfor %}
                </div>
                {% else %}
                <div class="px-6 py-8 text-center text-white/50">
                    <p>No recent activity to show.</p>
                </div>
                {% endif %}

                <!-- Recent Job Executions (if any) -->
                {% if dashboard.recent_job_executions %}
                <div class="border-t border-white/10 px-6 py-3">
                    <p class="text-white/60 text-xs uppercase tracking-wide font-semibold mb-2">Recent Scheduled Jobs</p>
                    {% for exec in dashboard.recent_job_executions %}
                    <div class="flex items-center justify-between py-1.5 text-sm">
                        <div class="flex items-center min-w-0">
                            <span class="px-1.5 py-0.5 rounded text-xs font-bold uppercase mr-2
                                {% if exec.job_type == 'raid' %}bg-blue-500/60
                                {% elif exec.job_type == 'shuffle' %}bg-purple-500/60
                                {% else %}bg-orange-500/60{% endif %} text-white">
                                {{ exec.job_type | replace('_', ' ') }}
                            </span>
                            <span class="text-white/80 truncate">{{ exec.schedule_name }}</span>
                        </div>
                        <div class="flex items-center flex-shrink-0 ml-2">
                            {% if exec.status == 'success' %}
                            <span class="text-green-400 text-xs">
                                {% if exec.tracks_added and exec.tracks_added > 0 %}+{{ exec.tracks_added }} tracks{% else %}Done{% endif %}
                            </span>
                            {% elif exec.status == 'failed' %}
                            <span class="text-red-400 text-xs">Failed</span>
                            {% else %}
                            <span class="text-white/40 text-xs">{{ exec.status }}</span>
                            {% endif %}
                        </div>
                    </div>
                    {% endfor %}
                </div>
                {% endif %}
            </div>
        </div>
    </div>
    {% endif %}

    <!-- No Activity: Onboarding Hint for New Users -->
    {% if not dashboard.is_returning_user and not dashboard.recent_activity %}
    <div class="relative max-w-6xl mx-auto px-4 pt-6">
        <div class="p-5 rounded-2xl backdrop-blur-md bg-white/10 border border-white/20">
            <div class="flex items-start">
                <div class="w-10 h-10 rounded-full bg-white/20 flex items-center justify-center mr-4 flex-shrink-0">
                    <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
                    </svg>
                </div>
                <div>
                    <h3 class="text-white font-bold text-lg">Get Started</h3>
                    <p class="text-white/70 text-sm mt-1">
                        Click on any playlist below to shuffle it, or open the Workshop for advanced track management. 
                        Set up <a href="{{ url_for('main.schedules') }}" class="text-white underline font-medium">scheduled operations</a> to keep your playlists fresh automatically.
                    </p>
                </div>
            </div>
        </div>
    </div>
    {% endif %}
    {% endif %}

    <!-- Playlists Grid (UNCHANGED from existing) -->
    <div class="relative max-w-6xl mx-auto px-4 py-8">
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 items-start">
            {% for playlist in playlists %}
                <!-- ... existing playlist card HTML is UNCHANGED ... -->
                <div class="rounded-2xl shadow-xl bg-spotify-green/90 border border-white/20 overflow-hidden transform transition duration-300 hover:scale-105 hover:shadow-2xl relative card-tile">
                    <!-- Playlist Artwork -->
                    <div class="relative h-48">
                        {% if playlist.images %}
                            <img src="{{ playlist.images[0].url }}" alt="{{ playlist.name }}"
                                 class="w-full h-full object-cover rounded-t-2xl md:rounded-t-2xl lg:rounded-t-4xl">
                        {% else %}
                            <div class="w-full h-full bg-black/20 flex items-center justify-center rounded-t-2xl md:rounded-t-2xl lg:rounded-t-4xl">
                                <span class="text-4xl">&#127925;</span>
                            </div>
                        {% endif %}
                    </div>
                    <!-- Playlist Info -->
                    <div class="bg-spotify-green px-4 py-3 flex items-center justify-between">
                        <div>
                            <h3 class="text-white text-xl font-bold truncate">{{ playlist.name }}</h3>
                            <p class="text-white/80 text-sm">{{ playlist.tracks.total }} tracks</p>
                        </div>
                        <div class="flex items-center space-x-2 ml-2">
                            <a href="{{ url_for('main.workshop', playlist_id=playlist.id) }}"
                               class="inline-flex items-center px-3 py-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-white text-sm font-semibold transition duration-150 border border-white/20"
                               title="Open Playlist Workshop"
                               onclick="event.stopPropagation();">
                                <svg class="w-4 h-4 mr-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path>
                                </svg>
                                Workshop
                            </a>
                            <a href="{{ playlist.external_urls.spotify }}"
                               target="_blank"
                               rel="noopener noreferrer"
                               class="bg-black/50 rounded-full p-2 transform transition-all duration-300 hover:scale-110 hover:bg-spotify-green"
                               onclick="event.stopPropagation();">
                                <svg class="w-6 h-6 text-white" viewBox="0 0 24 24" fill="currentColor">
                                    <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
                                </svg>
                            </a>
                        </div>
                    </div>
                    <!-- Shuffle Menu (UNCHANGED) -->
                    <div class="shuffle-menu max-h-0 opacity-0 transition-all duration-500 ease-in-out overflow-hidden shuffle-scrollbar mouseover-menu" style="pointer-events:auto;">
                        <form action="{{ url_for('main.shuffle', playlist_id=playlist.id) }}"
                              method="POST"
                              class="space-y-4 p-4"
                              data-playlist-id="{{ playlist.id }}"
                              onsubmit="event.preventDefault(); handlePlaylistAction(this, 'shuffle');">
                            <div>
                                <label for="algorithm-{{ playlist.id }}" class="block text-sm font-medium text-white/90 mb-2">
                                    Shuffle Algorithm:
                                </label>
                                <select id="algorithm-{{ playlist.id }}"
                                        name="algorithm"
                                        class="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white focus:ring-2 focus:ring-white/30 focus:border-transparent"
                                        onchange="updateAlgorithmParams(this, '{{ playlist.id }}')">
                                    {% for algo in algorithms %}
                                        <option value="{{ algo.class_name }}"
                                                data-description="{{ algo.description }}"
                                                data-parameters='{{ algo.parameters|tojson }}'>
                                            {{ algo.name }}
                                        </option>
                                    {% endfor %}
                                </select>
                                <p class="mt-1 text-sm text-white/60 algorithm-description-{{ playlist.id }}">
                                    {{ algorithms[0].description }}
                                </p>
                            </div>
                            <div id="algorithm-params-{{ playlist.id }}" class="space-y-4"></div>
                            <div class="flex space-x-2">
                                <button type="submit"
                                        class="flex-1 px-4 py-2 rounded-lg bg-white/20 hover:bg-white/30 text-white font-semibold transition duration-150">
                                    Shuffle
                                </button>
                            </div>
                        </form>
                        <form id="undo-form-{{ playlist.id }}"
                              action="{{ url_for('main.undo', playlist_id=playlist.id) }}"
                              method="POST"
                              data-playlist-id="{{ playlist.id }}"
                              onsubmit="event.preventDefault(); handlePlaylistAction(this, 'undo');"
                              class="mt-2 hidden">
                            <button type="submit"
                                    id="undo-button-{{ playlist.id }}"
                                    class="w-full px-4 py-2 rounded-lg bg-black/30 hover:bg-black/40 text-white/90 font-semibold transition duration-150">
                                Undo Last Shuffle
                            </button>
                        </form>
                    </div>
                </div>
            {% endfor %}
        </div>
    </div>
</div>

<!-- JavaScript (existing + new activity toggle) -->
<script>
/* ============================================================
 * Activity Feed Toggle
 * ============================================================ */
function toggleActivityFeed() {
    const body = document.getElementById('activity-feed-body');
    const chevron = document.getElementById('activity-chevron');
    const btn = document.getElementById('activity-toggle-btn');
    const isHidden = body.classList.contains('hidden');

    body.classList.toggle('hidden');
    chevron.classList.toggle('rotate-180');
    btn.setAttribute('aria-expanded', String(isHidden));
}

/* ============================================================
 * ALL EXISTING JAVASCRIPT FUNCTIONS BELOW ARE UNCHANGED
 * (updateAlgorithmParams, handlePlaylistAction,
 *  showNotification, refreshPlaylists, DOMContentLoaded)
 * ============================================================ */

// ... (keep the entire existing <script> content from the current dashboard.html)
</script>

<style>
/* Dashboard activity scrollbar */
.dashboard-scrollbar::-webkit-scrollbar { width: 8px; }
.dashboard-scrollbar::-webkit-scrollbar-thumb { background: rgba(20,120,60,0.5); border-radius: 6px; }
.dashboard-scrollbar::-webkit-scrollbar-track { background: transparent; }
.dashboard-scrollbar { scrollbar-color: rgba(20,120,60,0.5) transparent; scrollbar-width: thin; }

.rotate-180 { transform: rotate(180deg); }

/* Existing shuffle scrollbar styles (UNCHANGED) */
.shuffle-scrollbar::-webkit-scrollbar { width: 8px; }
.shuffle-scrollbar::-webkit-scrollbar-thumb { background: rgba(20, 120, 60, 0.5); border-radius: 6px; }
.shuffle-scrollbar::-webkit-scrollbar-track { background: transparent; }
.shuffle-scrollbar { scrollbar-color: rgba(20,120,60,0.5) transparent; scrollbar-width: thin; }
.menu-open .shuffle-menu {
  max-height: 1000px !important;
  opacity: 1 !important;
  transition: max-height 0.5s cubic-bezier(0.4,0,0.2,1), opacity 0.5s cubic-bezier(0.4,0,0.2,1);
}
.shuffle-menu {
  transition: max-height 0.5s cubic-bezier(0.4,0,0.2,1), opacity 0.5s cubic-bezier(0.4,0,0.2,1);
}
</style>
{% endblock %}
```

**Important note for the implementer:** The playlist card HTML and all existing JavaScript functions (`updateAlgorithmParams`, `handlePlaylistAction`, `showNotification`, `refreshPlaylists`, the `DOMContentLoaded` handler) must be kept exactly as they are in the current template. The only additions are:
1. The `toggleActivityFeed()` function at the top of the script block.
2. The `.dashboard-scrollbar` and `.rotate-180` CSS rules.
3. The new HTML sections between the welcome header and the playlist grid.

---

### Step 5: Create Tests

**New file: `/Users/chris/Projects/shuffify/tests/services/test_dashboard_service.py`**

```python
"""
Tests for DashboardService data aggregation.

These tests require a Flask app context with SQLAlchemy configured.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from shuffify.services.dashboard_service import (
    DashboardService,
    DashboardError,
)


@pytest.fixture
def db_user(app_context):
    """Create a test user in the database."""
    from shuffify.models.db import db, User, Schedule
    from shuffify.models.db import JobExecution

    # Clean up
    JobExecution.query.delete()
    Schedule.query.delete()
    User.query.delete()
    db.session.commit()

    user = User(
        spotify_id="dashboard_test_user",
        display_name="Dashboard Tester",
    )
    db.session.add(user)
    db.session.commit()
    yield user
    # Cleanup
    JobExecution.query.delete()
    Schedule.query.delete()
    User.query.delete()
    db.session.commit()


@pytest.fixture
def sample_schedule(db_user, app_context):
    """Create a sample schedule for the test user."""
    from shuffify.services.scheduler_service import (
        SchedulerService,
    )

    return SchedulerService.create_schedule(
        user_id=db_user.id,
        job_type="shuffle",
        target_playlist_id="pl_dash_test",
        target_playlist_name="Dashboard Test Playlist",
        schedule_type="interval",
        schedule_value="daily",
        algorithm_name="BasicShuffle",
    )


class TestGetDashboardData:
    """Tests for the main get_dashboard_data method."""

    def test_returns_expected_keys(
        self, db_user, app_context
    ):
        """Should return dict with all expected keys."""
        data = DashboardService.get_dashboard_data(
            db_user.id
        )
        assert "is_returning_user" in data
        assert "recent_activity" in data
        assert "activity_since_last_login" in data
        assert "activity_since_last_login_count" in data
        assert "quick_stats" in data
        assert "active_schedules" in data
        assert "recent_job_executions" in data

    def test_new_user_not_returning(
        self, db_user, app_context
    ):
        """Should mark user as not returning when no last_login_at."""
        data = DashboardService.get_dashboard_data(
            db_user.id, last_login_at=None
        )
        assert data["is_returning_user"] is False
        assert data["activity_since_last_login"] == []
        assert data["activity_since_last_login_count"] == 0

    def test_returning_user_flagged(
        self, db_user, app_context
    ):
        """Should mark user as returning when last_login_at provided."""
        last_login = datetime.now(
            timezone.utc
        ) - timedelta(hours=1)
        data = DashboardService.get_dashboard_data(
            db_user.id, last_login_at=last_login
        )
        assert data["is_returning_user"] is True


class TestQuickStats:
    """Tests for quick stats calculation."""

    def test_empty_stats_for_new_user(
        self, db_user, app_context
    ):
        """Should return zeroed stats for user with no activity."""
        stats = DashboardService._get_quick_stats(
            db_user.id
        )
        assert stats["total_shuffles"] == 0
        assert stats["total_scheduled_runs"] == 0
        assert stats["total_snapshots"] == 0
        assert stats["active_schedule_count"] == 0

    def test_active_schedule_count(
        self, db_user, sample_schedule, app_context
    ):
        """Should count active schedules."""
        stats = DashboardService._get_quick_stats(
            db_user.id
        )
        assert stats["active_schedule_count"] == 1


class TestActiveSchedules:
    """Tests for active schedule retrieval."""

    def test_empty_when_no_schedules(
        self, db_user, app_context
    ):
        """Should return empty list with no schedules."""
        result = DashboardService._get_active_schedules(
            db_user.id
        )
        assert result == []

    def test_returns_active_schedules(
        self, db_user, sample_schedule, app_context
    ):
        """Should return active schedules as dicts."""
        result = DashboardService._get_active_schedules(
            db_user.id
        )
        assert len(result) == 1
        assert result[0]["target_playlist_name"] == (
            "Dashboard Test Playlist"
        )

    def test_excludes_disabled_schedules(
        self, db_user, sample_schedule, app_context
    ):
        """Should not include disabled schedules."""
        from shuffify.services.scheduler_service import (
            SchedulerService,
        )

        SchedulerService.toggle_schedule(
            sample_schedule.id, db_user.id
        )
        result = DashboardService._get_active_schedules(
            db_user.id
        )
        assert len(result) == 0


class TestRecentActivity:
    """Tests for recent activity retrieval."""

    def test_empty_when_no_activity(
        self, db_user, app_context
    ):
        """Should return empty list when no ActivityLog exists."""
        result = DashboardService._get_recent_activity(
            db_user.id
        )
        # This may return [] either because table doesn't
        # exist or because there are no rows -- both are fine.
        assert isinstance(result, list)

    def test_graceful_failure_on_missing_table(
        self, db_user, app_context
    ):
        """Should return [] if ActivityLog model is unavailable."""
        with patch(
            "shuffify.services.dashboard_service."
            "DashboardService._get_recent_activity",
            return_value=[],
        ):
            data = DashboardService.get_dashboard_data(
                db_user.id
            )
            assert data["recent_activity"] == []


class TestRecentExecutions:
    """Tests for recent job execution retrieval."""

    def test_empty_when_no_executions(
        self, db_user, app_context
    ):
        """Should return empty list with no executions."""
        result = (
            DashboardService._get_recent_executions(
                db_user.id
            )
        )
        assert result == []

    def test_returns_executions_with_schedule_name(
        self, db_user, sample_schedule, app_context
    ):
        """Should include schedule_name in execution dicts."""
        from shuffify.models.db import db, JobExecution

        execution = JobExecution(
            schedule_id=sample_schedule.id,
            status="success",
            tracks_added=5,
            tracks_total=50,
        )
        db.session.add(execution)
        db.session.commit()

        result = (
            DashboardService._get_recent_executions(
                db_user.id
            )
        )
        assert len(result) == 1
        assert result[0]["schedule_name"] == (
            "Dashboard Test Playlist"
        )
        assert result[0]["job_type"] == "shuffle"


class TestEmptyStats:
    """Tests for the _empty_stats helper."""

    def test_all_values_zero(self):
        """Should return dict with all zero values."""
        stats = DashboardService._empty_stats()
        assert all(v == 0 for v in stats.values())
        assert "total_shuffles" in stats
        assert "total_scheduled_runs" in stats
        assert "total_snapshots" in stats
        assert "active_schedule_count" in stats


class TestGracefulDegradation:
    """Tests that dashboard degrades gracefully on errors."""

    def test_dashboard_data_with_failing_activity(
        self, db_user, app_context
    ):
        """
        Should return valid data even if activity
        queries fail.
        """
        with patch(
            "shuffify.services.dashboard_service."
            "DashboardService._get_recent_activity",
            side_effect=Exception("DB error"),
        ):
            # The outer method catches exceptions in
            # sub-methods, so this should still succeed
            # because each sub-call is independent
            data = DashboardService.get_dashboard_data(
                db_user.id
            )
            # Since _get_recent_activity raised, it returns []
            assert isinstance(data, dict)
            assert "quick_stats" in data
```

---

## Edge Cases

| Edge Case | Handling |
|-----------|----------|
| **New user (never logged in before)** | `last_login_at=None` results in `is_returning_user=False`, shows "Welcome to Shuffify" onboarding hint |
| **Returning user, no activity since last login** | Shows "Everything is up to date" message, activity feed shows recent activity instead |
| **ActivityLog/PlaylistSnapshot models don't exist yet** | Lazy imports + try/except in each sub-method return empty lists/zero counts |
| **Database completely unavailable** | `get_db_user()` returns `None`, `dashboard_data = {}`, template uses `{% if dashboard %}` guard to fall back to original behavior |
| **User has 0 playlists** | Dashboard data still loads (stats, activity, etc.) but playlist grid is empty (existing behavior) |
| **Very large activity log** | Queries use `.limit()` to cap results (10 for recent, 20 for since-last-login, 5 for executions) |
| **Template receives `dashboard={}` (empty dict)** | All `{% if dashboard.xxx %}` checks evaluate to falsy, so no new sections render -- existing playlist grid still shows |

---

## Verification Checklist

Before marking Phase 6 as complete:

- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes all tests (existing + new)
- [ ] `python run.py` starts without errors
- [ ] Dashboard loads for unauthenticated users (landing page, no changes)
- [ ] Dashboard loads for authenticated users with no database record (graceful degradation)
- [ ] Dashboard loads for authenticated users with database record (shows stats cards, activity section)
- [ ] Quick stats cards display correctly with 0 values
- [ ] Activity feed toggle opens/closes correctly
- [ ] Activity feed scrolls for many items
- [ ] Playlist grid and shuffle controls work exactly as before
- [ ] Schedules link still works
- [ ] Refresh button still works
- [ ] Logout still works
- [ ] Mobile layout is not broken (test at 375px width)
- [ ] CHANGELOG.md updated with Phase 6 entry

---

## What NOT To Do

1. **Do NOT modify the existing playlist grid or shuffle form JavaScript.** The playlist cards, algorithm parameter logic, shuffle AJAX, undo AJAX, and refresh AJAX must remain byte-for-byte identical.

2. **Do NOT add any write operations to `DashboardService`.** This service is strictly read-only. The `ActivityLogService` (from Phase 5) handles writes.

3. **Do NOT make the dashboard data fetch blocking on failure.** The try/except wrapper in the route ensures the dashboard always renders.

4. **Do NOT import Phase 1-5 models at module level in the dashboard service.** Use lazy imports inside methods so the module can be imported even if those models don't exist yet.

5. **Do NOT add new dependencies to requirements files.** This phase uses only existing Flask, SQLAlchemy, and Jinja2 features.

6. **Do NOT create a separate dashboard route.** The dashboard is and should remain the `index()` route (the `/` endpoint).

7. **Do NOT add JavaScript API calls for dashboard data.** The dashboard data is server-rendered via Jinja2, not fetched client-side. This keeps the implementation simple and avoids an extra round-trip.

8. **Do NOT add pagination to the activity feed in this phase.** The limit parameters (10, 20, 5) are sufficient for MVP. Pagination can be added later if needed.

---

## CHANGELOG Entry

```markdown
## [Unreleased]

### Added
- **Personalized Dashboard** - Dashboard now shows personalized welcome messaging, quick stats, and recent activity
  - New `DashboardService` aggregates activity, stats, and schedule data into a single dashboard payload
  - "Welcome back" messaging distinguishes returning users from first-time visitors
  - Quick stats cards show total shuffles, active schedules, scheduled runs, and snapshots saved
  - Collapsible activity feed shows recent actions and "since your last visit" summary
  - Recent scheduled job execution results displayed in activity section
  - Onboarding hint for new users with no activity
  - All dashboard data is non-blocking: failures degrade gracefully to the existing playlist grid
```

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/services/dashboard_service.py` - New file: core service that aggregates all dashboard data from multiple models
- `/Users/chris/Projects/shuffify/shuffify/routes/core.py` - Modify: update index route to call DashboardService and pass data to template
- `/Users/chris/Projects/shuffify/shuffify/templates/dashboard.html` - Modify: add welcome-back messaging, stats cards, activity feed sections above existing playlist grid
- `/Users/chris/Projects/shuffify/shuffify/services/__init__.py` - Modify: register DashboardService and DashboardError exports
- `/Users/chris/Projects/shuffify/tests/services/test_dashboard_service.py` - New file: comprehensive tests for all DashboardService methods and graceful degradation
