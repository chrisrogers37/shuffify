"""Mutation test for the ruff configuration itself.

Comparing linters on the real tree cannot validate a lint config: a rule that
is **disabled** and a tree that is **clean** both report zero findings, and the
two are indistinguishable from the outside. That is how 28 preview-gated
pycodestyle rules -- E225, E231 and the whole E301-E306 blank-line family --
were once selected via ``select = ["E", ...]`` and silently skipped anyway.

So instead of asserting "no findings", this feeds *known* violations through
the repo's real config and asserts each one is caught. It fails if a config
change ever stops the gate looking, rather than waiting for the offending code
to reach main.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

# One snippet per rule, each violating exactly the rule it is keyed by.
VIOLATIONS = {
    "E225": "x=1\n",  # missing whitespace around operator
    "E231": "d = {'a':1}\n",  # missing whitespace after ':'
    "E302": "def a():\n    pass\ndef b():\n    pass\n",  # expected 2 blank lines
    "E301": "class C:\n    def a(self):\n        pass\n    def b(self):\n        pass\n",
    "E305": "def a():\n    pass\nx = 1\n",  # expected 2 blank lines after def
    "F401": "import os\n",  # unused import
    "I001": "import sys\nimport os\n\nprint(os, sys)\n",  # unsorted imports
}

RUFF = shutil.which("ruff")


def _codes_reported(source: str, tmp_path: Path) -> set[str]:
    """Run the repo's real ruff config over `source`, return the rule codes."""
    target = tmp_path / "sample.py"
    target.write_text(textwrap.dedent(source), encoding="utf-8")
    proc = subprocess.run(
        [
            RUFF,
            "check",
            "--no-cache",
            "--config",
            str(PYPROJECT),
            "--output-format",
            "json",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):  # 1 == violations found
        pytest.fail(f"ruff failed to run: {proc.stderr.strip()[:400]}")
    return {item.get("code") for item in json.loads(proc.stdout or "[]")}


@pytest.mark.skipif(RUFF is None, reason="ruff not installed")
@pytest.mark.parametrize("code,source", sorted(VIOLATIONS.items()))
def test_configured_gate_catches_violation(code, source, tmp_path):
    """Each rule the project relies on must actually fire on a real violation."""
    reported = _codes_reported(source, tmp_path)
    assert code in reported, (
        f"{code} was not reported by the project's ruff config. Selecting a rule "
        f"family is not enough -- some pycodestyle rules are preview-gated and "
        f"are skipped unless [tool.ruff.lint] preview = true. Reported: "
        f"{sorted(reported) or 'nothing'}"
    )


@pytest.mark.skipif(RUFF is None, reason="ruff not installed")
def test_preview_is_enabled():
    """The setting the rules above depend on, asserted directly.

    Kept alongside the behavioural checks so a future edit that drops `preview`
    fails with a message naming the cause, not just a list of missing codes.
    """
    assert "preview = true" in PYPROJECT.read_text(encoding="utf-8"), (
        "[tool.ruff.lint] preview = true is required: without it ruff silently "
        "skips 28 pycodestyle E-codes that flake8 previously enforced."
    )
