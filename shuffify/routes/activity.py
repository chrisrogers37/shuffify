"""
Activity routes: activity log page with KPI stats and history.
"""

import logging

from flask import render_template

from shuffify.routes import main, require_auth_and_db
from shuffify.services import (
    ActivityLogService,
    DashboardService,
)

logger = logging.getLogger(__name__)


@main.route("/activity")
@require_auth_and_db
def activity(client=None, user=None):
    """Activity Log page with full history and KPI stats."""
    stats = DashboardService.get_quick_stats(user.id)
    activities = ActivityLogService.get_recent(
        user.id, limit=100
    )
    executions = DashboardService.get_recent_executions(
        user.id, limit=20
    )

    return render_template(
        "activity.html",
        stats=stats,
        activities=activities,
        executions=executions,
    )
