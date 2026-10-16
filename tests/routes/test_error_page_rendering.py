"""Backend failures preserve JSON, HTTP status, protocol headers, and session facts."""

import pytest
from flask import abort
from flask_wtf.csrf import CSRFError


@pytest.fixture
def error_app(db_app):
    db_app.config["PROPAGATE_EXCEPTIONS"] = False
    db_app.testing = False
    return db_app


@pytest.mark.parametrize("accept", [None, "*/*", "text/html", "application/json"])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 413, 429, 500, 503])
def test_http_error_contract_is_independent_of_browser_accept(error_app, status, accept):
    @error_app.route("/test-http-error")
    def fail():
        abort(status)

    headers = {"Accept": accept} if accept else {}
    response = error_app.test_client().get("/test-http-error", headers=headers)
    assert response.status_code == status
    assert response.is_json
    assert response.json["success"] is False
    assert response.json["message"]
    assert response.json["error_code"]
    assert response.json["category"] == "error"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path,headers", [
    ("/test-failure", {"Accept": "text/html"}),
    ("/api/test-failure", {"Accept": "application/json"}),
    ("/test-failure", {"X-Requested-With": "XMLHttpRequest"}),
])
def test_unexpected_errors_are_bounded_json_and_preserve_signed_in_session(error_app, path, headers):
    @error_app.route(path)
    def fail():
        raise RuntimeError("private implementation detail")

    with error_app.test_client() as client:
        with client.session_transaction() as session:
            session["user_data"] = {"id": "unchanged-owner"}
        response = client.get(path, headers=headers)
        with client.session_transaction() as session:
            assert session["user_data"]["id"] == "unchanged-owner"
    assert response.status_code == 500
    assert response.json["success"] is False
    assert "private implementation detail" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("headers", [{"Accept": "text/html"}, {"X-Requested-With": "XMLHttpRequest"}])
def test_csrf_failure_is_actionable_json_for_every_request_kind(error_app, headers):
    @error_app.route("/test-csrf", methods=["POST"])
    def fail_csrf():
        raise CSRFError("The CSRF token is missing.")

    response = error_app.test_client().post("/test-csrf", headers=headers)
    assert response.status_code == 400
    assert response.json["success"] is False
    assert "could not be verified" in response.json["message"]
    assert response.headers["Cache-Control"] == "no-store"


def test_method_error_preserves_allow_header(error_app):
    response = error_app.test_client().post("/health")
    assert response.status_code == 405
    assert response.is_json
    assert {"GET", "HEAD", "OPTIONS"} <= {method.strip() for method in response.headers["Allow"].split(",")}
