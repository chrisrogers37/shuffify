"""Activity JSON reads preserve owned stats, rich details, and bounded queries."""

from unittest.mock import patch

import pytest

from shuffify.models.db import User


def test_activity_context_requires_authentication(client):
    response = client.get("/api/activity")
    assert response.status_code == 401
    assert response.json["success"] is False


@pytest.mark.parametrize("populated", [False, True])
def test_activity_context_preserves_stats_records_and_owned_read_limits(auth_client, populated):
    user_id = User.query.filter_by(spotify_id="user123").one().id
    stats = {"total_shuffles": 42, "total_raids": 7}
    activities = [{"activity_type": "shuffle", "description": "Shuffled playlist", "metadata": {}}] if populated else []
    executions = [{"id": 1, "status": "success", "result_detail": {"version": 1}}] if populated else []
    with patch("shuffify.routes.require_auth", autospec=True, return_value=object()), patch(
        "shuffify.routes.contexts.DashboardService.get_quick_stats", autospec=True, return_value=stats
    ) as read_stats, patch(
        "shuffify.routes.contexts.ActivityLogService.get_recent", autospec=True, return_value=activities
    ) as read_activity, patch(
        "shuffify.routes.contexts.DashboardService.get_recent_executions", autospec=True, return_value=executions
    ) as read_executions:
        response = auth_client.get("/api/activity")
    assert response.status_code == 200
    assert response.json["stats"] == stats
    assert len(response.json["activities"]) == len(activities)
    assert len(response.json["executions"]) == len(executions)
    if populated:
        assert response.json["activities"][0]["description"] == "Shuffled playlist"
        assert response.json["executions"][0]["result_detail"] == {"version": 1}
    read_stats.assert_called_once_with(user_id)
    read_activity.assert_called_once_with(user_id, limit=100)
    read_executions.assert_called_once_with(user_id, limit=20)
