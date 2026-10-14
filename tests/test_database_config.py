"""Database URL compatibility with the PostgreSQL driver shipped by Shuffify."""

import psycopg2
import pytest
from sqlalchemy import create_engine

from config import _resolve_database_url


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
@pytest.mark.parametrize("source", ["environment", "fallback"])
def test_bare_postgres_constructs_engine_with_installed_driver(
    monkeypatch, scheme, source
):
    """Engine initialization must work without psycopg3 or a database connection."""
    url = f"{scheme}://fixture_user:fixture_password@db.example.test:5432/shuffify"
    if source == "environment":
        monkeypatch.setenv("DATABASE_URL", url)
        fallback = "sqlite:///:memory:"
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        fallback = url

    engine = create_engine(_resolve_database_url(fallback))
    try:
        assert engine.dialect.driver == "psycopg2"
        assert engine.dialect.dbapi is psycopg2
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_bare_postgres_preserves_credentials_and_connection_options(
    monkeypatch, scheme
):
    suffix = (
        "fixture_user:p%40ss%2Fword@db.example.test:5432/shuffify"
        "?sslmode=require&options=-c%20statement_timeout%3D5000"
        "&application_name=shuffify%2Frecovery"
    )
    monkeypatch.setenv("DATABASE_URL", f"{scheme}://{suffix}")

    assert _resolve_database_url("sqlite:///:memory:") == (
        f"postgresql+psycopg2://{suffix}"
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg2://fixture_user@db.example.test/shuffify",
        "postgresql+psycopg://fixture_user@db.example.test/shuffify",
        "postgresql+pg8000://fixture_user@db.example.test/shuffify",
        "sqlite:///:memory:",
        "sqlite:////tmp/shuffify-fixture.db",
        "",
    ],
)
def test_explicit_driver_sqlite_and_empty_urls_are_preserved(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)

    assert _resolve_database_url("sqlite:///fallback.db") == url


@pytest.mark.parametrize(
    "fallback",
    [
        "sqlite:///:memory:",
        "sqlite:///shuffify_dev.db",
        "postgresql+psycopg2://fixture_user@db.example.test/shuffify",
    ],
)
def test_missing_database_url_preserves_non_bare_postgres_fallback(
    monkeypatch, fallback
):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert _resolve_database_url(fallback) == fallback
