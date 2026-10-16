"""Tests for the production schema guard.

These run against the repository's real ``migrations/`` directory rather than
a synthetic chain -- the head revision the guard compares against is the one
that actually ships, so a migration added without a corresponding stamp is
caught here.
"""

import json
import os
import sys

import pytest
from flask import Flask
from sqlalchemy import text

from shuffify import (
    MIGRATION_STEP_VAR,
    SCHEMA_DRIFT_OVERRIDE_VAR,
    SchemaOutOfDateError,
    _env_is_true,
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
    """Environment for booting a real create_app("production").

    ProdConfig reads DATABASE_URL when config is imported, so the database is
    also set on the class.
    """
    from config import ProdConfig

    def _configure(db_name):
        url = f"sqlite:///{tmp_path / db_name}"
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test_client_id")
        monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test_client_secret")
        monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://localhost:8000/callback")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key")
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setattr(ProdConfig, "SQLALCHEMY_DATABASE_URI", url)
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

    def test_message_includes_the_production_migration_marker(self, schema_app):
        with pytest.raises(SchemaOutOfDateError) as exc:
            _verify_schema_at_head(MIGRATIONS_DIR)
        assert "SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1 flask db upgrade" in str(exc.value)


class TestBreakGlassOverride:
    """The guard must be clearable without a code change."""

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

    @pytest.mark.parametrize(
        "config",
        [{"TESTING": True}, {"MIGRATE_ON_STARTUP": True}, {"MIGRATE_ON_STARTUP": False}],
    )
    def test_the_migration_step_leaves_the_schema_to_the_step(
        self, tmp_path, routing_calls, monkeypatch, config
    ):
        """The step's own commands build, upgrade and stamp; the factory touches nothing."""
        from sqlalchemy import inspect

        from shuffify.models.db import db

        monkeypatch.setenv(MIGRATION_STEP_VAR, "1")
        app = _bare_app(tmp_path, name="step.db")
        app.config.update(config)
        _init_database(app)
        assert routing_calls == []
        with app.app_context():
            assert inspect(db.engine).get_table_names() == []


class TestSchemaErrorIsNotSwallowed:
    """A stale schema must reach the caller.

    _init_database wraps everything in a broad `except Exception` that logs
    and continues; left to it, a stale schema would serve traffic. These tests
    pin the one exception that must escape it.
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
        """Non-schema init failures degrade quietly."""

        def _boom(_dir):
            raise RuntimeError("connection reset")

        monkeypatch.setattr("shuffify._verify_schema_at_head", _boom)

        _init_database(_bare_app(tmp_path))  # must not raise


ENTRYPOINT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "docker-entrypoint.sh",
)


PREDEPLOY = os.path.join(os.path.dirname(ENTRYPOINT), "pre-deploy.sh")


def run_entrypoint(tmp_path, stub_body, env=None, *, script=ENTRYPOINT, command=None):
    """Run a real process boundary with controlled Flask outcome and environment."""
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
        ["sh", script, *(command if command is not None else ["echo", "SERVER-STARTED"])],
        capture_output=True,
        text=True,
        env=environ,
    )


class TestDockerEntrypoint:
    """Startup forwards the selected process without taking over schema ownership."""

    def test_startup_never_invokes_flask(self, tmp_path):
        result = run_entrypoint(tmp_path, 'echo "UNEXPECTED-FLASK"; exit 91')
        assert result.returncode == 0
        assert result.stdout == "SERVER-STARTED\n"

    def test_startup_does_not_invent_a_migration_marker(self, tmp_path):
        result = run_entrypoint(
            tmp_path, "exit 91", command=[sys.executable, "-c",
                f"import os; print(repr(os.getenv('{MIGRATION_STEP_VAR}')))"]
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "None"


class TestPredeploy:
    """A distinct upgrade failure blocks deployment without starting a service."""

    def test_runs_only_the_explicit_upgrade(self, tmp_path):
        result = run_entrypoint(
            tmp_path, 'echo "stub flask: $*"', script=PREDEPLOY, command=[]
        )
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["stub flask: db upgrade"]
        assert "SERVER-STARTED" not in result.stdout

    @pytest.mark.parametrize("override", ["true", "false"])
    def test_failed_upgrade_is_not_bypassed_by_the_runtime_guard_override(self, tmp_path, override):
        result = run_entrypoint(
            tmp_path, "exit 17", env={SCHEMA_DRIFT_OVERRIDE_VAR: override},
            script=PREDEPLOY, command=[]
        )
        assert result.returncode == 17
        assert "SERVER-STARTED" not in result.stdout

    def test_missing_schema_is_never_silently_bootstrapped(self, tmp_path):
        result = run_entrypoint(
            tmp_path, 'if [ "$1" = create-schema ]; then exit 91; fi; echo "stub flask: $*"',
            script=PREDEPLOY, command=[]
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "stub flask: db upgrade"

    def test_migration_runs_with_the_scheduler_disabled(self, tmp_path):
        result = run_entrypoint(
            tmp_path, 'echo "SCHEDULER_ENABLED=$SCHEDULER_ENABLED"',
            env={"SCHEDULER_ENABLED": "true"}, script=PREDEPLOY, command=[]
        )
        assert result.stdout.strip() == "SCHEDULER_ENABLED=false"


class TestMigrationStepIsExempt:
    """The migration step must not be blocked by the schema it will fix.

    `flask db upgrade` imports run.py and builds the whole app. Without an
    exemption the guard refuses the build, the upgrade never runs, and the
    schema can never reach head -- a deploy that cannot be unwedged by
    redeploying.
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
        """Production config + never-migrated database must build."""
        production_env("unmigrated.db")
        monkeypatch.setenv(MIGRATION_STEP_VAR, "1")

        from shuffify import create_app

        assert create_app("production") is not None

    def test_predeploy_sets_the_migration_step_marker(self, tmp_path):
        """Only the explicit migration process bypasses the schema guard."""
        result = run_entrypoint(
            tmp_path, f'echo "MARKER=${MIGRATION_STEP_VAR}"', script=PREDEPLOY, command=[]
        )
        markers = {line for line in result.stdout.splitlines() if line.startswith("MARKER=")}
        assert markers == {"MARKER=1"}


OVERRIDE_VALUES = [
    ("true", True),
    ("TRUE", True),
    ("1", True),
    ("yes", True),
    ("on", True),
    (" true", True),
    ("true ", True),
    ("  TRUE  ", True),
    ("\ttrue", True),
    ("", False),
    ("   ", False),
    ("false", False),
    ("no", False),
    ("0", False),
    ("maybe", False),
    ("truthy", False),
]


class TestSchemaDriftOverride:
    """The app factory evaluates the runtime guard override; startup preserves its value."""

    @pytest.mark.parametrize("value,expected", OVERRIDE_VALUES)
    def test_app_factory_reading(self, monkeypatch, value, expected):
        monkeypatch.setenv(SCHEMA_DRIFT_OVERRIDE_VAR, value)
        assert _env_is_true(SCHEMA_DRIFT_OVERRIDE_VAR) is expected

    @pytest.mark.parametrize("value,_expected", OVERRIDE_VALUES)
    def test_entrypoint_preserves_the_runtime_value_without_migrating(self, tmp_path, value, _expected):
        result = run_entrypoint(
            tmp_path, 'echo "UNEXPECTED-FLASK"; exit 91', env={SCHEMA_DRIFT_OVERRIDE_VAR: value},
            command=[sys.executable, "-c",
                     f"import json,os; print(json.dumps(os.getenv('{SCHEMA_DRIFT_OVERRIDE_VAR}')))"]
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == value


@pytest.fixture
def new_database_app(monkeypatch, production_env, tmp_path):
    """A production app on an empty database, built as the migration step builds it.

    Flask-Migrate finds migrations/ relative to the working directory, as the
    harness and the pre-deploy process run it. env.py applies alembic.ini's
    logging config, which would disable the loggers later tests capture from.
    """
    import logging.config

    production_env("new.db")
    monkeypatch.setenv(MIGRATION_STEP_VAR, "1")
    monkeypatch.chdir(os.path.dirname(MIGRATIONS_DIR))
    monkeypatch.setattr(logging.config, "fileConfig", lambda *args, **kwargs: None)

    from shuffify import create_app

    app = create_app("production")
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith(f"sqlite:///{tmp_path}")
    return app


def _tables(app):
    from sqlalchemy import inspect

    from shuffify.models.db import db

    with app.app_context():
        return set(inspect(db.engine).get_table_names())


def _revision(app):
    with app.app_context():
        current, _ = _schema_revision_state(MIGRATIONS_DIR)
    return current


class TestCreateSchema:
    """`flask create-schema` is how a database gets its schema."""

    def test_builds_a_new_database_and_stamps_it_at_head(self, new_database_app, real_head):
        from shuffify.models.db import db

        result = new_database_app.test_cli_runner().invoke(args=["create-schema"])

        assert result.exit_code == 0, result.output
        assert set(db.metadata.tables) <= _tables(new_database_app)
        assert _revision(new_database_app) == real_head

    def test_leaves_a_database_with_a_revision_as_it_is(self, new_database_app, real_head):
        from shuffify.models.db import db

        with new_database_app.app_context():
            _stamp(new_database_app, real_head)

        result = new_database_app.test_cli_runner().invoke(args=["create-schema"])

        assert result.exit_code == 0, result.output
        assert "nothing to build" in result.output
        assert not set(db.metadata.tables) & _tables(new_database_app)

    def test_refuses_model_tables_without_a_revision(self, new_database_app):
        from shuffify.models.db import db

        with new_database_app.app_context():
            db.metadata.tables["activity_log"].create(db.engine)

        result = new_database_app.test_cli_runner().invoke(args=["create-schema"])

        assert result.exit_code != 0
        assert "refusing to stamp" in result.output
        assert _revision(new_database_app) is None


class TestUpgradeNeverBuildsADatabase:
    """The base revision creates nothing, so a database joins the chain by stamp."""

    def test_upgrade_refuses_a_database_with_no_revision(self, new_database_app, caplog):
        from shuffify.models.db import db

        result = new_database_app.test_cli_runner().invoke(args=["db", "upgrade"])

        assert result.exit_code == 1
        assert "Refusing to upgrade a database that has no Alembic revision" in caplog.text
        assert _revision(new_database_app) is None
        assert not set(db.metadata.tables) & _tables(new_database_app)

    def test_a_refused_upgrade_leaves_the_database_buildable(
        self, new_database_app, real_head
    ):
        runner = new_database_app.test_cli_runner()
        runner.invoke(args=["db", "upgrade"])

        result = runner.invoke(args=["create-schema"])

        assert result.exit_code == 0, result.output
        assert _revision(new_database_app) == real_head

    def test_upgrade_at_head_changes_nothing(self, new_database_app, real_head):
        with new_database_app.app_context():
            _stamp(new_database_app, real_head)

        result = new_database_app.test_cli_runner().invoke(args=["db", "upgrade"])

        assert result.exit_code == 0, result.output
        assert _revision(new_database_app) == real_head
