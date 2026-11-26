"""CredentialRefresher: every refresh uses the refresh token stored in the users row and stores a rotated one.

The tests marked integration run on PostgreSQL, for the row lock, the
transaction-local lock timeout and two processes refreshing one row; they
depend only on transaction-scoped state, so they cover a transaction-mode
pooler too. The rest run on the default SQLite database.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import create_autospec, patch
from uuid import uuid4

import pytest
from flask import session as flask_session
from flask.sessions import SecureCookieSession
from sqlalchemy import event, insert, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from werkzeug.serving import make_server

from config import TestConfig
from harness.fake_spotify import create_app as create_fake_spotify
from harness.fake_spotify import tokens as fake_tokens
from harness.fake_spotify.app import CLIENT_ID as FAKE_CLIENT_ID
from harness.fake_spotify.app import CLIENT_SECRET as FAKE_CLIENT_SECRET
from shuffify import create_app
from shuffify.models.db import ActivityLog, PlaylistSnapshot, User, db
from shuffify.routes import require_auth
from shuffify.services import credential_refresher
from shuffify.services.credential_refresher import LOCK_TIMEOUT_SECONDS, CredentialRefresher, expired_token
from shuffify.services.executors import JobExecutorService
from shuffify.services.operations.control import read_retry_delay
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.token_service import TokenEncryptionError, TokenService
from shuffify.services.workshop_commit_service import WorkshopCommitService
from shuffify.spotify.auth import DEFAULT_SCOPES, SpotifyAuthManager, TokenInfo
from shuffify.spotify.credentials import credentials_for
from shuffify.spotify.exceptions import (
    SpotifyAppRejected,
    SpotifyGrantRevoked,
    SpotifyReadTransientError,
    SpotifyRefreshUnavailable,
    SpotifyTokenError,
)
from shuffify.worker import OperationWorker
from tests import spotify_urls
from tests.mutable_spotify import MutableSpotify, uri
from tests.playlist_setup_fixtures import PLAYLISTS, SetupSpotify
from tests.queued_operations import post_and_run
from tests.routes.test_playlist_setup_routes import put_settings, reviewed_body
from tests.routes.test_routine_policy import SOURCE, create, raid_watch, sign_in
from tests.services.operations.test_repository import postgres_repository as postgres_repository


class RotatingSpotify:
    """Spotify's token endpoint as a PKCE refresh may answer it.

    Each refresh issues a new refresh token, and the one presented stops
    working; presenting it again is ``invalid_grant``. ``rotate=False``
    answers with the token presented, as Spotify may.
    """

    def __init__(self, first="refresh-1", *, rotate=True):
        self.live, self.rotate = {first}, rotate
        self.presented, self.issued = [], 1

    def refresh_token(self, token_info):
        presented = token_info.refresh_token
        self.presented.append(presented)
        if presented not in self.live:
            raise SpotifyGrantRevoked("Token refresh failed: Invalid refresh token", error="invalid_grant")
        token = presented
        if self.rotate:
            self.issued += 1
            token = f"refresh-{self.issued}"
            self.live = {token}
        return TokenInfo(
            access_token=f"access-{len(self.presented)}", token_type="Bearer",
            expires_at=time.time() + 3600, refresh_token=token,
        )


def auth_manager(spotify):
    manager = create_autospec(SpotifyAuthManager, instance=True)
    manager.refresh_token.side_effect = spotify.refresh_token
    return manager


@pytest.fixture(autouse=True)
def no_kept_tokens():
    """Kept tokens live for the process; each test starts and ends without one."""
    credential_refresher._kept.clear()
    yield
    credential_refresher._kept.clear()


@pytest.fixture
def user(app_ctx):
    row = User(spotify_id="refreshing_user", encrypted_refresh_token=TokenService.encrypt_token("refresh-1"))
    db.session.add(row)
    db.session.commit()
    return row


def stored(user_id):
    """The users row's refresh token, as stored now."""
    with Session(db.engine) as session:
        return session.get(User, user_id).encrypted_refresh_token


def failing_commits(count):
    """Make the next ``count`` commits of the refresher's sessions fail, as a dropped connection would."""
    failures = [OperationalError("COMMIT", {}, Exception("connection lost"))] * count

    def fail(session):
        if failures and session is not db.session():
            raise failures.pop()

    event.listen(Session, "before_commit", fail)
    return fail


class TestRefresh:
    def test_a_refresh_uses_the_stored_token_and_stores_the_rotated_one(self, user):
        spotify = RotatingSpotify()

        token = CredentialRefresher(auth_manager(spotify), user.id).refresh_token(expired_token())

        assert spotify.presented == ["refresh-1"]
        assert TokenService.decrypt_token(stored(user.id)) == "refresh-2"
        assert token.access_token == "access-1"
        assert token.refresh_token is None

    def test_the_refresh_token_the_client_holds_is_never_presented(self, user):
        spotify = RotatingSpotify()
        held = TokenInfo(access_token="old", token_type="Bearer", expires_at=0, refresh_token="refresh-1-copy")

        CredentialRefresher(auth_manager(spotify), user.id).refresh_token(held)

        assert spotify.presented == ["refresh-1"]

    def test_each_refresh_reads_the_token_stored_since(self, user):
        spotify = RotatingSpotify()
        first, second = (CredentialRefresher(auth_manager(spotify), user.id) for _ in range(2))

        first.refresh_token()
        second.refresh_token()
        first.refresh_token()

        assert spotify.presented == ["refresh-1", "refresh-2", "refresh-3"]
        assert TokenService.decrypt_token(stored(user.id)) == "refresh-4"

    def test_a_refresh_that_keeps_the_token_writes_nothing(self, user):
        """Fernet ciphertext differs on every encryption, so a rewrite of the same token would show."""
        before = stored(user.id)

        CredentialRefresher(auth_manager(RotatingSpotify(rotate=False)), user.id).refresh_token()

        assert stored(user.id) == before

    def test_an_unexpired_token_is_used_as_it_is(self, user):
        spotify = RotatingSpotify()
        refresher = CredentialRefresher(auth_manager(spotify), user.id)
        fresh = TokenInfo(access_token="fresh", token_type="Bearer", expires_at=time.time() + 600)

        assert refresher.ensure_valid_token(fresh) is fresh
        assert refresher.ensure_valid_token(expired_token()).access_token == "access-1"
        assert spotify.presented == ["refresh-1"]

    def test_the_row_is_found_only_when_a_refresh_needs_it(self, user):
        calls = []

        def find():
            calls.append(1)
            return user.id

        refresher = CredentialRefresher(auth_manager(RotatingSpotify()), find)
        refresher.ensure_valid_token(TokenInfo(access_token="fresh", token_type="Bearer", expires_at=time.time() + 60))
        assert calls == []
        refresher.refresh_token()
        assert calls == [1]


class TestNoStoredToken:
    @pytest.mark.parametrize("row", ["no row", "no token", "unknown id"])
    def test_there_is_nothing_to_refresh_with(self, user, row):
        spotify = RotatingSpotify()
        if row == "no token":
            user.encrypted_refresh_token = None
            db.session.commit()
        user_id = {"no row": None, "no token": user.id, "unknown id": user.id + 1}[row]

        with pytest.raises(SpotifyTokenError) as raised:
            CredentialRefresher(auth_manager(spotify), user_id).refresh_token()

        assert not isinstance(raised.value, SpotifyReadTransientError)
        assert spotify.presented == []

    def test_a_stored_token_that_cannot_be_decrypted_is_named_without_its_text(self, user):
        user.encrypted_refresh_token = "not-a-fernet-token"
        db.session.commit()

        with pytest.raises(SpotifyTokenError) as raised:
            CredentialRefresher(auth_manager(RotatingSpotify()), user.id).refresh_token()

        assert "not-a-fernet-token" not in str(raised.value)
        assert isinstance(raised.value.__cause__, TokenEncryptionError)


class TestDatabaseErrorsAreTransient:
    def test_a_database_error_finding_the_row(self, user):
        def find():
            raise OperationalError("SELECT", {}, Exception("connection lost"))

        with pytest.raises(SpotifyRefreshUnavailable) as raised:
            CredentialRefresher(auth_manager(RotatingSpotify()), find).refresh_token()

        assert raised.value.retry_after == LOCK_TIMEOUT_SECONDS

    def test_a_database_error_before_spotify_answers(self, user, monkeypatch):
        spotify = RotatingSpotify()

        def unavailable(storage, user_id):
            raise OperationalError("SELECT ... FOR NO KEY UPDATE", {}, Exception("lock timeout"))

        monkeypatch.setattr(credential_refresher, "_locked_stored_token", unavailable)
        with pytest.raises(SpotifyRefreshUnavailable):
            CredentialRefresher(auth_manager(spotify), user.id).refresh_token()

        assert spotify.presented == []

    def test_the_worker_defers_it_and_the_web_does_not_sign_out(self):
        unavailable = SpotifyRefreshUnavailable("x", retry_after=LOCK_TIMEOUT_SECONDS)

        assert read_retry_delay(unavailable) is not None
        assert not isinstance(unavailable, SpotifyTokenError)


class TestFailedSave:
    """A rotated token whose store fails is kept by this process for its next refresh, and only that long."""

    @pytest.fixture
    def commits_fail(self):
        listeners = []

        def arm(count):
            listeners.append(failing_commits(count))

        yield arm
        for listener in listeners:
            event.remove(Session, "before_commit", listener)

    def test_a_store_that_fails_once_is_stored_by_the_retry(self, user, commits_fail):
        commits_fail(1)

        CredentialRefresher(auth_manager(RotatingSpotify()), user.id).refresh_token()

        assert TokenService.decrypt_token(stored(user.id)) == "refresh-2"
        assert credential_refresher._kept == {}

    def test_the_next_refresh_uses_the_kept_token_and_the_store_catches_up(self, user, commits_fail):
        spotify = RotatingSpotify()
        before = stored(user.id)
        commits_fail(2)

        CredentialRefresher(auth_manager(spotify), user.id).refresh_token()
        assert stored(user.id) == before
        assert user.id in credential_refresher._kept

        CredentialRefresher(auth_manager(spotify), user.id).refresh_token()

        assert spotify.presented == ["refresh-1", "refresh-2"]
        assert TokenService.decrypt_token(stored(user.id)) == "refresh-3"
        assert credential_refresher._kept == {}

    def test_the_kept_token_stands_in_even_without_a_rotation(self, user, commits_fail):
        spotify = RotatingSpotify()
        commits_fail(2)
        CredentialRefresher(auth_manager(spotify), user.id).refresh_token()

        spotify.rotate = False
        CredentialRefresher(auth_manager(spotify), user.id).refresh_token()

        assert spotify.presented == ["refresh-1", "refresh-2"]
        assert TokenService.decrypt_token(stored(user.id)) == "refresh-2"
        assert credential_refresher._kept == {}

    def test_the_kept_token_is_dropped_when_the_stored_token_changes(self, user, commits_fail):
        spotify = RotatingSpotify()
        commits_fail(2)
        CredentialRefresher(auth_manager(spotify), user.id).refresh_token()

        user.encrypted_refresh_token = TokenService.encrypt_token("signed-in-again")
        db.session.commit()
        spotify.live.add("signed-in-again")
        CredentialRefresher(auth_manager(spotify), user.id).refresh_token()

        assert spotify.presented == ["refresh-1", "signed-in-again"]
        assert credential_refresher._kept == {}

    def test_no_log_line_or_exception_text_holds_a_refresh_token(self, user, commits_fail, shuffify_logs, monkeypatch):
        spotify = RotatingSpotify()
        commits_fail(2)
        CredentialRefresher(auth_manager(spotify), user.id).refresh_token()

        def unavailable(storage, user_id):
            raise OperationalError("SELECT ... FOR NO KEY UPDATE", {}, Exception("lock timeout"))

        monkeypatch.setattr(credential_refresher, "_locked_stored_token", unavailable)
        with pytest.raises(SpotifyRefreshUnavailable) as raised:
            CredentialRefresher(auth_manager(spotify), user.id).refresh_token()

        kept = credential_refresher._kept[user.id]
        assert kept.token == "refresh-2"
        assert "Storing the rotated refresh token" in shuffify_logs.text
        for logged in (shuffify_logs.text, str(raised.value), repr(credential_refresher._kept)):
            assert "refresh-2" not in logged
            assert "refresh-1" not in logged

    def test_its_log_lines_name_the_user_by_internal_id(self, user, commits_fail, shuffify_logs):
        commits_fail(2)

        CredentialRefresher(auth_manager(RotatingSpotify()), user.id).refresh_token()

        assert f"refresh token for user {user.id} failed again" in shuffify_logs.text
        assert user.spotify_id not in shuffify_logs.text


class TestTypedFailureHook:
    @pytest.mark.parametrize("failure", [
        SpotifyAppRejected("Token refresh failed: Invalid client", error="invalid_client"),
        SpotifyGrantRevoked("Token refresh failed: Refresh token revoked", error="invalid_grant"),
    ])
    def test_a_refusal_calls_the_hook_once_and_still_raises(self, user, failure):
        manager = create_autospec(SpotifyAuthManager, instance=True)
        manager.refresh_token.side_effect = failure
        seen = []

        with pytest.raises(type(failure)) as raised:
            CredentialRefresher(manager, user.id, on_typed_failure=seen.append).refresh_token()

        assert raised.value is failure
        assert seen == [failure]

    def test_no_hook_for_a_success_or_another_refusal(self, user):
        seen = []
        CredentialRefresher(auth_manager(RotatingSpotify()), user.id, on_typed_failure=seen.append).refresh_token()
        manager = create_autospec(SpotifyAuthManager, instance=True)
        manager.refresh_token.side_effect = SpotifyTokenError("Token refresh failed: server error")
        with pytest.raises(SpotifyTokenError):
            CredentialRefresher(manager, user.id, on_typed_failure=seen.append).refresh_token()

        assert seen == []

    def test_a_hook_that_raises_does_not_change_the_failure(self, user):
        failure = SpotifyGrantRevoked("Token refresh failed: Refresh token revoked", error="invalid_grant")
        manager = create_autospec(SpotifyAuthManager, instance=True)
        manager.refresh_token.side_effect = failure

        def broken(_failure):
            raise RuntimeError("hook failed")

        with pytest.raises(SpotifyGrantRevoked) as raised:
            CredentialRefresher(manager, user.id, on_typed_failure=broken).refresh_token()

        assert raised.value is failure


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

REDIRECT = "http://127.0.0.1:8000/callback"
# Every connection the app under test opens carries this name, so a test can find them in pg_stat_activity.
APPLICATION = "credential-refresher-" + uuid4().hex
# Enough rotations for any test here: each refresh the fake answers with a new token uses one.
ROTATE = {"operation": "POST /api/token", "kind": "rotate_refresh", "times": 20}
EXPIRED_SESSION_TOKEN = {"access_token": "expired-access", "token_type": "Bearer", "expires_at": 0}


def fake_refresh_token(user="alice"):
    return fake_tokens.mint("refresh", user=user, scope=" ".join(DEFAULT_SCOPES))


@pytest.fixture(scope="module")
def fake_spotify_server():
    app = create_fake_spotify(redirect_uris=[REDIRECT])
    server = make_server("127.0.0.1", 0, app, threaded=True)
    app.config["ORIGIN"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield app
    server.shutdown()


@pytest.fixture
def fake_spotify(fake_spotify_server, monkeypatch):
    """The fake Spotify, reset, with every Spotify URL the app uses pointed at it."""
    config = fake_spotify_server.config
    config["STORE"].reset()
    config["FAULTS"].clear()
    config["VIOLATIONS"].clear()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    spotify_urls.repoint(monkeypatch, config["ORIGIN"])
    yield fake_spotify_server
    assert list(config["VIOLATIONS"]) == [], "the app sent Spotify something it does not document"


@pytest.fixture
def make_postgres_app(postgres_repository, monkeypatch, tmp_path):
    """Build the app on the test PostgreSQL schema, with the Spotify credentials the fake takes."""
    engine = postgres_repository.engine
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TestConfig, "REDIS_URL", None)
    monkeypatch.setattr(TestConfig, "SQLALCHEMY_DATABASE_URI", engine.url.render_as_string(hide_password=False))
    apps = []

    def make(**engine_options):
        monkeypatch.setattr(TestConfig, "SQLALCHEMY_ENGINE_OPTIONS", {
            "execution_options": engine.get_execution_options(),
            "connect_args": {"application_name": APPLICATION}, **engine_options,
        })
        app = create_app("testing")
        app.config.update(
            SPOTIFY_CLIENT_ID=FAKE_CLIENT_ID, SPOTIFY_CLIENT_SECRET=FAKE_CLIENT_SECRET, SPOTIFY_REDIRECT_URI=REDIRECT,
        )
        apps.append(app)
        return app

    yield make
    for app in apps:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


@pytest.fixture
def postgres_app(make_postgres_app):
    app = make_postgres_app()
    with app.app_context():
        yield app


def add_user(spotify_id="alice", refresh_token=None):
    row = User(spotify_id=spotify_id, display_name=spotify_id,
               encrypted_refresh_token=TokenService.encrypt_token(refresh_token or fake_refresh_token(spotify_id)))
    db.session.add(row)
    db.session.commit()
    user_id = row.id
    db.session.remove()
    return user_id


def stored_token(engine, user_id):
    """The users row's refresh token, decrypted, as stored now."""
    with Session(engine) as storage:
        return TokenService.decrypt_token(storage.get(User, user_id).encrypted_refresh_token)


def refresher(user_id, **options):
    return CredentialRefresher(SpotifyAuthManager(credentials_for(None)), user_id, **options)


def in_app(app, function):
    def run():
        with app.app_context():
            return function()

    return run


def wait_for(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


class HeldRefresh:
    """An auth manager whose first refresh waits at Spotify, the users row locked, until released."""

    def __init__(self, manager):
        self.manager, self.presented = manager, []
        self.held, self.release = threading.Event(), threading.Event()
        self._lock = threading.Lock()

    def refresh_token(self, token_info):
        with self._lock:
            self.presented.append(token_info.refresh_token)
            first = len(self.presented) == 1
        if first:
            self.held.set()
            assert self.release.wait(10)
        return self.manager.refresh_token(token_info)


def row_locked(engine, user_id):
    """Whether another transaction holds a lock on the users row that a refresh would wait for."""
    try:
        with engine.begin() as connection:
            connection.execute(
                select(User.id).where(User.id == user_id).with_for_update(key_share=True, nowait=True)
            ).all()
    except OperationalError as error:
        if getattr(error.orig, "pgcode", None) == "55P03":
            return True
        raise
    return False


@contextmanager
def row_lock_held(engine, user_id):
    """Hold the users row's lock in another transaction, as a refresh in the other process does."""
    with engine.connect() as holder, holder.begin():
        holder.execute(select(User.id).where(User.id == user_id).with_for_update(key_share=True)).all()
        yield


def waiting_backends(engine):
    """The app's connections that are waiting for a lock."""
    with engine.connect() as observer:
        return observer.scalars(text(
            "SELECT pid FROM pg_stat_activity WHERE application_name = :name AND wait_event_type = 'Lock'"
        ), {"name": APPLICATION}).all()


@pytest.fixture(params=["lock timeout", "dropped connection"])
def refusal(request, postgres_repository, monkeypatch):
    """The database refusing the next refresh of a users row.

    Another transaction holds the row's lock, and the refresh either waits
    past its lock timeout or has its connection dropped while it waits.
    """
    engine = postgres_repository.engine
    dropping = request.param == "dropped connection"
    monkeypatch.setattr(credential_refresher, "LOCK_TIMEOUT_SECONDS", 30 if dropping else 1)
    dropped = []

    def drop_the_waiting_refresh(stop):
        while not stop.is_set():
            waiting = waiting_backends(engine)
            if waiting:
                with engine.connect() as admin:
                    admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": waiting[0]})
                dropped.append(waiting[0])
                return
            time.sleep(0.02)

    @contextmanager
    def refuse(user_id):
        stop = threading.Event()
        dropper = threading.Thread(target=drop_the_waiting_refresh, args=(stop,))
        with row_lock_held(engine, user_id):
            if dropping:
                dropper.start()
            started = time.monotonic()
            try:
                yield
            finally:
                stop.set()
                if dropping:
                    dropper.join()
            elapsed = time.monotonic() - started
        if dropping:
            assert len(dropped) == 1 and elapsed < 20, "the refresh's connection was not dropped while it waited"

    return refuse


@pytest.mark.integration
class TestOneStoredToken:
    def test_the_web_and_the_worker_each_refresh_with_the_others_rotated_token(
        self, postgres_app, fake_spotify, postgres_repository,
    ):
        fake_spotify.config["FAULTS"].arm(ROTATE)
        first = fake_refresh_token()
        user_id = add_user(refresh_token=first)
        engine = postgres_repository.engine

        JobExecutorService._get_spotify_api(db.session.get(User, user_id))  # a run's client refreshes first
        after_worker = stored_token(engine, user_id)
        with postgres_app.test_request_context():
            flask_session.update(user_data={"id": "alice"}, _db_user_id=user_id)
            assert require_auth() is not None  # no access token in the session: one is minted
        after_web = stored_token(engine, user_id)
        JobExecutorService._get_spotify_api(db.session.get(User, user_id))

        # The fake refuses a replaced token, so each refresh presented the newest one.
        assert len({first, after_worker, after_web, stored_token(engine, user_id)}) == 4
        assert fake_spotify.config["STORE"].replaced_refresh_tokens == {first, after_worker, after_web}

    def test_two_refreshes_at_once_both_succeed_one_after_the_other(
        self, postgres_app, fake_spotify, postgres_repository,
    ):
        fake_spotify.config["FAULTS"].arm(ROTATE)
        first = fake_refresh_token()
        user_id = add_user(refresh_token=first)
        held = HeldRefresh(SpotifyAuthManager(credentials_for(None)))

        with ThreadPoolExecutor(max_workers=2) as pool:
            web = pool.submit(in_app(postgres_app, CredentialRefresher(held, user_id).refresh_token))
            try:
                assert held.held.wait(10)
                worker = pool.submit(in_app(postgres_app, CredentialRefresher(held, user_id).refresh_token))
                assert wait_for(lambda: waiting_backends(postgres_repository.engine)), "the second refresh never waited"
            finally:
                held.release.set()
            refreshed = web.result(timeout=20), worker.result(timeout=20)

        assert all(token.access_token for token in refreshed)
        assert held.presented[0] == first and held.presented[1] not in (None, first)
        assert stored_token(postgres_repository.engine, user_id) not in held.presented

    def test_a_web_refresh_does_not_wait_for_a_run_holding_its_credential_lock(
        self, postgres_app, fake_spotify,
    ):
        user_id = add_user()
        repository = OperationRepository(db.engine)
        holding, done = threading.Event(), threading.Event()

        def long_run():
            with repository.target_locks([f"credential:{user_id}"], timeout_s=5) as acquired:
                assert acquired
                holding.set()
                assert done.wait(20)

        with ThreadPoolExecutor(max_workers=1) as pool:
            run = pool.submit(in_app(postgres_app, long_run))
            try:
                assert holding.wait(10)
                started = time.monotonic()
                with postgres_app.test_request_context():
                    flask_session.update(user_data={"id": "alice"}, _db_user_id=user_id,
                                         spotify_token=EXPIRED_SESSION_TOKEN)
                    api = require_auth()
                elapsed = time.monotonic() - started
            finally:
                done.set()
            run.result(timeout=20)

        assert api is not None and api.token_info.access_token != "expired-access"
        assert elapsed < 5


@pytest.mark.integration
class TestTheLockIsTransactionLocal:
    def test_the_lock_timeout_lasts_for_the_refresh_transaction_only(
        self, make_postgres_app, fake_spotify, postgres_repository, monkeypatch,
    ):
        app = make_postgres_app(pool_size=1, max_overflow=0)
        with app.app_context():
            user_id = add_user()
            refreshing = refresher(user_id)

            def pooled_lock_timeout():
                # The pool holds one connection, so this is the one each refresh used.
                with db.engine.connect() as connection:
                    return connection.scalar(text("SHOW lock_timeout"))

            refreshing.refresh_token()
            assert pooled_lock_timeout() == "0"
            monkeypatch.setattr(credential_refresher, "LOCK_TIMEOUT_SECONDS", 1)
            started = time.monotonic()
            with row_lock_held(postgres_repository.engine, user_id), pytest.raises(SpotifyRefreshUnavailable):
                refreshing.refresh_token()
            assert time.monotonic() - started < 5
            assert pooled_lock_timeout() == "0"

    def test_a_refresh_holding_the_row_lock_does_not_block_inserts_that_reference_the_user(
        self, postgres_app, fake_spotify, postgres_repository,
    ):
        user_id = add_user()
        engine = postgres_repository.engine
        held = HeldRefresh(SpotifyAuthManager(credentials_for(None)))

        with ThreadPoolExecutor(max_workers=1) as pool:
            refreshing = pool.submit(in_app(postgres_app, CredentialRefresher(held, user_id).refresh_token))
            try:
                assert held.held.wait(10)
                assert row_locked(engine, user_id)
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                    connection.execute(insert(ActivityLog).values(
                        user_id=user_id, activity_type="login", description="During a refresh",
                    ))
            finally:
                held.release.set()
            refreshing.result(timeout=20)

        with Session(engine) as storage:
            assert storage.scalars(select(ActivityLog.description)).all() == ["During a refresh"]

    def test_the_hook_for_a_refused_refresh_runs_after_the_row_lock_is_released(
        self, postgres_app, fake_spotify, postgres_repository,
    ):
        user_id = add_user()
        fake_spotify.config["FAULTS"].arm({"operation": "POST /api/token", "kind": "grant_revoked"})
        marked = []

        def mark(failure):
            # A hook that updates the same row, in a transaction of its own.
            with postgres_repository.engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                connection.execute(update(User).where(User.id == user_id).values(display_name="marked"))
            marked.append(failure.error)

        started = time.monotonic()
        with pytest.raises(SpotifyGrantRevoked):
            refresher(user_id, on_typed_failure=mark).refresh_token()

        assert marked == ["invalid_grant"]
        assert time.monotonic() - started < 2


@pytest.mark.integration
class TestADatabaseRefusalIsTransient:
    @pytest.mark.parametrize("where", ["client construction", "401 retry"])
    def test_the_request_answers_503_and_keeps_the_session(self, postgres_app, fake_spotify, refusal, where):
        user_id = add_user()
        client = postgres_app.test_client()
        token = (EXPIRED_SESSION_TOKEN if where == "client construction" else
                 {"access_token": "not-a-fake-token", "token_type": "Bearer", "expires_at": time.time() + 3600})
        with client.session_transaction() as sess:
            sess.update(user_data={"id": "alice"}, _db_user_id=user_id, spotify_token=token)
            before = dict(sess)
        # Activity reads only the database once signed in; the review reads the playlist from Spotify, which
        # rejects the access token, so the client refreshes inside its 401 retry.
        path = "/api/activity" if where == "client construction" else "/api/playlist/alicemix/operation-review"

        with refusal(user_id):
            response = client.get(path)

        assert response.status_code == 503, response.get_json()
        assert response.headers["Retry-After"] == str(credential_refresher.LOCK_TIMEOUT_SECONDS)
        with client.session_transaction() as sess:
            assert dict(sess) == before

    def test_a_run_whose_refresh_is_refused_is_deferred_not_failed(self, postgres_app, fake_spotify, refusal):
        user_id = add_user()
        repository = OperationRepository(db.engine)
        row = repository.enqueue(kind="example", user_id=user_id, targets=["alicemix"], payload={})
        clients = []

        def handler(runtime):
            clients.append(runtime.api)
            return {"success": True}

        worker = OperationWorker(postgres_app, repository=repository, handlers={"example": handler},
                                 heartbeat_interval=0)
        with refusal(user_id):
            assert worker.run_once(row.id) is False

        pending = repository.get(row.id)
        assert pending.state == "running" and pending.completed_at is None
        assert pending.journal["read_retry"]["next_attempt_at"]
        assert clients == []


@pytest.mark.integration
def test_a_failed_save_keeps_the_rotated_token_for_the_next_refresh(
    postgres_app, fake_spotify, postgres_repository, shuffify_logs,
):
    fake_spotify.config["FAULTS"].arm(ROTATE)
    first = fake_refresh_token()
    user_id = add_user(refresh_token=first)
    engine = postgres_repository.engine
    failing = failing_commits(2)  # the store and its retry
    try:
        refresher(user_id).refresh_token()
    finally:
        event.remove(Session, "before_commit", failing)
    kept = credential_refresher._kept[user_id].token
    assert stored_token(engine, user_id) == first
    assert first in fake_spotify.config["STORE"].replaced_refresh_tokens  # Spotify takes only the kept one now

    refresher(user_id).refresh_token()

    assert stored_token(engine, user_id) not in (first, kept)
    assert credential_refresher._kept == {}
    assert first not in shuffify_logs.text and kept not in shuffify_logs.text


class LockProbe:
    """A Spotify stand-in's calls, each checking first whether a transaction holds a users row's lock."""

    def __init__(self, spotify, engine, user_id):
        self._spotify, self._engine, self._user_id = spotify, engine, user_id
        self.probed, self.locked_calls = 0, []

    def __getattr__(self, name):
        value = getattr(self._spotify, name)
        if not callable(value):
            return value

        def call(*args, **kwargs):
            self.probed += 1
            if row_locked(self._engine, self._user_id):
                self.locked_calls.append(name)
            return value(*args, **kwargs)

        return call


LOCKING_PATHS = ["raid watch", "playlist setup save", "routine activation", "worker workshop commit",
                 "workshop commit whose snapshot fails"]


@pytest.mark.integration
@pytest.mark.parametrize("path", LOCKING_PATHS)
def test_a_path_that_locks_the_users_row_ends_its_transaction_before_calling_spotify(
    path, postgres_app, postgres_repository, sample_token, monkeypatch, no_scraping,
):
    """A Spotify call while the row is locked would wait for the lock in any token refresh it makes.

    These are the paths that take the lock (lock_user_inventory) and call
    Spotify in the same request or run. The routes that save or delete a
    snapshot or a saved session take it and call Spotify not at all.
    """
    monkeypatch.setattr("shuffify.services.worker_control.WorkerControlClient.poke", lambda self: None)
    monkeypatch.setattr("shuffify.services.worker_control.WorkerControlClient.registrations", lambda *args: None)
    workshop = path.startswith("worker") or path.startswith("workshop")
    owner = "alice" if workshop else "user123"
    stand_in = (MutableSpotify(SimpleNamespace(spotify_id=owner), target=[uri(1)]) if workshop else SetupSpotify(
        playlists={**PLAYLISTS, "mix0": {"name": "Mix 0", "collaborative": True},
                   "mix1": {"name": "Mix 1", "collaborative": True}, SOURCE: {"name": "External", "owner": "external"}},
    ))
    user_id = add_user(owner)
    spotify = LockProbe(stand_in, postgres_repository.engine, user_id)
    monkeypatch.setattr("shuffify.routes.require_auth", lambda: spotify)
    client = sign_in(postgres_app, owner, sample_token)

    if path == "raid watch":
        assert raid_watch(client, "mix0").status_code == 200
    elif path == "playlist setup save":
        _, body = reviewed_body(client, spotify, [{"op": "add_search_source", "search_query": "lofi"}])
        assert put_settings(client, spotify, body).status_code == 200
    elif path == "routine activation":
        response, _ = create(client, "mix1")
        assert response.status_code == 200, response.json
    elif path == "worker workshop commit":
        applied = post_and_run(client, spotify, "/api/workshop/target/commit",
                               json={"baseline_track_uris": [uri(1)], "track_uris": [uri(2)]})
        assert applied.operation["state"] == "succeeded"
    else:
        # The snapshot fails after it has taken the lock; the write to Spotify goes ahead.
        with postgres_app.test_request_context(), patch(
            "shuffify.services.playlist_snapshot_service.check_storage_growth", side_effect=RuntimeError("unexpected"),
        ):
            WorkshopCommitService(spotify).commit(
                "target", db.session.get(User, user_id), SecureCookieSession(), [uri(1)], [uri(2)],
            )
        assert stand_in.writes == [("update", "target", [uri(2)])]
        assert db.session.query(PlaylistSnapshot).count() == 0

    assert spotify.probed > 0
    assert spotify.locked_calls == []
