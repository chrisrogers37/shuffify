"""Source review context forwards only the requested read scopes."""

from unittest.mock import patch

import pytest


@pytest.mark.parametrize(
    "query,raid,archive",
    [
        ("", True, True),
        ("?raid=1", True, False),
        ("?archive=1", False, True),
        ("?raid=1&archive=1", True, True),
        ("?raid=0&archive=0", False, False),
    ],
)
@patch("shuffify.routes.workshop.WorkshopFinalizeService")
@patch("shuffify.routes.require_auth")
def test_requested_context_scopes(auth, service, auth_client, query, raid, archive):
    body = {
        "success": True,
        "target": {"id": "p1", "name": "Target"},
        "raid": None,
        "archive": None,
    }
    service.return_value.source_context.return_value = body, 200
    response = auth_client.get(f"/workshop/p1/source-context{query}")
    assert response.status_code == 200
    assert response.json == body
    service.assert_called_once_with(auth.return_value)
    call = service.return_value.source_context.call_args
    assert call.args[0] == "p1"
    assert call.kwargs == {"raid": raid, "archive": archive}


@pytest.mark.parametrize("status", [409, 502])
@patch("shuffify.routes.workshop.WorkshopFinalizeService")
@patch("shuffify.routes.require_auth")
def test_context_failure_preserves_status(auth, service, auth_client, status):
    body = {
        "success": False,
        "message": "Could not read source",
        "error_code": "read_failed",
    }
    service.return_value.source_context.return_value = body, status
    response = auth_client.get("/workshop/p1/source-context?archive=1")
    assert response.status_code == status
    assert response.json == body


@patch("shuffify.routes.require_auth", return_value=None)
def test_context_requires_authentication(auth, db_app):
    with db_app.test_client() as client:
        response = client.get("/workshop/p1/source-context")
    assert response.status_code == 401
