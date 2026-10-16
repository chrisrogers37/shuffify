"""Operation polling is session-authorized and never constructs a Spotify client."""

import json
from pathlib import Path

from jsonschema import Draft7Validator

from shuffify.models.db import JobExecution, User, db
from tests.paths import api_path


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
