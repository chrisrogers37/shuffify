"""Legacy form adapters exercised with complete mutable Spotify responses."""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from shuffify.services.state_service import StateService
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyTokenExpiredError,
    SpotifyWriteRejectedError,
    SpotifyWriteUnverifiedError,
)
from tests.paths import api_path
from tests.queued_operations import post_and_run, run_operation, scoped_history
from tests.services.test_live_executor_outcomes import MutableSpotify, uri


@pytest.fixture
def api():
    return MutableSpotify(SimpleNamespace(spotify_id="user123"), playlist123=[uri(1), uri(2)])


@pytest.fixture(autouse=True)
def snapshots():
    with patch("shuffify.services.live_playlist_service.PlaylistSnapshotService.auto_snapshot_if_enabled"):
        yield


@pytest.mark.parametrize("endpoint", ["shuffle", "undo"])
def test_unauth_returns_direct_outcome_without_identity(db_app, endpoint):
    with patch("shuffify.routes.require_auth", return_value=None), db_app.test_client() as client:
        response = client.post(api_path(f"/{endpoint}/playlist123"))
    data = response.get_json()
    assert response.status_code == 401
    assert data["operation"]["resources"] == []
    assert data["operation"]["kind"] == endpoint
    assert not {"execution_id", "started_at", "execution_state"} & data.keys()


def test_shuffle_rejects_non_editable_playlist(auth_client, api):
    api.owner = "someone_else"
    with patch("shuffify.routes.require_auth", return_value=api):
        response = post_and_run(auth_client, api, api_path("/shuffle/playlist123"), data={"algorithm": "BasicShuffle"})
    assert response.status_code == 200
    assert response.operation["state"] == "failed"
    assert response.get_json()["operation"]["aggregate_status"] == "partial"
    assert api.writes == []


def test_successful_form_shuffle_preserves_legacy_fields_and_parameters(auth_client, api):
    with (
        patch("shuffify.routes.require_auth", return_value=api),
        patch(
            "shuffify.services.live_playlist_service.ShuffleService.execute", return_value=[uri(2), uri(1)]
        ) as execute,
    ):
        response = post_and_run(auth_client, api,
            api_path("/shuffle/playlist123"), data={"algorithm": "BasicShuffle", "keep_first": "1"},
        )
    data = response.get_json()
    assert response.status_code == 200
    assert data["success"] is True
    assert data["playlist"]["id"] == "playlist123"
    assert data["playlist_state"]["current_index"] == 1
    assert data["operation"]["aggregate_status"] == "complete"
    assert execute.call_args.args[2] == {"keep_first": 1}
    assert api.writes == [("update", "playlist123", [uri(2), uri(1)])]


def test_nochange_shuffle_is_verified_informational_success(auth_client, api):
    with (
        patch("shuffify.routes.require_auth", return_value=api),
        patch("shuffify.services.live_playlist_service.ShuffleService.execute", return_value=[uri(1), uri(2)]),
    ):
        response = post_and_run(auth_client, api, api_path("/shuffle/playlist123"), data={"algorithm": "BasicShuffle"})
    data = response.get_json()
    assert response.status_code == 200
    assert data["success"] is True
    assert data["category"] == "info"
    assert data["operation"]["resources"][0]["status"] == "not_required"
    assert "did not change" in data["message"]
    with auth_client.session_transaction() as session:
        assert "playlist_states" not in session
    assert api.writes == []


@pytest.mark.parametrize("body", [{"algorithm": "NotReal"}, {"algorithm": "BasicShuffle", "keep_first": "invalid"}])
def test_invalid_form_has_known_nonretryable_direct_rejection(auth_client, api, body):
    with patch("shuffify.routes.require_auth", return_value=api):
        response = post_and_run(auth_client, api, api_path("/shuffle/playlist123"), data=body)
    assert response.status_code == 400
    assert response.get_json()["operation"]["retryable"] is False
    assert api.writes == []


def seed_three_states(client):
    states = [[uri(3)], [uri(2)], [uri(1), uri(2)]]
    with client.session_transaction() as session:
        StateService.initialize_playlist_state(session, "playlist123", states[0])
        StateService.record_new_state(session, "playlist123", states[1])
        StateService.record_new_state(session, "playlist123", states[2])
    return states


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize(
    ("error_type", "status"),
    [
        (SpotifyWriteRejectedError, 502),
        (SpotifyRateLimitError, 429),
        (SpotifyQuotaExceededError, 429),
        (SpotifyForbiddenError, 403),
        (SpotifyNotFoundError, 404),
        (SpotifyTokenExpiredError, 401),
    ],
)
def test_rejected_undo_preserves_exact_revision_for_explicit_retry(auth_client, api, error_type, status, batched):
    states = seed_three_states(auth_client)
    rejection = error_type("private provider rejection")
    error = SpotifyPartialBatchError("playlist123", "update", 0, 1, [], states[1], rejection) if batched else rejection

    def rejected(fake, playlist_id, uris):
        del fake.faults[("update", playlist_id)]
        raise error

    api.faults[("update", "playlist123")] = rejected
    with patch("shuffify.routes.require_auth", return_value=api):
        response = post_and_run(auth_client, api, api_path("/undo/playlist123"))
        assert response.status_code == 200
        if status == 429:
            assert response.operation["state"] == "running"
            assert response.get_json() is None
            assert scoped_history(auth_client, "playlist123")["current_index"] == 2
            assert api.writes == [("update", "playlist123", states[1])]
            time.sleep(1.05)
            response = run_operation(auth_client, api, response.operation["id"], response.accepted)
            assert response.operation["state"] == "succeeded"
            assert response.get_json()["playlist_state"]["current_index"] == 1
            assert api.writes == [("update", "playlist123", states[1]), ("update", "playlist123", states[1])]
            return
        data = response.get_json()
        assert data["success"] is False
        assert data["operation"]["aggregate_status"] == "partial"
        assert data["operation"]["retryable"] is True
        assert data["operation"]["resources"][0]["status"] == "failed"
        assert data["operation"]["resources"][0]["affected_count"] == 0
        assert data["operation"]["resources"][0]["dispatched"] is True
        assert "private" not in str(data)
        with auth_client.session_transaction() as session:
            assert StateService.get_state_info(session, "playlist123")["current_index"] == 2
        response = post_and_run(auth_client, api, api_path("/undo/playlist123"))
    assert response.status_code == 200
    assert response.get_json()["playlist_state"]["current_index"] == 1
    assert api.writes == [("update", "playlist123", states[1]), ("update", "playlist123", states[1])]


@pytest.mark.parametrize(
    "error",
    [
        SpotifyWriteUnverifiedError("Response lost"),
        SpotifyAPIError("Outcome unknown"),
        SpotifyPartialBatchError("playlist123", "update", 0, 1, [], [], SpotifyWriteUnverifiedError("lost")),
        SpotifyPartialBatchError("playlist123", "update", 1, 2, [], [], SpotifyRateLimitError("later rejection")),
    ],
)
def test_uncertain_undo_never_consumes_or_replays_history(auth_client, api, error):
    seed_three_states(auth_client)

    def unknown(*args):
        raise error

    api.faults[("update", "playlist123")] = unknown
    with patch("shuffify.routes.require_auth", return_value=api):
        response = post_and_run(auth_client, api, api_path("/undo/playlist123"))
    assert response.status_code == 200
    assert response.operation["state"] == "needs_attention"
    assert response.get_json()["success"] is False
    assert response.get_json()["operation"]["aggregate_status"] == "unknown"
    with auth_client.session_transaction() as session:
        assert StateService.get_state_info(session, "playlist123")["current_index"] == 2
    assert len(api.writes) == 1
