"""Engine options: bound values stay out of exception text under every profile."""

import pytest

from config import Config, DevConfig, HarnessConfig, ProdConfig, TestConfig


def test_prod_options_hide_parameters_and_keep_the_rest(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    options = ProdConfig.SQLALCHEMY_ENGINE_OPTIONS
    assert options["hide_parameters"] is True
    assert {key: options[key] for key in ("pool_size", "pool_recycle", "pool_pre_ping")} == {
        "pool_size": 5, "pool_recycle": 300, "pool_pre_ping": True,
    }
    assert "connect_args" in options
    assert set(options) == {"hide_parameters", "pool_size", "pool_recycle", "pool_pre_ping", "connect_args"}


@pytest.mark.parametrize("profile", [Config, DevConfig, TestConfig, HarnessConfig])
def test_every_profile_hides_parameters(profile):
    assert profile.SQLALCHEMY_ENGINE_OPTIONS["hide_parameters"] is True
