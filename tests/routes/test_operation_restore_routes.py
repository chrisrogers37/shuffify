"""Owned review reads and local-only idempotent restoration admission."""

import pytest

from shuffify.models.db import JobExecution, User, db
from shuffify.services.operations.commands.registry import COMMAND_HANDLERS
from shuffify.worker import OperationWorker
from tests.services.operations.test_restore_command import restoration as restoration
from tests.services.operations.test_routine_commands import routine as routine


def client(case, token):
    result = case.app.test_client()
    with result.session_transaction() as session:
        session["spotify_token"] = token
        session["user_data"] = {"id": case.user.spotify_id}
    return result


def test_owned_review_then_local_only_acceptance_replay_and_worker_completion(restoration, sample_token, monkeypatch):
    browser = client(restoration, sample_token)
    base = f"/api/operations/{restoration.parent_id}"
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: restoration.factory(restoration.user))
    context = browser.get(base + "/restore-context")
    assert context.status_code == 200
    assert context.json["context"]["operation_id"] == restoration.parent_id
    assert restoration.world.writes == []

    def forbidden(*args, **kwargs):
        pytest.fail("Restoration admission must be local-only")

    with monkeypatch.context() as admission:
        admission.setattr("shuffify.routes.require_auth", forbidden)
        admission.setattr("shuffify.spotify.api.SpotifyAPI.__init__", forbidden)
        accepted = browser.post(
            base + "/restore",
            json={"reviewed": context.json["context"]["reviewed"]},
            headers={"Idempotency-Key": "reviewed-restore"},
        )
    assert accepted.status_code == 202
    row = restoration.repository.get(accepted.json["operation_id"])
    assert row.targets == ["target"] and row.kind == "operation_restore"
    assert restoration.world.writes == []
    worker = OperationWorker(
        restoration.app,
        repository=restoration.repository,
        handlers=COMMAND_HANDLERS,
        api_factory=restoration.factory,
        heartbeat_interval=0,
    )
    assert worker.run_once(row.id)
    assert browser.get(accepted.headers["Location"]).json["operation"]["state"] == "succeeded"
    replay = browser.post(
        base + "/restore",
        json={"reviewed": context.json["context"]["reviewed"]},
        headers={"Idempotency-Key": "reviewed-restore"},
    )
    assert replay.status_code == 202 and replay.json["operation_id"] == row.id
    refused = browser.post(
        base + "/restore",
        json={"reviewed": context.json["context"]["reviewed"]},
        headers={"Idempotency-Key": "another-restore"},
    )
    assert refused.status_code == 409 and refused.json["operation_id"] == row.id
    assert JobExecution.query.count() == 2 and len(restoration.world.writes) == 1
    parent = browser.get(base).json["operation"]
    assert parent["state"] == "needs_attention"
    assert parent["recovery"]["available"] is False and parent["recovery"]["resolved_operation_id"] == row.id


def test_other_owners_parent_is_404_before_provider_construction(restoration, sample_token, monkeypatch):
    other = User(spotify_id="other-owner")
    db.session.add(other)
    db.session.commit()
    parent = db.session.get(JobExecution, restoration.parent_id)
    parent.user_id = other.id
    db.session.commit()
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: pytest.fail("Owner check must happen first"))
    browser = client(restoration, sample_token)
    for suffix, method in [("restore-context", "get"), ("restore", "post")]:
        response = getattr(browser, method)(f"/api/operations/{parent.id}/{suffix}", json={})
        assert response.status_code == 404 and response.json["error_code"] == "operation_not_found"
    assert restoration.world.writes == []


def test_restoration_child_refuses_nested_review_and_admission_before_provider_access(
    restoration, sample_token, monkeypatch
):
    reviewed = restoration.review_restore()
    parent = db.session.get(JobExecution, restoration.parent_id)
    parent.kind = "operation_restore"
    db.session.commit()
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: pytest.fail("Nested restoration must refuse locally"))
    browser = client(restoration, sample_token)
    base = f"/api/operations/{parent.id}"
    for response in [
        browser.get(base + "/restore-context"),
        browser.post(base + "/restore", json={"reviewed": reviewed}),
    ]:
        assert response.status_code == 409
        assert response.json["error_code"] == "nested_restoration_unavailable"
    projected = browser.get(base).json["operation"]
    assert projected["recovery"]["available"] is False
    assert projected["recovery"]["reason"] == "nested_restoration_unavailable"
    assert JobExecution.query.count() == 1 and restoration.world.writes == []


@pytest.mark.parametrize(
    "body", [{}, {"desired": []}, {"reviewed": {"version": True}}, {"reviewed": {}, "track_uris": []}]
)
def test_restore_never_accepts_client_desired_lists_or_partial_review(restoration, sample_token, body):
    response = client(restoration, sample_token).post(f"/api/operations/{restoration.parent_id}/restore", json=body)
    assert response.status_code == 400
    assert JobExecution.query.count() == 1 and restoration.world.writes == []


@pytest.mark.parametrize("token_state", ["missing", "expired"])
def test_restore_enforces_csrf_before_capture(csrf_client, csrf_headers, token_state, monkeypatch):
    monkeypatch.setattr("shuffify.routes.queue.enqueue_command", lambda *args: pytest.fail("CSRF must run first"))
    response = csrf_client.post("/api/operations/1/restore", json={}, headers=csrf_headers(token_state))
    assert response.status_code == 400


def test_unknown_child_blocks_fresh_key_and_review_but_same_key_replays_its_id(restoration, sample_token, monkeypatch):
    from copy import deepcopy

    from sqlalchemy.orm import Session

    browser = client(restoration, sample_token)
    base = f"/api/operations/{restoration.parent_id}"
    reviewed = restoration.review_restore()
    accepted = browser.post(base + "/restore", json={"reviewed": reviewed}, headers={"Idempotency-Key": "one-attempt"})
    assert accepted.status_code == 202
    child_id = accepted.json["operation_id"]
    original = restoration.repository.get(restoration.parent_id).journal
    journal = deepcopy(original)
    journal["plans"][0]["batches"][0].update(state="dispatched", attempts=1)
    with Session(restoration.repository.engine) as storage, storage.begin():
        child = storage.get(JobExecution, child_id)
        child.state, child.journal = "needs_attention", journal
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: pytest.fail("Unknown child must block before Spotify"))
    replay = browser.post(base + "/restore", json={"reviewed": reviewed}, headers={"Idempotency-Key": "one-attempt"})
    assert replay.status_code == 202 and replay.json["operation_id"] == child_id
    for response in [
        browser.get(base + "/restore-context"),
        browser.post(base + "/restore", json={"reviewed": reviewed}, headers={"Idempotency-Key": "fresh-attempt"}),
    ]:
        assert response.status_code == 409 and response.json["operation_id"] == child_id
        assert response.json["error_code"] == "restoration_attempt_unresolved"
    public = browser.get(base).json["operation"]["recovery"]
    assert public["available"] is False and public["restoration_operation_id"] == child_id
    assert JobExecution.query.count() == 2 and restoration.world.writes == []


@pytest.mark.parametrize(
    "failure,status,code",
    [
        ("rate", 429, "rate_limited"),
        ("auth", 401, "authentication_required"),
        ("access", 403, "restore_access_changed"),
        ("unsupported", 422, "unsupported_playlist_items"),
        ("unstable", 409, "restore_read_changed"),
        ("unexpected", 502, "restore_read_failed"),
    ],
)
def test_review_read_failures_are_authored_and_preserve_provider_retry_hint(
    restoration,
    sample_token,
    monkeypatch,
    failure,
    status,
    code,
):
    from shuffify.services.playlist_service import PlaylistAccessError
    from shuffify.services.workshop_commit_service import IncompletePlaylistRead, UnsupportedPlaylistItems
    from shuffify.spotify.exceptions import SpotifyAuthError, SpotifyRateLimitError

    errors = {
        "rate": SpotifyRateLimitError("private provider text", retry_after=17),
        "auth": SpotifyAuthError("private auth text"),
        "access": PlaylistAccessError("private owner"),
        "unsupported": UnsupportedPlaylistItems("private item"),
        "unstable": IncompletePlaylistRead("private read"),
        "unexpected": ValueError("private implementation"),
    }

    def fail(*args, **kwargs):
        raise errors[failure]

    monkeypatch.setattr("shuffify.routes.require_auth", lambda: object())
    monkeypatch.setattr("shuffify.services.operations.restore_review.read_tracks", fail)
    response = client(restoration, sample_token).get(f"/api/operations/{restoration.parent_id}/restore-context")
    assert response.status_code == status and response.json["error_code"] == code
    assert "private" not in str(response.json)
    if failure == "rate":
        assert response.headers["Retry-After"] == "17" and response.json["retry_after"] == 17
    assert restoration.world.writes == [] and JobExecution.query.count() == 1


def test_scope_limit_refuses_context_and_admission_before_any_provider_access(restoration, sample_token, monkeypatch):
    reviewed = restoration.review_restore()
    row = db.session.get(JobExecution, restoration.parent_id)
    row.targets = sorted(["target", *(f"guard{index}" for index in range(100))])
    db.session.commit()
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: pytest.fail("Oversized scope must refuse locally"))
    browser = client(restoration, sample_token)
    base = f"/api/operations/{row.id}"
    for response in [
        browser.get(base + "/restore-context"),
        browser.post(base + "/restore", json={"reviewed": reviewed}),
    ]:
        assert response.status_code == 409 and response.json["error_code"] == "restore_scope_too_large"
    assert JobExecution.query.count() == 1 and restoration.world.writes == []


def test_worker_recapture_enforces_scope_limit_without_provider_access(restoration):
    row = restoration.enqueue_restore()
    parent = db.session.get(JobExecution, restoration.parent_id)
    parent.targets = sorted(["target", *(f"guard{index}" for index in range(100))])
    db.session.commit()
    finished, completed = restoration.run_restore(row, lambda user: pytest.fail("Oversized scope must refuse locally"))
    assert finished and completed.state == "failed"
    assert completed.journal["result"]["error_code"] == "restore_scope_too_large"
    assert restoration.world.writes == []
