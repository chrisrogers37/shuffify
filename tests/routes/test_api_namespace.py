"""The API namespace has one response contract, including early failures."""

from unittest.mock import patch

import pytest
from flask import request
from werkzeug.routing import IntegerConverter


def assert_envelope(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.mimetype == "application/json"
    if response.request.method == "HEAD":
        assert response.data == b""
        return
    payload = response.get_json()
    assert isinstance(payload["success"], bool)
    if not payload["success"]:
        assert payload["error_code"]
        assert payload["message"]
        assert payload["category"] == "error"


def test_api_rules_all_answer_with_uncached_envelopes(app):
    rules = [rule for rule in app.url_map.iter_rules() if rule.endpoint.startswith("api.")]
    assert len(rules) > 80
    client = app.test_client()
    for rule in rules:
        assert rule.rule.startswith("/api/")
        assert not rule.rule.startswith("/api/api/")
        values = {
            name: 1 if isinstance(converter, IntegerConverter) else "example-playlist"
            for name, converter in rule._converters.items()
        }
        path = rule.build(values)[1]
        for method in sorted(rule.methods):
            response = client.open(path, method=method)
            assert_envelope(response)
            assert response.status_code in (200, 401), (path, method, response.get_json())


@pytest.mark.parametrize("path,method,status", [
    ("/api/missing", "GET", 404),
    ("/api", "GET", 404),
    ("/api/settings", "DELETE", 405),
])
def test_api_routing_errors_use_the_contract(client, path, method, status):
    response = client.open(path, method=method)
    assert response.status_code == status
    assert_envelope(response)


def test_api_csrf_errors_use_the_contract(app):
    app.config["WTF_CSRF_ENABLED"] = True
    response = app.test_client().post("/api/settings", json={})
    assert response.status_code == 400
    assert_envelope(response)


def test_api_oversize_errors_use_the_contract(app):
    app.config["MAX_CONTENT_LENGTH"] = 8

    @app.route("/api/test-body", methods=["POST"])
    def read_body():
        request.get_data()
        return {"success": True}

    response = app.test_client().post("/api/test-body", json={"large": "x" * 100})
    assert response.status_code == 413
    assert_envelope(response)


def test_api_unexpected_errors_keep_the_contract_and_hide_details(app):
    @app.route("/api/test-failure")
    def fail():
        raise RuntimeError("private implementation detail")

    response = app.test_client().get("/api/test-failure")
    assert response.status_code == 500
    assert_envelope(response)
    assert "private implementation detail" not in response.get_data(as_text=True)


def test_api_normalization_preserves_operation_details(app):
    @app.route("/api/test-operation")
    def result():
        return {"success": False, "error_code": "playlist_busy", "message": "Busy", "result": {
            "operation_id": "example-operation", "state": "needs_attention", "receipt": {"batches_done": 2},
        }}, 409

    response = app.test_client().get("/api/test-operation")
    assert_envelope(response)
    assert response.json["result"]["receipt"] == {"batches_done": 2}
    assert response.json["error_code"] == "playlist_busy"


@pytest.mark.parametrize("endpoint,path,method,limit", [
    ("auth.login", "/login", "GET", 10),
    ("auth.callback", "/callback", "GET", 10),
    ("api.shuffle", "/api/shuffle/example-playlist", "POST", 5),
    ("api.workshop_commit", "/api/workshop/example-playlist/commit", "POST", 10),
    ("api.run_schedule_now", "/api/schedules/1/run", "POST", 5),
    ("api.playlist_setup_create_playlist", "/api/playlist/example-playlist/setup/spotify-playlists", "POST", 10),
])
def test_each_endpoint_rate_limit_is_enforced_without_running_actions(app, endpoint, path, method, limit):
    assert hasattr(app.view_functions[endpoint], "__wrapped__")
    client = app.test_client()
    # Incomplete OAuth attempts and signed-out API requests cannot reach Spotify.
    with patch("shuffify.spotify.http_client.SpotifyHTTPClient._request_url", autospec=True) as spotify_request, patch(
        "shuffify.services.AuthService.get_authenticated_api", autospec=True
    ) as authenticated_api:
        for _ in range(limit):
            assert client.open(path, method=method).status_code != 429
        response = client.open(path, method=method)
        spotify_request.assert_not_called()
        authenticated_api.assert_not_called()
    assert response.status_code == 429
    if path.startswith("/api/"):
        assert_envelope(response)


@pytest.mark.parametrize("payload", [[{"id": "example-item"}], "unexpected HTML"])
def test_api_normalization_keeps_list_data_and_rejects_non_json_success(app, payload):
    @app.route("/api/test-payload")
    def result():
        return payload

    response = app.test_client().get("/api/test-payload")
    assert_envelope(response)
    if isinstance(payload, list):
        assert response.json["data"] == payload
    else:
        assert response.status_code == 500
        assert response.json["success"] is False


def test_only_pages_auth_and_health_remain_outside_the_api(app):
    assert {rule.endpoint for rule in app.url_map.iter_rules() if not rule.rule.startswith("/api/")} == {
        "static", "auth.login", "auth.callback", "auth.logout", "site.index", "site.terms", "site.privacy",
        "site.health", "site.settings", "site.update_settings_page", "site.schedules", "site.activity",
        "site.workshop_hub", "site.workshop",
        "site.playlist_setup_page", "site.routine_setup",
    }


@pytest.mark.parametrize("value,saved,category", [("17", True, "success"), ("invalid", False, "error")])
def test_native_settings_form_redirects_with_a_flash(auth_client, db_app, value, saved, category):
    from shuffify.models.db import UserSettings

    with db_app.app_context():
        before = UserSettings.query.one().max_snapshots_per_playlist
    with patch("shuffify.routes.require_auth", return_value=object(), autospec=True), patch(
        "shuffify.routes.AuthService.get_user_data", return_value={"id": "user123"}, autospec=True
    ):
        response = auth_client.post("/settings", data={"max_snapshots_per_playlist": value})
    assert response.status_code == 302
    assert response.mimetype == "text/html"
    assert response.headers["Location"] == "/settings"
    with auth_client.session_transaction() as session:
        assert session["_flashes"][-1][0] == category
    with db_app.app_context():
        settings = UserSettings.query.first()
        assert settings.max_snapshots_per_playlist == (17 if saved else before)
