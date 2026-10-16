"""Split production API configuration requires its browser-facing origin."""

import pytest

from config import validate_required_env_vars


def set_production_credentials(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "fixture-client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "fixture-secret")
    monkeypatch.setenv("SECRET_KEY", "fixture-secret-key")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg2://fixture@localhost/shuffify"
    )
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)


def test_production_split_api_requires_public_origin(monkeypatch):
    set_production_credentials(monkeypatch)
    monkeypatch.setenv("SHUFFIFY_PROCESS_ROLE", "api")

    with pytest.raises(ValueError, match="PUBLIC_ORIGIN"):
        validate_required_env_vars("production")


def test_production_split_api_accepts_owner_supplied_public_origin(monkeypatch):
    set_production_credentials(monkeypatch)
    monkeypatch.setenv("SHUFFIFY_PROCESS_ROLE", "api")
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://shuffify-v2.vercel.app")

    validate_required_env_vars("production")


@pytest.mark.parametrize("role", ["worker", "", None])
def test_non_api_production_roles_do_not_require_browser_origin(monkeypatch, role):
    set_production_credentials(monkeypatch)
    if role is None:
        monkeypatch.delenv("SHUFFIFY_PROCESS_ROLE", raising=False)
    else:
        monkeypatch.setenv("SHUFFIFY_PROCESS_ROLE", role)

    validate_required_env_vars("production")


def test_development_api_does_not_require_public_origin(monkeypatch):
    monkeypatch.setenv("SHUFFIFY_PROCESS_ROLE", "api")
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "fixture-client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "fixture-secret")

    validate_required_env_vars("development")


@pytest.mark.parametrize("role", ["api", "worker"])
def test_production_split_services_require_owner_database_url(monkeypatch, role):
    set_production_credentials(monkeypatch)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SHUFFIFY_PROCESS_ROLE", role)
    if role == "api":
        monkeypatch.setenv("PUBLIC_ORIGIN", "https://shuffify-v2.vercel.app")

    with pytest.raises(ValueError, match="DATABASE_URL"):
        validate_required_env_vars("production")
