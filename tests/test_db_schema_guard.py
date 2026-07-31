"""Tests for the production schema guard (SR-018).

These run against the repository's real ``migrations/`` directory rather than
a synthetic chain -- the head revision the guard compares against is the one
that actually ships, so a migration added without a corresponding stamp is
caught here.
"""

import os

import pytest
from flask import Flask
from sqlalchemy import text

from shuffify import (
    MIGRATION_STEP_VAR,
    SCHEMA_DRIFT_OVERRIDE_VAR,
    SchemaOutOfDateError,
    _init_database,
    _schema_revision_state,
    _verify_schema_at_head,
)

MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations"
)


@pytest.fixture
def real_head():
    """The actual head revision of the shipped migration chain."""
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory(MIGRATIONS_DIR).get_heads()
    assert len(heads) == 1, f"expected a single migration head, got {heads}"
    return heads[0]


@pytest.fixture
def schema_app(tmp_path):
    """A non-TESTING app on a real file-backed SQLite database.

    TestConfig sets TESTING=True, which routes _init_database to create_all()
    and never reaches the guard. The guard needs a real engine it can stamp.
    """
    from shuffify.models.db import db

    app = _bare_app(tmp_path)
    db.init_app(app)
    with app.app_context():
        yield app


def _bare_app(tmp_path, name="schema.db", migrate_on_startup=False):
    """A non-TESTING app with no SQLAlchemy instance registered yet.

    _init_database calls db.init_app itself; binding it here first would make
    that call raise, and the generic handler would absorb it.
    """
    app = Flask(__name__)
    app.config["TESTING"] = False
    app.config["MIGRATE_ON_STARTUP"] = migrate_on_startup
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path / name}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    return app


@pytest.fixture
def routing_calls(monkeypatch):
    """Spy on which schema strategy _init_database dispatches to."""
    calls = []
    monkeypatch.setattr("shuffify._upgrade_schema", lambda: calls.append("upgrade"))
    monkeypatch.setattr(
        "shuffify._verify_schema_at_head", lambda _dir: calls.append("verify")
    )
    return calls


@pytest.fixture
def production_env(monkeypatch, tmp_path):
    """Environment for booting a real create_app("production")."""

    def _configure(db_name):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test_client_id")
        monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test_client_secret")
        monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://localhost:8000/callback")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / db_name}")
        monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    return _configure


@pytest.fixture(autouse=True)
def _clear_override():
    """Keep the break-glass override out of unrelated tests."""
    for var in (SCHEMA_DRIFT_OVERRIDE_VAR, MIGRATION_STEP_VAR):
        os.environ.pop(var, None)
    yield
    for var in (SCHEMA_DRIFT_OVERRIDE_VAR, MIGRATION_STEP_VAR):
        os.environ.pop(var, None)


def _stamp(app, revision):
    """Write ``revision`` into alembic_version, as a real upgrade would."""
    from shuffify.models.db import db

    with db.engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL)"
            )
        )
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:r)"),
            {"r": revision},
        )


class TestSchemaRevisionState:
    """_schema_revision_state reads real state from both sides."""

    def test_reads_head_from_shipped_migration_chain(self, schema_app, real_head):
        _, heads = _schema_revision_state(MIGRATIONS_DIR)
        assert heads == {real_head}

    def test_current_is_none_on_never_migrated_database(self, schema_app):
        current, _ = _schema_revision_state(MIGRATIONS_DIR)
        assert current is None

    def test_current_reflects_stamped_revision(self, schema_app, real_head):
        _stamp(schema_app, real_head)
        current, _ = _schema_revision_state(MIGRATIONS_DIR)
        assert current == real_head


class TestVerifySchemaAtHead:
    """The guard itself."""

    def test_passes_when_stamped_at_head(self, schema_app, real_head):
        _stamp(schema_app, real_head)
        _verify_schema_at_head(MIGRATIONS_DIR)  # must not raise

    def test_raises_when_database_never_migrated(self, schema_app):
        with pytest.raises(SchemaOutOfDateError) as exc:
            _verify_schema_at_head(MIGRATIONS_DIR)
        assert "never migrated" in str(exc.value)

    def test_raises_when_stamped_behind_head(self, schema_app, real_head):
        _stamp(schema_app, "0000deadbeef")
        with pytest.raises(SchemaOutOfDateError) as exc:
            _verify_schema_at_head(MIGRATIONS_DIR)
        assert "0000deadbeef" in str(exc.value)
        assert real_head in str(exc.value)

    def test_message_names_the_override(self, schema_app):
        with pytest.raises(SchemaOutOfDateError) as exc:
            _verify_schema_at_head(MIGRATIONS_DIR)
        assert SCHEMA_DRIFT_OVERRIDE_VAR in str(exc.value)


class TestBreakGlassOverride:
    """SR-034 precedent: the guard must be clearable without a code change."""

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_override_downgrades_failure_to_a_log(self, schema_app, caplog, value):
        os.environ[SCHEMA_DRIFT_OVERRIDE_VAR] = value
        with caplog.at_level("ERROR"):
            _verify_schema_at_head(MIGRATIONS_DIR)  # must not raise
        assert SCHEMA_DRIFT_OVERRIDE_VAR in caplog.text

    @pytest.mark.parametrize("value", ["", "false", "no", "0", "maybe"])
    def test_non_truthy_override_still_fails(self, schema_app, value):
        os.environ[SCHEMA_DRIFT_OVERRIDE_VAR] = value
        with pytest.raises(SchemaOutOfDateError):
            _verify_schema_at_head(MIGRATIONS_DIR)


class TestInitDatabaseRouting:
    """_init_database picks its schema strategy from config, not a call site."""

    def test_verifies_when_migrate_on_startup_is_false(self, tmp_path, routing_calls):
        _init_database(_bare_app(tmp_path, migrate_on_startup=False))
        assert routing_calls == ["verify"]

    def test_migrates_when_migrate_on_startup_is_true(self, tmp_path, routing_calls):
        _init_database(_bare_app(tmp_path, migrate_on_startup=True))
        assert routing_calls == ["upgrade"]

    def test_production_config_verifies_rather_than_migrating(
        self, tmp_path, routing_calls
    ):
        """The shipped ProdConfig must resolve to the verify branch."""
        from config import ProdConfig

        app = _bare_app(tmp_path, name="prod.db")
        app.config["MIGRATE_ON_STARTUP"] = ProdConfig.MIGRATE_ON_STARTUP
        _init_database(app)
        assert routing_calls == ["verify"]

    def test_testing_config_bypasses_both(self, tmp_path, routing_calls):
        """TESTING=True still builds tables straight from the models."""
        app = _bare_app(tmp_path, name="testing.db")
        app.config["TESTING"] = True
        _init_database(app)
        assert routing_calls == []


class TestSchemaErrorIsNotSwallowed:
    """The SR-018 regression: a stale schema must reach the caller.

    _init_database wraps everything in a broad `except Exception` that logs
    and continues. That handler is why the original bug served traffic on a
    stale schema. These tests pin the one exception that must escape it.
    """

    def test_schema_error_propagates_out_of_init_database(self, tmp_path, monkeypatch):
        def _boom(_dir):
            raise SchemaOutOfDateError("schema is behind head")

        monkeypatch.setattr("shuffify._verify_schema_at_head", _boom)

        with pytest.raises(SchemaOutOfDateError):
            _init_database(_bare_app(tmp_path))

    def test_schema_error_propagates_out_of_create_app(self, production_env):
        """End to end: a stale schema stops the app factory in production."""
        production_env("prod.db")

        from shuffify import create_app

        with pytest.raises(SchemaOutOfDateError):
            create_app("production")

    def test_other_database_errors_remain_non_fatal(self, tmp_path, monkeypatch):
        """Unchanged behaviour: non-schema init failures still degrade quietly."""

        def _boom(_dir):
            raise RuntimeError("connection reset")

        monkeypatch.setattr("shuffify._verify_schema_at_head", _boom)

        _init_database(_bare_app(tmp_path))  # must not raise


ENTRYPOINT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "docker-entrypoint.sh",
)


def run_entrypoint(tmp_path, stub_body, env=None):
    """Run the shipped entrypoint with a stub `flask` on PATH.

    stub_body is the shell body of the fake `flask`, so a test can control
    both what the migration step reports and whether it succeeds.
    """
    import subprocess

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "flask"
    stub.write_text(f"#!/bin/sh\n{stub_body}\n")
    stub.chmod(0o755)

    environ = dict(os.environ)
    environ["PATH"] = f"{stub_dir}:{environ['PATH']}"
    for var in (SCHEMA_DRIFT_OVERRIDE_VAR, MIGRATION_STEP_VAR):
        environ.pop(var, None)
    environ.update(env or {})

    return subprocess.run(
        ["sh", ENTRYPOINT, "echo", "SERVER-STARTED"],
        capture_output=True,
        text=True,
        env=environ,
    )


class TestDockerEntrypoint:
    """The shipped entrypoint script's migrate-then-exec contract.

    Exercises scripts/docker-entrypoint.sh directly with a stub `flask` on
    PATH, so the branch that decides whether a deploy proceeds is tested
    rather than described. No Docker required.
    """

    def test_successful_migration_starts_the_server(self, tmp_path):
        result = run_entrypoint(tmp_path, 'echo "stub flask: $*"')
        assert result.returncode == 0
        assert "SERVER-STARTED" in result.stdout
        assert "db upgrade" in result.stdout

    def test_failed_migration_stops_the_container(self, tmp_path):
        result = run_entrypoint(tmp_path, "exit 1")
        assert result.returncode == 1
        assert "SERVER-STARTED" not in result.stdout
        assert SCHEMA_DRIFT_OVERRIDE_VAR in result.stderr

    def test_override_starts_the_server_despite_a_failed_migration(self, tmp_path):
        """SR-034: recoverable from the platform console, no code change."""
        result = run_entrypoint(
            tmp_path, "exit 1", env={SCHEMA_DRIFT_OVERRIDE_VAR: "true"}
        )
        assert result.returncode == 0
        assert "SERVER-STARTED" in result.stdout

    def test_override_is_not_triggered_by_a_falsey_value(self, tmp_path):
        result = run_entrypoint(
            tmp_path, "exit 1", env={SCHEMA_DRIFT_OVERRIDE_VAR: "false"}
        )
        assert result.returncode == 1
        assert "SERVER-STARTED" not in result.stdout

    def test_migration_runs_with_the_scheduler_disabled(self, tmp_path):
        """`flask db upgrade` builds the whole app; APScheduler must stay off."""
        result = run_entrypoint(
            tmp_path,
            'echo "SCHEDULER_ENABLED=$SCHEDULER_ENABLED"',
            env={"SCHEDULER_ENABLED": "true"},
        )
        assert "SCHEDULER_ENABLED=false" in result.stdout


class TestMigrationStepIsExempt:
    """The migration step must not be blocked by the schema it will fix.

    `flask db upgrade` imports run.py and builds the whole app. Without an
    exemption the guard refuses the build, the upgrade never runs, and the
    schema can never reach head -- a deploy that cannot be unwedged by
    redeploying. This is the deadlock the container simulation caught.
    """

    def test_migration_step_bypasses_the_guard(self, schema_app):
        os.environ[MIGRATION_STEP_VAR] = "1"
        _verify_schema_at_head(MIGRATIONS_DIR)  # stale schema, must not raise

    @pytest.mark.parametrize("value", ["", "false", "0", "no"])
    def test_falsey_migration_step_does_not_bypass(self, schema_app, value):
        os.environ[MIGRATION_STEP_VAR] = value
        with pytest.raises(SchemaOutOfDateError):
            _verify_schema_at_head(MIGRATIONS_DIR)

    def test_app_factory_builds_for_the_migration_step(
        self, monkeypatch, production_env
    ):
        """Regression: production config + never-migrated database must build."""
        production_env("unmigrated.db")
        monkeypatch.setenv(MIGRATION_STEP_VAR, "1")

        from shuffify import create_app

        assert create_app("production") is not None

    def test_entrypoint_sets_the_migration_step_marker(self, tmp_path):
        """The exemption is only safe because the entrypoint is what sets it."""
        result = run_entrypoint(tmp_path, f'echo "MARKER=${MIGRATION_STEP_VAR}"')
        assert "MARKER=1" in result.stdout
