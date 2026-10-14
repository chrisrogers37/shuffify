"""Flask-WTF rejects live actions before any authorized invocation exists."""

from unittest.mock import patch

import pytest

LIVE_ACTIONS = [
    ("/playlist/untrusted-target/raid-now", "raid"),
    ("/playlist/untrusted-target/drip-now", "drip"),
    ("/playlist/untrusted-target/raid-and-drip", "raid_and_drip"),
    ("/schedules/98765/run", "schedule"),
]


@pytest.mark.parametrize("path,kind", LIVE_ACTIONS)
@pytest.mark.parametrize("token_state", ["missing", "expired"])
def test_live_csrf_is_known_not_started_without_resource_disclosure(
    csrf_client, csrf_headers, path, kind, token_state,
):
    with (
        patch("shuffify.routes.require_auth") as auth,
        patch("shuffify.routes.get_db_user") as db_user,
        patch("shuffify.routes.raid_panel.RaidSyncService") as raid,
        patch("shuffify.routes.schedules.JobExecutorService") as executor,
        patch("shuffify.services.activity_log_service.ActivityLogService.log") as activity,
    ):
        response = csrf_client.post(path, json={
            "resource": "untrusted-resource",
            "resource_name": "A name from an unverified request",
            "execution_id": 123,
            "operation": {"aggregate_status": "complete"},
        }, headers=csrf_headers(token_state))
    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert body["execution_id"] is None
    assert body["started_at"] is None
    assert body["execution_state"] == "not_started"
    assert body["tracks_added"] == body["tracks_total"] == 0
    assert body["operation"] == {
        "kind": kind,
        "aggregate_status": "partial",
        "resources": [],
        "bookkeeping": [],
        "error_code": "session_verification_expired",
        "retryable": False,
    }
    assert "This attempt did not run" in body["message"]
    assert "Reloading loses the recovery details kept on this page" in body["message"]
    assert "earlier Spotify changes remain" in body["message"]
    assert "untrusted" not in response.text
    assert "http_status" not in body
    for service in (auth, db_user, raid, executor, activity):
        service.assert_not_called()
