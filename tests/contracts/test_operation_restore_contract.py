"""Actual restore review and admission responses obey the frontend contracts."""

from tests.contracts.test_page_contracts import check_response, validator
from tests.routes.test_operation_restore_routes import client
from tests.services.operations.test_restore_command import restoration as restoration
from tests.services.operations.test_routine_commands import routine as routine


def test_actual_review_request_and_queued_operation_match_generated_schemas(restoration, sample_token, monkeypatch):
    browser = client(restoration, sample_token)
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: restoration.factory(restoration.user))
    base = f"/api/operations/{restoration.parent_id}"
    body = check_response(browser.get(base + "/restore-context"), "operation-restore-context")
    request = {"reviewed": body["context"]["reviewed"]}
    assert validator("operation-restore-request").is_valid(request)
    response = browser.post(base + "/restore", json=request)
    check_response(response, "operation-accepted", 202)
    check_response(browser.get(response.headers["Location"]), "operation-context")
