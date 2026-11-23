"""Operation admission and polling use the session and isolated queue."""

import json
from pathlib import Path

from jsonschema import Draft7Validator

from shuffify.models.db import JobExecution, User, db
from tests.paths import api_path
from tests.queued_operations import run_operation
from tests.services.test_live_metadata_service import MetadataSpotify


def check_contract(payload):
    schema = Path(__file__).resolve().parents[2] / "web/schemas/operation-context.schema.json"
    Draft7Validator(json.loads(schema.read_text())).validate(payload)


def test_operation_poll_requires_sign_in(client):
    response = client.get(api_path("/operations/1"))
    assert response.status_code == 401
    assert response.get_json()["error_code"] == "authentication_required"
    assert response.headers["Cache-Control"] == "no-store"


def test_operation_poll_is_owned_and_omits_private_storage(auth_client, db_app, monkeypatch):
    def forbidden_spotify(*args, **kwargs):
        raise AssertionError("Polling must not construct or call Spotify")

    monkeypatch.setattr("shuffify.services.AuthService.get_authenticated_api", forbidden_spotify)
    with db_app.app_context():
        user = User.query.filter_by(spotify_id="user123").one()
        owned = JobExecution(
            user_id=user.id, kind="shuffle", targets=["example-playlist"], state="queued", status="queued",
            payload={"private_command": "example-payload"},
            journal={"baseline": ["private-track-order"]}, lease_owner="private-claim-token",
        )
        foreign = JobExecution(user_id=user.id + 100, kind="shuffle", state="queued", status="queued")
        legacy = JobExecution(status="success")
        db.session.add_all([owned, foreign, legacy])
        db.session.commit()
        owned_id, foreign_id, legacy_id = owned.id, foreign.id, legacy.id
    response = auth_client.get(api_path(f"/operations/{owned_id}"))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    check_contract(body)
    assert body["success"] is True
    assert body["operation"]["id"] == owned_id
    assert body["operation"]["state"] == "queued"
    assert body["operation"]["started_at"] is None
    assert "private" not in response.get_data(as_text=True)
    for operation_id in (foreign_id, legacy_id, 99999):
        denied = auth_client.get(api_path(f"/operations/{operation_id}"))
        assert denied.status_code == 404
        assert denied.get_json()["success"] is False


def test_operation_poll_returns_stored_terminal_result_without_spotify(auth_client, db_app, monkeypatch):
    monkeypatch.setattr("shuffify.services.AuthService.get_authenticated_api", lambda *a: (_ for _ in ()).throw(
        AssertionError("Polling must not use Spotify")
    ))
    with db_app.app_context():
        user = User.query.filter_by(spotify_id="user123").one()
        operation = JobExecution(
            user_id=user.id, kind="workshop_commit", targets=["example-playlist"], state="succeeded",
            status="success", journal={"result": {"success": True, "target_status": "applied"}},
        )
        db.session.add(operation)
        db.session.commit()
        operation_id = operation.id
    response = auth_client.get(api_path(f"/operations/{operation_id}"))
    assert response.status_code == 200
    check_contract(response.get_json())
    assert response.get_json()["operation"]["result"] == {"success": True, "target_status": "applied"}


def test_outstanding_cap_is_per_user_and_releases_capacity_on_completion(auth_client, db_app, sample_token):
    db.session.add(User(spotify_id="second-user"))
    db.session.commit()
    other_client = db_app.test_client()
    with other_client.session_transaction() as session:
        session["spotify_token"] = sample_token
        session["user_data"] = {"id": "second-user"}

    def submit(client, target, key, *, public=True):
        return client.post(api_path(f"/playlist/{target}/toggle-visibility"),
                           headers={"Idempotency-Key": key}, json={"public": public})

    accepted = [submit(auth_client, f"playlist{i}", f"key-{i}") for i in range(5)]
    assert all(response.status_code == 202 for response in accepted), [response.json for response in accepted]
    first_id = accepted[0].get_json()["operation_id"]
    blocked = submit(auth_client, "overflow", "overflow")
    assert blocked.status_code == 429
    error = blocked.get_json()
    assert error["error_code"] == "outstanding_operation_limit"
    assert error["retryable"] is True
    assert "wait" in error["message"].lower() and "finish" in error["message"].lower()
    assert "operation_id" not in error and "Location" not in blocked.headers
    assert JobExecution.query.count() == 5

    replay = submit(auth_client, "playlist0", "key-0")
    assert replay.status_code == 202 and replay.get_json()["operation_id"] == first_id
    conflict = submit(auth_client, "playlist0", "key-0", public=False)
    assert conflict.status_code == 409 and conflict.get_json()["error_code"] == "request_key_conflict"

    other = submit(other_client, "otherplaylist", "key-0")
    assert other.status_code == 202
    other_spotify = MetadataSpotify()
    other_spotify.owner = "second-user"
    other_result = run_operation(other_client, other_spotify, other.get_json()["operation_id"], other)
    assert other_result.operation["state"] == "succeeded"
    assert other_spotify.public is True
    assert submit(auth_client, "overflow", "overflow").status_code == 429

    first_spotify = MetadataSpotify()
    assert run_operation(auth_client, first_spotify, first_id, accepted[0]).operation["state"] == "succeeded"
    assert first_spotify.public is True
    retry = submit(auth_client, "overflow", "overflow")
    assert retry.status_code == 202 and retry.get_json()["operation_id"] != first_id
    terminal_replay = submit(auth_client, "playlist0", "key-0")
    assert terminal_replay.status_code == 202 and terminal_replay.get_json()["operation_id"] == first_id
    assert JobExecution.query.count() == 7
