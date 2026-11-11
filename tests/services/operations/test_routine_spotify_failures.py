"""A routine run that Spotify refuses because of the app, the grant or the account is named as such.

The worker builds its Spotify client from the user's stored refresh token, so a refresh that fails
reaches the routine handler wrapped in JobExecutionError. The run still ends `failed`, now with the
error code of the typed failure behind it instead of the generic one.
"""

import json

import pytest
import requests

from shuffify.models.db import User, db
from shuffify.services.token_service import TokenService
from shuffify.worker import OperationWorker
from tests.services.operations.test_routine_commands import handlers
from tests.services.operations.test_routine_commands import routine as routine


def response(status, body):
    value = requests.Response()
    value.status_code, value._content = status, json.dumps(body).encode()
    return value


@pytest.fixture
def stored_token(routine):
    """The owner has a stored refresh token, as signing in leaves one."""
    user = db.session.get(User, routine.user.id)
    user.encrypted_refresh_token = TokenService.encrypt_token("fake-refresh")
    db.session.commit()


def worker_without_a_factory(routine):
    """The worker as production runs it: its client comes from the stored refresh token."""
    return OperationWorker(routine.app, repository=routine.repository, handlers=handlers(), heartbeat_interval=0)


def refuse_the_refresh(monkeypatch, status, body):
    posts = []

    def post(*args, **kwargs):
        posts.append(kwargs.get("data", {}).get("grant_type"))
        return response(status, body)

    monkeypatch.setattr("shuffify.spotify.auth.requests.post", post)
    monkeypatch.setattr("requests.sessions.Session.request", lambda self, method, url, **kwargs: post(url, **kwargs))
    return posts


@pytest.mark.parametrize(
    "body, code",
    [
        ({"error": "invalid_client", "error_description": "Invalid client"}, "spotify_app_rejected"),
        ({"error": "invalid_grant", "error_description": "Refresh token revoked"}, "spotify_grant_revoked"),
    ],
)
def test_a_run_whose_refresh_is_refused_ends_failed_with_the_typed_error_code(
    routine, stored_token, monkeypatch, body, code
):
    posts = refuse_the_refresh(monkeypatch, 400, body)
    row, _ = routine.enqueue("raid", direct=True)

    assert worker_without_a_factory(routine).run_once(row.id) is True

    completed = routine.repository.get(row.id)
    assert completed.state == "failed"
    result = completed.journal["result"]
    assert result["operation"]["error_code"] == code
    assert result["success"] is False
    assert posts == ["refresh_token"]
    assert routine.world.writes == []


def test_a_scheduled_run_is_named_the_same_way(routine, stored_token, monkeypatch):
    refuse_the_refresh(monkeypatch, 400, {"error": "invalid_client", "error_description": "Invalid client"})
    row, _ = routine.enqueue("shuffle", origin="scheduler")

    assert worker_without_a_factory(routine).run_once(row.id) is True

    completed = routine.repository.get(row.id)
    assert completed.state == "failed"
    assert completed.journal["result"]["operation"]["error_code"] == "spotify_app_rejected"


def refuse_every_write(routine, error):
    def refuse(method, path, body):
        raise error

    routine.world.dispatch = refuse


def test_a_403_that_says_the_user_is_not_registered_is_named_too(routine):
    from shuffify.spotify.exceptions import SpotifyUserNotAllowed

    refuse_every_write(routine, SpotifyUserNotAllowed("API error 403: User not registered in the Developer Dashboard"))
    row, _ = routine.enqueue("shuffle")

    completed = routine.run(row)

    assert completed.state in {"failed", "rolled_back"}
    assert completed.journal["result"]["operation"]["error_code"] == "spotify_user_not_allowed"


def test_a_playlist_403_stays_a_write_rejection(routine):
    from shuffify.spotify.exceptions import SpotifyForbiddenError

    refuse_every_write(routine, SpotifyForbiddenError("API error 403: Forbidden"))
    row, _ = routine.enqueue("shuffle")

    completed = routine.run(row)

    assert completed.state in {"failed", "rolled_back"}
    assert completed.journal["result"]["operation"]["error_code"] == "write_rejected"
