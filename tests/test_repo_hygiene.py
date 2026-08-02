"""Guards against local environment artefacts reaching this public repo.

A virtualenv was committed here once (53 files, `.venv-mason/`). It was not
caught by `.gitignore`, which matched `venv/` but not a dot-prefixed suffixed
name, and it was not caught by review either -- three people read the PR and
looked past the file list at the substance.

Review is the wrong instrument for this. A human skims a 53-file diff and
reads the two files that matter; a test reads all 53. So the invariant lives
here instead of in anyone's attention.

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
# directory name nobody anticipated, which is the case that actually happened.
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
        "Local environment artefacts are tracked in a public repo:\n  "
        + "\n  ".join(str(p) for p in sorted(offenders)[:20])
        + "\nRemove with `git rm -r --cached <dir>` and confirm .gitignore "
        "covers the name."
    )


def test_no_tracked_file_is_an_activate_script():
    """`bin/activate` and friends embed the absolute path of the build host.

    This is the specific disclosure that made the committed venv a problem
    rather than merely untidy: every console-script shebang and every activate
    variant carries the full path of the machine that created it.
    """
    tracked = _tracked_files()

    offenders = [p for p in tracked if p.stem == "activate" and p.parent.name in {"bin", "Scripts"}]

    assert not offenders, "Virtualenv activate scripts are tracked; these embed absolute host paths:\n  " + "\n  ".join(
        str(p) for p in sorted(offenders)
    )


def test_gitignore_covers_common_virtualenv_names():
    """The patterns must cover names other than the one we happened to use.

    `venv/` alone did not match `.venv-mason/`. The next person will not use
    either spelling, so this pins the family rather than the instance.
    """
    candidates = [
        ".venv/probe",
        ".venv-mason/probe",
        ".venv-someoneelse/probe",
        "venv/probe",
        "venv-alex/probe",
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
