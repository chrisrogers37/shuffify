"""HTTP boundary for the reviewed routine API."""

from unittest.mock import patch

import pytest

from shuffify.models.db import ActivityLog, RoutineActivationReceipt, Schedule, User
from shuffify.services import playlist_setup_service as setup_service
from shuffify.services.scheduler_service import ScheduleBusyError, SchedulerService
from tests.paths import api_path
from tests.playlist_setup_fixtures import SetupSpotify

BASE = "/playlist/target/routines"


@pytest.fixture
def api():
    return SetupSpotify()


@pytest.fixture
def user(auth_client):
    return User.query.filter_by(spotify_id="user123").one()


def test_context_and_review_are_read_only(auth_client, user, api):
    with patch("shuffify.routes.require_auth", return_value=api):
        context = auth_client.get(api_path(f"{BASE}/context"))
        review = auth_client.post(api_path(f"{BASE}/review"), json={
            "request_key": "review-1",
            "definition": {
                "job_type": "shuffle", "algorithm_name": "BasicShuffle",
                "timing": {"kind": "weekly", "weekday": "mon", "time": "09:00", "timezone": "UTC"},
            },
        })
    assert context.status_code == review.status_code == 200
    assert context.headers["Cache-Control"] == review.headers["Cache-Control"] == "no-store"
    body = review.get_json()
    assert body["definition"]["schedule_value"] == "0 9 * * mon"
    assert body["resource_scope"]["target_id"] == "target"
    assert Schedule.query.count() == RoutineActivationReceipt.query.count() == ActivityLog.query.count() == 0
    assert api.writes == []


@pytest.mark.parametrize("path,method", [
    (api_path(f"{BASE}/context"), "get"), (api_path(f"{BASE}/review"), "post"),
    (api_path(f"{BASE}/activate"), "post"), (api_path("/routines/1/state"), "put"),
])
def test_signed_out_requests_are_refused(db_app, path, method):
    with db_app.test_client() as client:
        response = getattr(client, method)(path, json={})
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"


def test_wire_contract_rejects_unknown_and_untyped_values(auth_client, api):
    with patch("shuffify.routes.require_auth", return_value=api):
        extra = auth_client.post(api_path(f"{BASE}/review"), json={
            "request_key": "one", "definition": {"job_type": "shuffle", "algorithm_name": "BasicShuffle",
                                                    "timing": {"kind": "interval", "interval": "daily"}},
            "unreviewed_switch": True,
        })
        untyped = auth_client.put(
            api_path("/routines/1/state"), json={"desired_enabled": "false", "expected_revision": "a" * 64},
        )
    assert extra.status_code == untyped.status_code == 400
    assert extra.get_json()["field_errors"][0]["path"] == "unreviewed_switch"
    assert untyped.get_json()["field_errors"][0]["path"] == "desired_enabled"


def test_context_and_review_each_use_one_setup_snapshot(auth_client, api):
    with patch("shuffify.routes.require_auth", return_value=api), patch.object(
        setup_service, "read_state", wraps=setup_service.read_state
    ) as read_state:
        context = auth_client.get(api_path(f"{BASE}/context"))
        assert context.status_code == 200, context.get_json()
        assert read_state.call_count == 1
        read_state.reset_mock()
        review = auth_client.post(api_path(f"{BASE}/review"), json={
            "request_key": "same-snapshot",
            "definition": {"job_type": "shuffle", "algorithm_name": "BasicShuffle",
                           "timing": {"kind": "interval", "interval": "daily"}},
        })
        assert review.status_code == 200, review.get_json()
        assert read_state.call_count == 1
        assert review.get_json()["dependency_revision"] == context.get_json()["dependency_revision"]


@pytest.mark.parametrize("target,status,code", [
    ("missing", 404, "playlist_not_found"),
    ("foreign", 403, "access_denied"),
])
def test_target_errors_match_setup(auth_client, api, target, status, code):
    with patch("shuffify.routes.require_auth", return_value=api):
        response = auth_client.get(api_path(f"/playlist/{target}/routines/context"))
    assert response.status_code == status
    assert response.get_json()["error_code"] == code


@pytest.mark.parametrize("method,path,body,service_method", [
    ("delete", api_path("/schedules/1"), None, "delete_schedule"),
])
def test_delete_schedule_busy_reply(auth_client, api, method, path, body, service_method):
    with patch("shuffify.routes.require_auth", return_value=api), patch.object(
        SchedulerService, service_method, side_effect=ScheduleBusyError("A routine is using this playlist. Try again.")
    ):
        response = getattr(auth_client, method)(path, json=body)
    assert response.status_code == 409
    assert response.get_json()["error_code"] == "playlist_busy"
