"""Real expired-token construction distinguishes temporary endpoint outages from invalid credentials."""

import json

import pytest
import requests
from sqlalchemy.orm import Session

from shuffify.models.db import User
from shuffify.services.token_service import TokenService
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import SpotifyReadTransientError, SpotifyTokenError
from shuffify.worker import OperationWorker
from tests.services.operations.test_worker_finalization import terminal as terminal


def response(status, body):
    value = requests.Response()
    value.status_code, value._content = status, json.dumps(body).encode()
    return value


@pytest.mark.parametrize('failure', [500, 503, requests.ConnectionError, requests.Timeout])
def test_expired_refresh_network_and_server_failures_are_transient(monkeypatch, failure):
    manager = SpotifyAuthManager(SpotifyCredentials('fake-id', 'fake-secret', 'http://localhost/callback'))

    def post(*args, **kwargs):
        if isinstance(failure, type):
            raise failure('Private transport detail')
        return response(failure, {'error_description': 'Private server detail'})

    monkeypatch.setattr('shuffify.spotify.auth.requests.post', post)
    monkeypatch.setattr('requests.sessions.Session.request', lambda self, method, url, **kwargs: post(url, **kwargs))
    with pytest.raises(SpotifyReadTransientError) as caught:
        manager.refresh_token(TokenInfo('expired', 'Bearer', 0, refresh_token='fake-refresh'))
    assert 'Private' not in str(caught.value)


def test_a_real_expired_worker_token_503_defers_then_reuses_the_same_operation(db_app, terminal, monkeypatch):
    with Session(terminal.repository.engine) as storage, storage.begin():
        storage.get(User, terminal.user_id).encrypted_refresh_token = TokenService.encrypt_token('fake-refresh')
    responses = [response(503, {'error_description': 'Temporary outage'}), response(200, {
        'access_token': 'fresh-test-access', 'token_type': 'Bearer', 'expires_in': 3600,
    })]
    refreshes = []

    def post(*args, **kwargs):
        refreshes.append(True)
        return responses.pop(0)

    monkeypatch.setattr('shuffify.spotify.auth.requests.post', post)
    monkeypatch.setattr('requests.sessions.Session.request', lambda self, method, url, **kwargs: post(url, **kwargs))

    def handler(runtime):
        assert runtime.api._token_info.access_token == 'fresh-test-access'
        return {'success': True}

    def worker():
        return OperationWorker(
            db_app, repository=terminal.repository, handlers={'example': handler}, heartbeat_interval=0,
        )

    row = terminal.accept()
    assert worker().run_once(row.id) is False
    pending = terminal.repository.get(row.id)
    assert pending.state == 'running' and pending.completed_at is None
    assert pending.journal['read_retry']['next_attempt_at']
    assert len(refreshes) == 1 and terminal.world.calls == 0
    from tests.services.operations.test_read_deferral_budget import make_due

    make_due(terminal, row.id)
    assert worker().run_once(row.id) is True
    assert terminal.repository.get(row.id).state == 'succeeded'
    assert len(refreshes) == 2 and terminal.world.calls == 0


def test_invalid_grant_remains_an_auth_failure(monkeypatch):
    manager = SpotifyAuthManager(SpotifyCredentials('fake-id', 'fake-secret', 'http://localhost/callback'))
    monkeypatch.setattr(
        'shuffify.spotify.auth.requests.post', lambda *a, **kw: response(400, {'error': 'invalid_grant'}),
    )
    with pytest.raises(SpotifyTokenError):
        manager.refresh_token(TokenInfo('expired', 'Bearer', 0, refresh_token='fake-refresh'))


def test_refresh_503_during_terminal_verification_defers_without_attention_or_replay(db_app, terminal, monkeypatch):
    from shuffify.services.operations.runtime import OperationRuntime
    from tests.services.operations.test_read_deferral_budget import make_due
    from tests.services.operations.test_worker_finalization import Handler

    row, handler = terminal.accept(), Handler()
    finish = terminal.repository.finish
    monkeypatch.setattr(
        terminal.repository, 'finish', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('DB outage')),
    )
    assert terminal.build(handler).run_once(row.id) is False
    monkeypatch.setattr(terminal.repository, 'finish', finish)
    with Session(terminal.repository.engine) as storage, storage.begin():
        storage.get(User, terminal.user_id).encrypted_refresh_token = TokenService.encrypt_token('fake-refresh')
    refreshes = []

    def post(self, method, url, **kwargs):
        assert url == 'https://accounts.spotify.com/api/token'
        refreshes.append(True)
        return response(503, {'error_description': 'Temporary outage'}) if len(refreshes) == 1 else response(200, {
            'access_token': 'fresh-test-access', 'token_type': 'Bearer', 'expires_in': 3600,
        })

    monkeypatch.setattr('requests.sessions.Session.request', post)

    def authorized_read(runtime, target):
        runtime.api
        return terminal.world.observe(target)

    monkeypatch.setattr(OperationRuntime, 'read_resource', authorized_read)

    def worker():
        return OperationWorker(
            db_app, repository=terminal.repository, handlers={'example': handler}, heartbeat_interval=0,
        )

    make_due(terminal, row.id)
    assert worker().run_once(row.id) is False
    pending = terminal.repository.get(row.id)
    assert pending.state == 'running' and pending.completed_at is None
    assert pending.journal['read_retry']['next_attempt_at']
    assert pending.journal['runtime'].get('terminal_recovery_attempts', 0) == 0
    assert handler.executions == 1 and terminal.world.calls == 1
    make_due(terminal, row.id)
    assert worker().run_once(row.id) is True
    assert terminal.repository.get(row.id).state == 'succeeded'
    assert handler.executions == 1 and terminal.world.calls == 1 and len(refreshes) == 2
