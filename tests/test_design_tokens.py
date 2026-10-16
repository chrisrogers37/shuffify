"""Design token validation rejects missing aliases and unsafe font references."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node is required for design-token validation")
    return executable


def run_node(node, script):
    result = subprocess.run([node, "-"], input=script, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mutation,expected", [
    ("values.spacing.semantic.control = 'missing'", "Unknown spacing alias"),
    ("values.fonts.roles.ui.family = []", "Invalid font family"),
    ("values.fonts.roles.ui.source = '../fonts/missing.woff2'", "font asset"),
    ("values.fonts.roles.ui.source = '../../outside.woff2'", "font asset"),
    ("values.fonts.roles.ui.source = 'https://example.com/font.woff2'", "font asset"),
])
def test_adapter_rejects_bad_spacing_and_font_references(node, mutation, expected):
    run_node(node, f"""
const assert = require('node:assert/strict');
const adapter = require('./assets/design');
const values = structuredClone(require('./assets/design/tokens.json'));
{mutation};
assert.throws(() => adapter.validateTokens(values),
              new RegExp({json.dumps(expected)}));
""")
