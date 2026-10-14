"""Startup logging never writes a database or Redis password.

Each case asserts against the password the connecting library itself would
send -- libpq, via SQLAlchemy and psycopg2, for the database; redis-py for
Redis -- not against a format, so a parser disagreement (such as where an
unencoded "@" ends the password) fails here rather than leaking.

The URLs are assembled from FAKE so that no line holds a credential-shaped
literal for secret scanners to flag; every host is on example.com.
"""

import logging
from unittest.mock import MagicMock

import pytest
import redis
from flask import Flask
from psycopg2.extensions import make_dsn, parse_dsn
from redis.connection import parse_url as redis_parse_url
from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2
from sqlalchemy.engine import make_url

import shuffify
from shuffify import _loggable_db_url, _loggable_redis_url

FAKE = "fake-55word"  # what every case checks the output for
FAKE_WITH_AT = "fake@55word"  # an unencoded "@": the two parsers disagree on it
FAKE_ENCODED = "fake%4055word"  # the same, percent-encoded
FAKE_DSN = "password%3Dfake-55word"  # a whole libpq connection string
DB_HOST = "db.example.com"
REDIS_HOST = "redis.example.com"


def libpq_password(url):
    """The password libpq receives: SQLAlchemy's connect args, as psycopg2.connect() joins them."""
    cargs, cparams = PGDialect_psycopg2().create_connect_args(make_url(url))
    return parse_dsn(make_dsn(*cargs, **cparams))["password"]


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql+psycopg2://shuffify:{FAKE}@{DB_HOST}:5432/app?sslmode=require",
        f"postgresql://u:{FAKE_ENCODED}@{DB_HOST}/app",
        f"postgresql+psycopg2://u@{DB_HOST}/app?sslmode=require&password={FAKE}",  # passed to connect()
        f"postgresql://u@{DB_HOST}/app?dsn={FAKE_DSN}",  # merged into the connection string
    ],
)
def test_db_url_masks_what_libpq_would_receive(url):
    sent = libpq_password(url)
    rendered = _loggable_db_url(url)
    assert "55word" in sent  # the case really carries one
    assert sent not in rendered and "55word" not in rendered
    assert DB_HOST in rendered


@pytest.mark.parametrize(
    "url, key",
    [
        (f"rediss://default:{FAKE}@{REDIS_HOST}:25061/0", "password"),
        (f"rediss://default:{FAKE_WITH_AT}@{REDIS_HOST}:25061/0", "password"),  # urllib splits at the last "@"
        (f"redis://:{FAKE}@{REDIS_HOST}:6379/0", "password"),
        (f"redis://{REDIS_HOST}:6379/0?password={FAKE}", "password"),
        (f"unix:///var/run/redis.sock?db=0&password={FAKE}", "password"),  # the documented socket form
        (f"rediss://{REDIS_HOST}:6380/0?ssl_keyfile=/k.pem&ssl_password={FAKE}", "ssl_password"),  # key passphrase
    ],
)
def test_redis_url_masks_what_redis_py_would_send(url, key):
    sent = redis_parse_url(url)[key]
    rendered = _loggable_redis_url(url)
    assert "55word" in sent  # the case really carries one
    assert sent not in rendered and "55word" not in rendered
    assert REDIS_HOST in rendered or "/var/run/redis.sock" in rendered


@pytest.mark.parametrize(
    "masker, url",
    [
        (_loggable_db_url, "postgresql://u@127.0.0.1:54329/shuffify_harness"),
        (_loggable_redis_url, "redis://127.0.0.1:63799/0"),
        (_loggable_redis_url, "unix:///tmp/redis.sock"),
    ],
)
def test_url_without_a_password_is_unchanged(masker, url):
    assert masker(url) == url


@pytest.mark.parametrize("masker", [_loggable_db_url, _loggable_redis_url])
def test_unparseable_value_is_not_echoed(masker):
    """A value that cannot be parsed may still hold a password."""
    assert "55word" not in masker(f"not a url :: {FAKE}")


@pytest.mark.parametrize(
    "password",
    [
        FAKE_WITH_AT,  # the rest lands in the host
        "fake@ss/55word",  # ... in the database name
        "fake@ss?a=55word",  # ... in the query
    ],
)
def test_db_url_with_unencoded_at_is_not_echoed(password):
    """SQLAlchemy ends the password at the first "@"; wherever the rest lands, it is not logged."""
    assert "55word" not in _loggable_db_url(f"postgresql://u:{password}@{DB_HOST}/app")


def test_database_startup_log_omits_the_password(monkeypatch, caplog):
    """Boot the real factory with a password-bearing URL and read what it logs."""
    from config import TestConfig
    from shuffify import create_app
    from shuffify.models.db import db

    monkeypatch.setattr(
        TestConfig, "SQLALCHEMY_DATABASE_URI", f"postgresql+psycopg2://shuffify:{FAKE}@{DB_HOST}:5432/app"
    )
    # TestConfig builds tables directly; skip that so nothing connects to the
    # (unreachable) host and the factory reaches the log line.
    monkeypatch.setattr(db, "create_all", lambda *a, **k: None)

    with caplog.at_level(logging.DEBUG):
        create_app("testing")

    # The positive match proves the line was logged, so the negative one
    # cannot pass just because nothing was.
    assert f"SQLAlchemy database initialized: postgresql+psycopg2://shuffify:***@{DB_HOST}" in caplog.text
    assert "55word" not in caplog.text


def test_redis_startup_log_omits_the_password(monkeypatch, caplog):
    app = Flask(__name__)
    # A password with an unencoded "@", which a SQLAlchemy-based masker leaks.
    app.config["REDIS_URL"] = f"rediss://default:{FAKE_WITH_AT}@{REDIS_HOST}:25061/0"
    monkeypatch.setattr(shuffify, "_create_redis_client", lambda url: MagicMock(spec=redis.Redis))

    with caplog.at_level(logging.INFO):
        shuffify._init_redis(app)

    assert f"Redis session storage configured: rediss://***@{REDIS_HOST}:25061/0" in caplog.text
    assert "55word" not in caplog.text
