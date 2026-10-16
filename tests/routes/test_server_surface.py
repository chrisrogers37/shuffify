"""The backend exposes JSON resources, OAuth transport, and health only."""

import json
from pathlib import Path

import pytest

API_ROUTES = Path(__file__).parents[1] / "fixtures" / "api_routes.json"


def test_backend_keeps_the_complete_api_contract(app):
    actual = [
        {"path": rule.rule, "methods": sorted(rule.methods - {"HEAD", "OPTIONS"}), "endpoint": rule.endpoint}
        for rule in app.url_map.iter_rules() if rule.endpoint.startswith("api.")
    ]
    assert sorted(actual, key=lambda rule: (rule["path"], rule["methods"])) == json.loads(API_ROUTES.read_text())


def test_only_oauth_health_and_assets_exist_outside_the_api(app):
    assert {rule.endpoint for rule in app.url_map.iter_rules() if not rule.rule.startswith("/api/")} == {
        "static", "auth.login", "auth.callback", "auth.logout", "site.health",
    }


@pytest.mark.parametrize("path", [
    "/", "/terms", "/privacy", "/schedules", "/settings", "/activity", "/workshop",
    "/workshop/example-playlist", "/playlist/example-playlist/routines/new", "/playlist/example-playlist/setup",
])
def test_browser_paths_belong_to_the_frontend(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.is_json
    assert response.json["success"] is False


def test_native_settings_post_has_no_server_page_fallback(client):
    response = client.post("/settings", data={"max_snapshots_per_playlist": "17"})
    assert response.status_code == 404
    assert response.is_json


def test_json_session_is_the_csrf_source_for_authenticated_clients(auth_client):
    response = auth_client.get("/api/session")
    assert response.status_code == 200
    assert response.json["signed_in"] is True
    assert isinstance(response.json["csrf_token"], str)
    assert response.json["csrf_token"]
