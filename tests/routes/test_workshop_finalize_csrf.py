"""Real Flask-WTF rejections retain source certainty without running a step."""

from unittest.mock import Mock, patch

import pytest

from shuffify.services.workshop_finalize_service import WorkshopFinalizeResult
from tests.paths import api_path

A = "spotify:track:" + "a" * 22
FINALIZERS = [
    ("pending-raids/finalize", "promoted_count", "raid_panel", "finalize_raids"),
    ("pending-raids/finalize-all", "promoted_count", "raid_panel", "finalize_raids"),
    ("pair/archive", "archived_count", "playlist_pairs", "append_archive"),
    ("pair/finalize-restore", "restored_count", "playlist_pairs", "finalize_restore"),
]


def captured_payload(inspect_only):
    return {
        "source_playlist_id": "source123",
        "target_track_uris": [A],
        "track_ids": [1],
        "track_uris": [A],
        "baseline_track_uris": [],
        "inspect_only": inspect_only,
    }


@pytest.mark.parametrize("path,count_key,module,method", FINALIZERS)
@pytest.mark.parametrize("token_state", ["missing", "expired"])
@pytest.mark.parametrize("inspect_only", [False, True])
def test_actual_csrf_rejection_preserves_source_contract(
    csrf_client,
    csrf_headers,
    caplog,
    path,
    count_key,
    module,
    method,
    token_state,
    inspect_only,
):
    with (
        patch("shuffify.routes.require_auth") as auth,
        patch(f"shuffify.routes.{module}.WorkshopFinalizeService") as service,
        patch(
            "shuffify.services.activity_log_service.ActivityLogService.log"
        ) as activity,
        patch(
            "shuffify.services.pending_raid_service.PendingRaidService.complete_finalize"
        ) as transition,
    ):
        response = csrf_client.post(
            api_path(f"/playlist/target/{path}"),
            json=captured_payload(inspect_only),
            headers=csrf_headers(token_state),
        )
    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert body["resource"] == "source123"
    assert body["status"] == ("unknown" if inspect_only else "failed")
    assert body["retryable"] is False
    assert body["error_code"] == "session_verification_expired"
    assert body["affected_count"] == (None if inspect_only else 0)
    assert body[count_key] == (None if inspect_only else 0)
    assert "Reloading loses the recovery details kept on this page" in body["message"]
    assert "earlier Spotify changes remain" in body["message"]
    assert (
        "This check did not run"
        if inspect_only
        else "This attempt applied no source changes"
    ) in body["message"]
    expected_reason = (
        "token is missing" if token_state == "missing" else "token has expired"
    )
    assert any(
        expected_reason in record.getMessage()
        for record in caplog.records
        if record.name == "shuffify.error_handlers"
    )
    auth.assert_not_called()
    service.assert_not_called()
    transition.assert_not_called()
    activity.assert_not_called()


@pytest.mark.parametrize("path,count_key,module,method", FINALIZERS)
def test_valid_csrf_allows_readonly_finalizer_inspection(
    csrf_client,
    csrf_headers,
    path,
    count_key,
    module,
    method,
):
    expected = WorkshopFinalizeResult(
        "source123", "Source", "not_required", "Already verified", extra={count_key: 1}
    )
    with (
        patch("shuffify.routes.is_authenticated", return_value=True),
        patch("shuffify.routes.require_auth", return_value=Mock()),
        patch("shuffify.routes.get_db_user", return_value=Mock(id=1)),
        patch(f"shuffify.routes.{module}.WorkshopFinalizeService") as service,
    ):
        finalize = getattr(service.return_value, method)
        finalize.return_value = expected
        response = csrf_client.post(
            api_path(f"/playlist/target/{path}"),
            json=captured_payload(True),
            headers=csrf_headers("valid"),
        )
    assert response.status_code == 200
    assert response.get_json() == expected.to_dict()
    finalize.assert_called_once()


@pytest.mark.parametrize(
    "path",
    [
        api_path("/workshop/target/commit"),
        api_path("/playlist/target/pending-raids/dismiss"),
        api_path("/playlist/target/pair/unarchive"),
    ],
)
@pytest.mark.parametrize("token_state", ["missing", "expired"])
def test_other_json_routes_keep_generic_csrf_response(
    csrf_client, csrf_headers, path, token_state
):
    response = csrf_client.post(
        path, json=captured_payload(False), headers=csrf_headers(token_state)
    )
    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "category": "error",
        "error_code": "invalid_request",
        "message": "Your request could not be verified. Please refresh the page and try again.",
    }


def test_generic_form_csrf_still_renders_html(csrf_client):
    response = csrf_client.post("/logout", headers={"Accept": "text/html"})
    assert response.status_code == 400
    assert response.content_type.startswith("text/html")
    assert b"Your request could not be verified" in response.data
