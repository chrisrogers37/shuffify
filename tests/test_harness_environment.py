"""Harness environments must exclude dotenv files as well as shell exports.

Exercise the shipped shell functions in a temporary checkout containing only
harmless sentinels. The probes use Flask's real CLI and app.run() dotenv paths;
only Werkzeug's server is replaced, so no app or service starts listening.
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SENTINELS = {
    "SHUFFIFY_DOTENV_SENTINEL": "from-env",
    "SHUFFIFY_FLASKENV_SENTINEL": "from-flaskenv",
    "SHUFFIFY_SHELL_SENTINEL": None,
}
PROBE = """
import json
import os
from unittest.mock import patch

from flask import Flask

NAMES = %r


def snapshot():
    return {name: os.environ.get(name) for name in NAMES}


at_import = snapshot()
app = Flask(__name__)


def report():
    print("DOTENV_PROBE=" + json.dumps({"imported": at_import, "running": snapshot()}))


app.cli.command("snapshot")(report)


if __name__ == "__main__":
    with patch("werkzeug.serving.run_simple") as server:
        app.run(use_reloader=False)
        server.assert_called_once()
    report()
""" % (tuple(SENTINELS),)


@pytest.fixture(scope="module")
def bash():
    """The harness requires Bash 4.4+, including on macOS."""
    for candidate in ("/opt/homebrew/bin/bash", shutil.which("bash")):
        if candidate and Path(candidate).is_file():
            version = subprocess.run(
                [candidate, "-c", "(( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4) ))"],
                capture_output=True,
                timeout=10,
            )
            if version.returncode == 0:
                return candidate
    pytest.skip("the harness requires Bash 4.4+")


def harness_environment(tmp_path, bash, launcher):
    """Capture env -i arguments from the actual functions without dispatching setup."""
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    source = (REPO_ROOT / "scripts" / "harness.sh").read_text()
    definitions, separator, _ = source.partition('\ncase "${1:-}" in\n')
    assert separator, "locate the harness command dispatcher before sourcing its functions"
    script = scripts / "harness.sh"
    script.write_text(definitions)
    state = checkout / ".harness"
    state.mkdir()
    (state / "secrets.env").write_text("SECRET_KEY=harmless-test-secret\nWORKER_CONTROL_TOKEN=harmless-control-token\n")
    shutil.copytree(REPO_ROOT / "harness" / "netguard", checkout / "harness" / "netguard")

    # run_checks() performs unrelated artifact/lint checks before its env -i.
    # Stub only those commands; the environment construction stays unchanged.
    bin_dir = checkout / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text('#!/bin/sh\ncase "$1" in scripts/*) exit 0 ;; esac\nexec "$HARNESS_TEST_PYTHON" "$@"\n')
    python.chmod(0o755)
    for command in (bin_dir / "ruff", scripts / "build-css.sh"):
        command.write_text("#!/bin/sh\nexit 0\n")
        command.chmod(0o755)

    capture = tmp_path / "command.args"
    result = subprocess.run(
        [
            bash,
            "-c",
            textwrap.dedent("""
                source "$1"
                capture="$4"
                env() { printf '%s\\0' env "$@" > "$capture"; }
                stop_proc() { :; }
                launch_proc() { "${HARNESS_CMD[@]}"; }
                case "$2" in
                    harness) run_harness "$3" -- "$3" ;;
                    fake) start_fake_spotify "$3" ;;
                    api) start_app "$3" ;;
                    worker) start_worker "$3" ;;
                    frontend) start_frontend "$3" ;;
                    checks) run_checks "$3" ;;
                esac
            """),
            "harness-environment-test",
            str(script),
            launcher,
            str(python),
            str(capture),
        ],
        cwd=checkout,
        env={
            "PATH": os.defpath,
            "HOME": str(tmp_path),
            "HARNESS_TEST_PYTHON": sys.executable,
            "SHUFFIFY_SHELL_SENTINEL": "from-shell",
            "FLASK_SKIP_DOTENV": "0",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    command = capture.read_text().rstrip("\0").split("\0")
    assert command[:2] == ["env", "-i"]
    return checkout, command[: command.index(str(python))]


def run_probe(checkout, environment, entrypoint):
    args = ["-m", "flask", "--app", "probe", "snapshot"] if entrypoint == "cli" else ["probe.py"]
    result = subprocess.run(
        [*environment, sys.executable, *args],
        cwd=checkout,
        env={**os.environ, "SHUFFIFY_SHELL_SENTINEL": "from-shell"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reports = [
        line.removeprefix("DOTENV_PROBE=") for line in result.stdout.splitlines() if line.startswith("DOTENV_PROBE=")
    ]
    assert len(reports) == 1, result.stdout
    return json.loads(reports[0])


@pytest.mark.parametrize("launcher", ["harness", "fake", "checks", "api", "worker", "frontend"])
@pytest.mark.parametrize("location", ["checkout", "ancestor"])
@pytest.mark.parametrize("entrypoint", ["cli", "app_run"])
def test_harness_excludes_dotenv_and_shell_variables(tmp_path, bash, launcher, location, entrypoint):
    checkout, environment = harness_environment(tmp_path, bash, launcher)
    dotenv_dir = checkout if location == "checkout" else tmp_path
    (dotenv_dir / ".env").write_text("SHUFFIFY_DOTENV_SENTINEL=from-env\n")
    (dotenv_dir / ".flaskenv").write_text("SHUFFIFY_FLASKENV_SENTINEL=from-flaskenv\n")
    (checkout / "probe.py").write_text(PROBE)

    # Positive control proves both files are discoverable by the installed
    # Flask/python-dotenv, before checking the harness actually suppresses them.
    load_dotenv = [arg for arg in environment if not arg.startswith("FLASK_SKIP_DOTENV=")]
    control = run_probe(checkout, load_dotenv, entrypoint)
    empty = dict.fromkeys(SENTINELS)
    assert control == {
        "imported": SENTINELS if entrypoint == "cli" else empty,
        "running": SENTINELS,
    }
    assert run_probe(checkout, environment, entrypoint) == {"imported": empty, "running": empty}


def test_harness_api_and_worker_share_local_private_control(tmp_path, bash):
    _checkout, arguments = harness_environment(tmp_path, bash, "harness")
    environment = dict(argument.split("=", 1) for argument in arguments if "=" in argument)
    assert environment["WORKER_CONTROL_URL"] == "http://127.0.0.1:8001"
    assert environment["WORKER_CONTROL_HOST"] == "127.0.0.1"
    assert environment["WORKER_CONTROL_PORT"] == "8001"
    assert environment["WORKER_CONTROL_TOKEN"] == "harmless-control-token"
    assert environment["SCHEDULER_ENABLED"] == "true"
    assert environment["SCHEDULER_TIMEZONE"] == "UTC"
    assert "8001" in environment["SHUFFIFY_NETGUARD_ALLOW_PORTS"].split(",")


def test_harness_worker_shutdown_allows_a_slow_drain_without_forcing_kill(tmp_path, bash):
    source = (REPO_ROOT / "scripts/harness.sh").read_text()
    definitions, separator, _ = source.partition('\ncase "${1:-}" in\n')
    assert separator
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "harness.sh"
    script.write_text(definitions)
    result = subprocess.run(
        [
            bash,
            "-c",
            textwrap.dedent("""
            source "$1"
            proc_pid() { printf '999999'; }
            sleep() { :; }
            probes=0
            kill() {
                case "$1" in
                    -0) probes=$((probes + 1)); (( probes < 30 )) ;;
                    -9) printf 'FORCED-KILL'; return 0 ;;
                    *) return 0 ;;
                esac
            }
            stop_proc worker
            (( probes >= 30 ))
        """),
            "harness-drain-test",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FORCED-KILL" not in result.stdout
