"""Harness setup helpers use the same reviewed HTTP boundaries as the interface."""

import importlib
from unittest.mock import patch

import pytest

from shuffify.models.db import ActivityLog, Schedule, User, db
from tests.paths import api_path
from tests.playlist_setup_fixtures import SetupSpotify


def helper_module():
    assert importlib.util.find_spec("tests.routine_flow") is not None, "Reviewed HTTP setup helpers are missing"
    return importlib.import_module("tests.routine_flow")


class FlaskCalls:
    def __init__(self, client):
        self.client = client
        self.calls = []

    def ok(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        response = self.client.open(api_path(path), method=method.upper(), **kwargs)
        assert response.status_code == 200, response.json
        assert response.json["success"] is True
        return response.json


@pytest.fixture
def flow(auth_client):
    user = User.query.filter_by(spotify_id="user123").one()
    user.encrypted_refresh_token = "stored-test-token"
    db.session.commit()
    api = SetupSpotify()
    with patch("shuffify.routes.require_auth", return_value=api, autospec=True):
        yield FlaskCalls(auth_client), api


def test_shared_source_helper_reviews_original_changes_before_one_save(flow):
    client, api = flow
    helpers = helper_module()
    source = helpers.add_playlist_source(client, "target", "ext1")
    assert source["source_playlist_id"] == "ext1"
    writes = [(method, path, kwargs["json"]) for method, path, kwargs in client.calls if method != "get"]
    assert [path for _, path, _ in writes] == ["/playlist/target/setup/impact", "/playlist/target/setup/settings"]
    assert writes[0][2]["changes"] == writes[1][2]["changes"] == [
        {"op": "add_playlist_source", "playlist_id": "ext1"}
    ]
    assert "acknowledgment" in writes[1][2]
    assert "summary" not in writes[1][2]["changes"][0]
    assert Schedule.query.count() == 0
    assert api.writes == []


def test_routine_helper_uses_normalized_review_and_exact_state_revision(flow):
    client, api = flow
    helpers = helper_module()
    schedule = helpers.create_routine(client, "target", timing={
        "kind": "advanced", "expression": "* * * * *", "timezone": "UTC",
    })
    assert schedule["schedule_type"] == "cron" and schedule["schedule_value"] == "* * * * *"
    paused = helpers.set_routine_enabled(client, schedule["id"], False)
    assert paused["schedule"]["is_enabled"] is False
    assert helpers.list_schedules(client, "target")[0]["id"] == schedule["id"]
    review_call = next(body["json"] for _, path, body in client.calls if path.endswith("/review"))
    activate_call = next(body["json"] for _, path, body in client.calls if path.endswith("/activate"))
    assert activate_call["request_key"] == review_call["request_key"]
    assert activate_call["definition"]["target_playlist_id"] == "target"
    state_call = next(body["json"] for _, path, body in client.calls if path.endswith("/state"))
    assert state_call["desired_enabled"] is False
    assert len(state_call["expected_revision"]) == 64
    assert ActivityLog.query.filter_by(activity_type="schedule_create").count() == 1
    assert api.writes == []


def test_routine_helper_keeps_source_configuration_out_of_definition(flow):
    client, _ = flow
    helpers = helper_module()
    helpers.add_playlist_source(client, "target", "ext1")
    schedule = helpers.create_routine(client, "target", job_type="raid")
    assert schedule["job_type"] == "raid"
    review = next(body["json"] for _, path, body in client.calls if path.endswith("/review"))
    assert set(review["definition"]) == {"job_type", "timing"}
    assert schedule["source_playlist_ids"] == []
