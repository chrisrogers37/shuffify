"""Legacy fixture writes are refused before any client or database access outside the harness."""

import importlib
from unittest.mock import patch

import pytest

from config import HarnessConfig, HarnessSafetyError


def test_legacy_source_fixture_checks_real_harness_guard_before_any_io(monkeypatch):
    assert importlib.util.find_spec("tests.e2e.legacy_fixtures") is not None, "Guarded legacy fixture is missing"
    module = importlib.import_module("tests.e2e.legacy_fixtures")
    monkeypatch.setattr(HarnessConfig, "SQLALCHEMY_DATABASE_URI", "postgresql://outside.invalid/production")
    with patch.object(module, "create_engine", autospec=True) as connect:
        with pytest.raises(HarnessSafetyError, match="Refusing to start the harness config"):
            module.seed_unavailable_legacy_source(None, "target", 1, "source", legacy_ids=["source"])
    connect.assert_not_called()
