"""The harness config refuses to start against anything but this machine.

HarnessConfig is what scripts/harness.sh runs the real app under. Its guard
keeps the app away from real Spotify and production, so these tests pin each
refusal, and pin that create_app() cannot swallow it:
create_app logs and continues past a ValueError from env validation for every
non-production config, which is why the guard raises something else.
"""

import psycopg2
import pytest
from psycopg2.extensions import parse_dsn
from sqlalchemy import create_engine

from config import (
    HARNESS_FAKE_CLIENT_ID,
    HARNESS_FAKE_CLIENT_SECRET,
    HarnessConfig,
    HarnessSafetyError,
    _libpq_conninfo,
)

PG = "postgresql+psycopg2://"  # what _resolve_database_url turns every PostgreSQL URL into

SAFE = {
    "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg2://shuffify@127.0.0.1:54329/shuffify_harness",
    "REDIS_URL": "redis://127.0.0.1:63799/0",
    "SENTRY_DSN": "",
    "SPOTIFY_CLIENT_ID": HARNESS_FAKE_CLIENT_ID,
    "SPOTIFY_CLIENT_SECRET": HARNESS_FAKE_CLIENT_SECRET,
    "SECRET_KEY": "harness-test-secret",
}


@pytest.fixture
def harness(monkeypatch):
    """HarnessConfig with every attribute set to a safe value."""
    for name, value in SAFE.items():
        monkeypatch.setattr(HarnessConfig, name, value)
    for name in ("SHUFFIFY_ALLOW_SCHEMA_DRIFT", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)
    # As if SPOTIFY_BASE_URL pointed at the local fake when the client loaded.
    monkeypatch.setattr("shuffify.spotify.endpoints.API_BASE_URL", "http://127.0.0.1:8900/v1")
    return HarnessConfig


def test_safe_config_passes(harness):
    harness.assert_safe()


@pytest.mark.parametrize(
    "url",
    [
        f"{PG}localhost/shuffify_harness",
        f"{PG}u:p@[::1]:5432/db",
        f"{PG}u@127.0.0.2:5432/db",
        f"{PG}u@/shuffify_harness?host=/var/run/postgresql",  # a Unix socket directory
    ],
)
def test_loopback_database_urls_pass(harness, monkeypatch, url):
    monkeypatch.setattr(harness, "SQLALCHEMY_DATABASE_URI", url)
    harness.assert_safe()


# Each of these names a local host where a URL parser looks, and another host
# where libpq looks. Hosts are .invalid and addresses TEST-NET, so even a
# broken guard could not reach anything.
LIBPQ_ELSEWHERE = [
    f"{PG}u@localhost/db?host=db.prod.invalid",
    f"{PG}u@localhost/db?service=prod",  # a service definition supplies its own host
    f"{PG}u@localhost:5432/db#?hostaddr=192.0.2.1",  # urlsplit reads the query as a fragment
    f"{PG}127.0.0.1#@db.prod.invalid:5432/neondb",  # urlsplit's host is SQLAlchemy's user
    f"{PG}127.0.0.1?@db.prod.invalid:5432/neondb",
    f"{PG}u@127.0.0.1:54329/db?dsn=hostaddr%3D192.0.2.1",  # psycopg2 merges dsn in
    f"{PG}u@localhost/db?%20host=db.prod.invalid",  # libpq skips the space
    f"{PG}u@localhost/db?hostaddr%20=192.0.2.1",
    f"{PG}u@localhost/db?host=localhost,db.prod.invalid",  # libpq tries every host
    f"{PG}u@localhost/db?host=localhost&host=db.prod.invalid",
]


@pytest.mark.parametrize("url", LIBPQ_ELSEWHERE)
def test_database_url_naming_another_host_to_libpq_is_refused(harness, monkeypatch, url):
    monkeypatch.setattr(harness, "SQLALCHEMY_DATABASE_URI", url)
    with pytest.raises(HarnessSafetyError, match="DATABASE_URL"):
        harness.assert_safe()


@pytest.mark.parametrize("url", [f"{PG}shuffify@127.0.0.1:54329/shuffify_harness", *LIBPQ_ELSEWHERE])
def test_check_reads_what_the_engine_hands_libpq(monkeypatch, url):
    """The parameters the check inspects are the ones the app's engine connects with.

    psycopg2's C-level connect is replaced, so nothing connects: the test
    captures the connection string the engine would have handed libpq.
    """
    sent = []

    def capture(dsn, **kwargs):
        sent.append(dsn)
        raise ConnectionAbortedError("captured")

    monkeypatch.setattr(psycopg2, "_connect", capture)
    engine = create_engine(url, **getattr(HarnessConfig, "SQLALCHEMY_ENGINE_OPTIONS", {}))
    with pytest.raises(ConnectionAbortedError):
        engine.connect()
    assert parse_dsn(sent[0]) == _libpq_conninfo(url)


@pytest.mark.parametrize("name, value", [("PGHOSTADDR", "192.0.2.1"), ("PGSERVICE", "prod")])
def test_libpq_redirecting_environment_refused(harness, monkeypatch, name, value):
    """libpq reads these even when the connection string names a local host."""
    monkeypatch.setenv(name, value)
    with pytest.raises(HarnessSafetyError, match=name):
        harness.assert_safe()


@pytest.mark.parametrize(
    "attr, value, reason",
    [
        # A remote PostgreSQL URL.
        ("SQLALCHEMY_DATABASE_URI", f"{PG}u@ep-x.db.invalid/neondb?sslmode=require", "DATABASE_URL"),
        ("SQLALCHEMY_DATABASE_URI", "", "DATABASE_URL"),
        ("SQLALCHEMY_DATABASE_URI", "sqlite:///shuffify_harness.db", "DATABASE_URL"),
        # Another driver: the check reads connection parameters the psycopg2 way.
        ("SQLALCHEMY_DATABASE_URI", "postgresql+asyncpg://u@localhost/db", "DATABASE_URL"),
        # A host that merely starts with a loopback-looking label is not loopback.
        ("SQLALCHEMY_DATABASE_URI", f"{PG}u@localhost.example.com/db", "DATABASE_URL"),
        # A DNS name starting "127." resolves wherever its owner says.
        ("SQLALCHEMY_DATABASE_URI", f"{PG}u@127.attacker.example/db", "DATABASE_URL"),
        ("REDIS_URL", "redis://127.attacker.example:6379/0", "REDIS_URL"),
        ("REDIS_URL", "redis://redis.internal:6379/0", "REDIS_URL"),
        ("REDIS_URL", "", "REDIS_URL"),
        ("SENTRY_DSN", "https://key@o0.ingest.sentry.io/0", "SENTRY_DSN"),
        ("SPOTIFY_CLIENT_ID", "any-other-client-id", "credentials"),
        ("SPOTIFY_CLIENT_SECRET", "any-other-client-secret", "credentials"),
        ("SECRET_KEY", None, "SECRET_KEY"),
    ],
)
def test_unsafe_value_refused(harness, monkeypatch, attr, value, reason):
    monkeypatch.setattr(harness, attr, value)
    with pytest.raises(HarnessSafetyError, match=reason):
        harness.assert_safe()


def test_real_spotify_hosts_refused(harness, monkeypatch):
    """Without SPOTIFY_BASE_URL the client would talk to api.spotify.com."""
    monkeypatch.setattr("shuffify.spotify.endpoints.API_BASE_URL", "https://api.spotify.com/v1")
    with pytest.raises(HarnessSafetyError, match="SPOTIFY_BASE_URL"):
        harness.assert_safe()


def test_schema_drift_override_refused(harness, monkeypatch):
    monkeypatch.setenv("SHUFFIFY_ALLOW_SCHEMA_DRIFT", "true")
    with pytest.raises(HarnessSafetyError, match="SHUFFIFY_ALLOW_SCHEMA_DRIFT"):
        harness.assert_safe()


def test_every_problem_is_reported_at_once(harness, monkeypatch):
    monkeypatch.setattr(harness, "REDIS_URL", "redis://remote:6379/0")
    monkeypatch.setattr(harness, "SENTRY_DSN", "https://key@o0.ingest.sentry.io/0")
    with pytest.raises(HarnessSafetyError) as exc:
        harness.assert_safe()
    assert "REDIS_URL" in str(exc.value) and "SENTRY_DSN" in str(exc.value)


def test_guard_is_not_a_value_error():
    """create_app() swallows ValueError for non-production configs."""
    assert not issubclass(HarnessSafetyError, ValueError)


def test_create_app_refuses_unsafe_harness_config(harness, monkeypatch):
    """The refusal propagates out of create_app before anything is built."""
    from shuffify import create_app

    # .invalid: were the refusal ever to come too late, nothing could answer.
    monkeypatch.setattr(harness, "SQLALCHEMY_DATABASE_URI", f"{PG}u@ep-x.db.invalid/neondb")
    with pytest.raises(HarnessSafetyError):
        create_app("harness")


def test_harness_config_never_falls_back_to_create_all():
    """TESTING would route it to db.create_all() and skip the migration chain."""
    assert HarnessConfig.TESTING is False
    assert HarnessConfig.MIGRATE_ON_STARTUP is False
