"""SPOTIFY_BASE_URL reroutes every Spotify URL to the harness, and changes nothing unset.

The production expectations are literals on purpose: deriving them from the
same constants would pass vacuously.
"""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from shuffify.spotify.endpoints import SpotifyBaseURLError, local_origin

REPO_ROOT = Path(__file__).resolve().parent.parent

PRODUCTION = {
    "api": "https://api.spotify.com/v1",
    "authorize": "https://accounts.spotify.com/authorize",
    "token": "https://accounts.spotify.com/api/token",
    "revoke": "https://accounts.spotify.com/api/revoke",
    "scraper_embed": "https://open.spotify.com/embed/playlist/{playlist_id}",
    "scraper_public": "https://open.spotify.com/playlist/{playlist_id}",
    "metadata_embed": "https://open.spotify.com/embed/playlist/{playlist_id}",
    "csp_form_action": "form-action 'self' https://accounts.spotify.com",
}


def _spotify_urls(csp_header):
    """Every Spotify URL the app builds, plus the CSP form-action directive."""
    from tests.spotify_urls import current

    return {
        **current(),
        "csp_form_action": next(d for d in csp_header.split("; ") if d.startswith("form-action")),
    }


def test_the_url_table_names_every_spotify_url_shuffify_binds():
    """Every module- or class-level string in shuffify that starts with a
    Spotify origin is in tests/spotify_urls.py, which the fake-Spotify
    contract tests repoint -- and nothing else is. A URL built inside a
    function is left to that suite's socket guard."""
    import importlib
    import inspect
    import pkgutil

    import shuffify
    from shuffify.spotify import endpoints
    from tests.spotify_urls import SPOTIFY_URLS

    bases = (endpoints.API_BASE_URL, endpoints.ACCOUNTS_BASE_URL, endpoints.OPEN_BASE_URL)
    bound = set()
    for info in pkgutil.walk_packages(shuffify.__path__, "shuffify."):
        module = importlib.import_module(info.name)
        if module is endpoints:
            continue
        owners = [(module, "")] + [
            (cls, f"{cls.__name__}.") for cls in vars(module).values()
            if inspect.isclass(cls) and cls.__module__ == module.__name__
        ]
        for owner, prefix in owners:
            for name, value in vars(owner).items():
                # A base imported under its own name is not a URL anything calls.
                if isinstance(value, str) and value.startswith(bases) and getattr(endpoints, name, None) is not value:
                    bound.add((module.__name__, prefix + name))
    assert bound == {(module, attribute) for module, attribute, _ in SPOTIFY_URLS.values()}


def test_unset_every_url_is_production(client):
    assert "SPOTIFY_BASE_URL" not in os.environ, "unset SPOTIFY_BASE_URL to run the unit tests"
    csp = client.get("/health").headers["Content-Security-Policy"]
    assert _spotify_urls(csp) == PRODUCTION


def test_override_moves_every_url_to_the_local_origin():
    """The constants bind at import, so the override needs a fresh interpreter."""
    probe = textwrap.dedent(
        """
        import json, os
        import pytest  # noqa: F401 -- config.py skips load_dotenv() under pytest
        os.environ.setdefault("SPOTIFY_CLIENT_ID", "probe")
        os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "probe")
        from shuffify import create_app
        from tests.test_spotify_endpoints import _spotify_urls
        csp = create_app("testing").test_client().get("/health").headers["Content-Security-Policy"]
        print(json.dumps(_spotify_urls(csp)))
        """
    )
    fake = "http://127.0.0.1:8900"
    # No developer .env values (see the probe's first import), and nothing that
    # would reach a real Redis or Sentry from this subprocess.
    env = {**os.environ, "SPOTIFY_BASE_URL": fake, "REDIS_URL": "", "SENTRY_DSN": ""}
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "api": f"{fake}/v1",
        "authorize": f"{fake}/authorize",
        "token": f"{fake}/api/token",
        "revoke": f"{fake}/api/revoke",
        "scraper_embed": f"{fake}/embed/playlist/{{playlist_id}}",
        "scraper_public": f"{fake}/playlist/{{playlist_id}}",
        "metadata_embed": f"{fake}/embed/playlist/{{playlist_id}}",
        "csp_form_action": f"form-action 'self' {fake}",
    }


def test_non_loopback_override_fails_at_import(monkeypatch):
    """A remote override must stop the process, not warn: the secret goes there.

    Loads a fresh, unregistered copy of the module (it imports only the
    standard library), so nothing already imported is affected.
    """
    spec = importlib.util.spec_from_file_location("_endpoints_probe", REPO_ROOT / "shuffify/spotify/endpoints.py")
    monkeypatch.setenv("SPOTIFY_BASE_URL", "https://spotify-proxy.example.com")
    with pytest.raises(Exception, match="bare loopback origin"):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


@pytest.mark.parametrize(
    "value",
    [
        "https://api.spotify.com",  # real Spotify is not an override target either
        "http://10.0.0.5:8900",
        "http://localhost.example.com:8900",
        "http://127.attacker.example:8900",  # a DNS name, not a loopback address
        "http://127.0.0.1:8900/v1",  # an origin has no path
        "http://127.0.0.1:8900?x=1",
        "http://user:pw@127.0.0.1:8900",  # it would end up in the CSP header
        "ftp://127.0.0.1:8900",
        "127.0.0.1:8900",  # no scheme
        # Anything that is not exactly host[:port] would land in the CSP header.
        "http://127.0.0.1:8900 *",
        "http://127.0.0.1:8900; frame-ancestors *",
        "http://[::1%25x;frame-ancestors *;sandbox allow-scripts]:8900",  # via an IPv6 zone ID
        "http://127.0.0.1:8900x",
        "http://127.0.0.1:99999",
    ],
)
def test_local_origin_refuses(value):
    with pytest.raises(SpotifyBaseURLError):
        local_origin(value)


@pytest.mark.parametrize(
    "value, origin",
    [
        ("http://127.0.0.1:8900", "http://127.0.0.1:8900"),
        ("http://127.0.0.1:8900/", "http://127.0.0.1:8900"),
        ("http://localhost:8900", "http://localhost:8900"),
        ("http://[::1]:8900", "http://[::1]:8900"),
        ("https://127.0.0.5", "https://127.0.0.5"),
        (None, None),
        ("", None),
    ],
)
def test_local_origin_accepts_loopback(value, origin):
    assert local_origin(value) == origin


def test_production_config_refuses_the_override(monkeypatch):
    from config import validate_required_env_vars

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SECRET_KEY", "key")
    validate_required_env_vars("production")  # baseline: passes without it

    monkeypatch.setenv("SPOTIFY_BASE_URL", "http://127.0.0.1:8900")
    with pytest.raises(ValueError, match="SPOTIFY_BASE_URL"):
        validate_required_env_vars("production")
