"""Direct actions rejected by Flask-WTF disclose no unverified result."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tests.paths import api_path


def _csrf_post(client, path, body, headers, untouched_targets):
    """Post with a refused token; none of the named services may run."""
    with ExitStack() as stack:
        untouched = [stack.enter_context(patch(target)) for target in untouched_targets]
        response = client.post(path, json=body, headers=headers)
    for service in untouched:
        service.assert_not_called()
    return response


@pytest.mark.parametrize("path,kind", [
    (api_path("/shuffle/untrusted-target"), "shuffle"),
    (api_path("/undo/untrusted-target"), "undo"),
    (api_path("/snapshots/98765/restore"), "snapshot_restore"),
    (api_path("/playlist/untrusted-target/toggle-visibility"), "privacy"),
])
@pytest.mark.parametrize("token_state", ["missing", "expired"])
def test_direct_csrf_precedes_authorization_and_has_no_execution_receipt(
    csrf_client, csrf_headers, path, kind, token_state,
):
    response = _csrf_post(csrf_client, path, {
        "resource": "untrusted-resource",
        "resource_name": "untrusted name",
        "execution_id": 123,
        "public": True,
        "operation": {"aggregate_status": "complete"},
    }, csrf_headers(token_state), (
        "shuffify.routes.require_auth",
        "shuffify.routes.get_db_user",
        "shuffify.services.live_playlist_service.LivePlaylistService.shuffle",
        "shuffify.services.live_playlist_service.LivePlaylistService.undo",
        "shuffify.services.live_playlist_service.LivePlaylistService.restore_snapshot",
        "shuffify.services.live_metadata_service.LiveMetadataService.set_visibility",
        "shuffify.services.playlist_snapshot_service.PlaylistSnapshotService.get_snapshot",
        "shuffify.services.activity_log_service.ActivityLogService.log",
    ))
    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert body["operation"] == {
        "kind": kind,
        "aggregate_status": "partial",
        "resources": [],
        "bookkeeping": [],
        "error_code": "session_verification_expired",
        "retryable": False,
    }
    assert not {"execution_id", "started_at", "execution_state", "tracks_added",
                "tracks_total", "http_status"}.intersection(body)
    assert "This attempt did not run" in body["message"]
    assert "Reloading loses the recovery details kept on this page" in body["message"]
    assert "earlier Spotify changes remain" in body["message"]
    assert "untrusted" not in response.text


@pytest.mark.parametrize("token_state", ["missing", "expired"])
def test_review_csrf_failure_changes_and_claims_nothing(csrf_client, csrf_headers, token_state):
    response = _csrf_post(
        csrf_client, api_path("/playlist/untrusted-target/live-context"), {"kind": "undo"}, csrf_headers(token_state), (
            "shuffify.routes.require_auth",
            "shuffify.routes.get_db_user",
            "shuffify.services.live_action_context_service.LiveActionContextService.resolve",
        ),
    )
    assert response.status_code == 400
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["success"] is False
    assert body["operation"] == {
        "kind": "live_context", "aggregate_status": "partial", "resources": [], "bookkeeping": [],
        "error_code": "session_verification_expired", "retryable": False,
    }
    assert "This review did not load and nothing was changed" in body["message"]
    assert not {"execution_id", "started_at", "execution_state", "http_status", "context"} & body.keys()


@pytest.mark.parametrize("body,kind", [
    ({"kind": "tracks", "desired_track_uris": []}, "track_inspection"),
    ({"kind": "privacy", "desired_public": True}, "privacy"),
    (["not", "an", "object"], "track_inspection"),
])
@pytest.mark.parametrize("token_state", ["missing", "expired"])
def test_check_csrf_failure_is_unknown_never_a_known_no_effect(csrf_client, csrf_headers, body, kind, token_state):
    response = _csrf_post(
        csrf_client, api_path("/playlist/untrusted-target/inspect-live"), body, csrf_headers(token_state), (
        "shuffify.routes.require_auth",
        "shuffify.routes.get_db_user",
        "shuffify.services.live_playlist_service.LivePlaylistService.inspect_tracks",
        "shuffify.services.live_metadata_service.LiveMetadataService.inspect_visibility",
        ),
    )
    assert response.status_code == 400
    assert response.headers["Cache-Control"] == "no-store"
    response_body = response.get_json()
    assert response_body["inspection_only"] is True
    assert response_body["operation"] == {
        "kind": kind, "aggregate_status": "unknown", "resources": [], "bookkeeping": [],
        "error_code": "session_verification_expired", "retryable": False,
    }
    assert "This check did not run, so earlier results remain unverified" in response_body["message"]
    assert not {"execution_id", "started_at", "execution_state", "http_status"} & response_body.keys()


@pytest.mark.parametrize("path,kind,status,extra", [
    ("live-context", "live_context", "partial", {}),
    ("inspect-live", "track_inspection", "unknown", {"inspection_only": True}),
])
def test_oversized_review_and_check_bodies_keep_their_contract(auth_client, db_app, path, kind, status, extra):
    from types import SimpleNamespace

    from tests.mutable_spotify import MutableSpotify, uri

    api = MutableSpotify(SimpleNamespace(spotify_id="user123"), target=[uri(1)])
    db_app.config["MAX_CONTENT_LENGTH"] = 64
    with patch("shuffify.routes.require_auth", return_value=api):
        response = auth_client.post(api_path(f"/playlist/target/{path}"), json={
            "kind": "tracks", "desired_track_uris": [uri(1)] * 10,
        })
    assert response.status_code == 413
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["success"] is False
    assert (body["operation"]["kind"], body["operation"]["aggregate_status"]) == (kind, status)
    assert body["operation"]["error_code"] == "request_too_large"
    assert body["operation"]["retryable"] is False
    assert {key: body[key] for key in extra} == extra
    assert api.writes == []
