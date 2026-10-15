"""Check tracked files and ignore rules for local environment artifacts.

These assert against `git ls-files` -- what is *tracked* -- not the working
tree. An ignored-but-present venv is fine and expected; a tracked one is not.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directory names that mean "someone's local environment". Checked as whole
# path segments, so a legitimate file like `shuffify/services/env_service.py`
# does not match.
ENV_DIR_MARKERS = {
    "site-packages",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}

# The file every Python virtualenv puts at its root. Catches an env whose
# directory name nobody anticipated.
VENV_MARKER_FILES = {"pyvenv.cfg"}


def _tracked_files():
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git unavailable: {exc}")
    return [Path(p) for p in out.split("\0") if p]


def test_no_virtualenv_is_tracked():
    """No tracked file may sit inside a Python virtualenv."""
    tracked = _tracked_files()

    offenders = [p for p in tracked if p.name in VENV_MARKER_FILES or any(part in ENV_DIR_MARKERS for part in p.parts)]

    assert not offenders, (
        "Tracked files include local environment artifacts:\n  "
        + "\n  ".join(str(p) for p in sorted(offenders)[:20])
        + "\nRemove with `git rm -r --cached <dir>` and confirm .gitignore "
        "covers the name."
    )


def test_no_tracked_file_is_an_activate_script():
    """Tracked files must exclude virtualenv activation scripts and host paths."""
    tracked = _tracked_files()

    offenders = [p for p in tracked if p.stem == "activate" and p.parent.name in {"bin", "Scripts"}]

    assert not offenders, "Virtualenv activate scripts are tracked; these embed absolute host paths:\n  " + "\n  ".join(
        str(p) for p in sorted(offenders)
    )


def test_gitignore_covers_common_virtualenv_names():
    """The patterns must cover the family of virtualenv names, not one spelling.

    A bare `venv/` does not match a dot-prefixed or suffixed name, so this pins
    the family rather than the instance.
    """
    candidates = [
        ".venv/probe",
        ".venv-dev/probe",
        ".venv-someoneelse/probe",
        "venv/probe",
        "venv-test/probe",
        "env/probe",
        "unanticipated-name/pyvenv.cfg",
    ]

    not_ignored = []
    for rel in candidates:
        result = subprocess.run(
            ["git", "check-ignore", "-q", rel],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        # 0 = ignored, 1 = not ignored, 128 = error
        if result.returncode == 128:
            pytest.skip("git check-ignore unavailable")
        if result.returncode != 0:
            not_ignored.append(rel)

    assert not not_ignored, "These virtualenv paths are not ignored and could be committed:\n  " + "\n  ".join(
        not_ignored
    )
