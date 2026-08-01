"""Contract tests for the HTML page auth guard (SR-027).

The failure this locks down is not a missing auth check -- both decorators
authenticate correctly. It is answering a **browser** with a JSON 401 body,
which renders as raw JSON instead of sending the visitor to the login page.

Two layers, deliberately:

* The AST check is the class-level guard. It fails when *any* future route
  renders a template behind the JSON decorator, including routes that do not
  exist yet -- which a per-route test cannot do.
* The behavioural check proves the guard actually redirects, so the AST check
  can never pass on a decorator that has stopped working.
"""

import ast
import pathlib

import pytest

ROUTES_DIR = pathlib.Path(__file__).resolve().parent.parent / "shuffify" / "routes"

# `/` renders the login page itself for anonymous visitors and is the target
# every other page redirects to, so it must not carry the redirecting guard --
# that would be an infinite redirect. It is the one legitimate hand-rolled case.
LOGIN_PAGE_ENDPOINT = ("core.py", "index")


def _route_handlers():
    """Yield (filename, funcname, decorator_sources, body_source) per route."""
    for path in sorted(ROUTES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if not any(".route(" in d for d in decorators):
                continue
            yield path.name, node.name, decorators, ast.unparse(node)


def _html_routes():
    for fname, func, decorators, body in _route_handlers():
        if "render_template(" in body:
            yield fname, func, decorators, body


def test_html_routes_exist_to_check():
    """Guard against the collector silently matching nothing."""
    assert len(list(_html_routes())) >= 5


@pytest.mark.parametrize(
    "fname,func,decorators",
    [(f, fn, d) for f, fn, d, _ in _html_routes()],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_html_route_does_not_use_json_auth_decorator(fname, func, decorators):
    """An HTML page behind @require_auth_and_db answers a browser with JSON."""
    assert not any("require_auth_and_db" in d for d in decorators), (
        f"{fname}:{func} renders a template but is guarded by "
        "@require_auth_and_db, which returns a JSON 401. An unauthenticated "
        "browser would render raw JSON instead of being redirected to the "
        "login page. Use @require_auth_page."
    )


@pytest.mark.parametrize(
    "fname,func,decorators",
    [(f, fn, d) for f, fn, d, _ in _html_routes()],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_html_route_is_guarded(fname, func, decorators):
    """Every HTML page uses the page guard, except the login page itself."""
    if (fname, func) == LOGIN_PAGE_ENDPOINT:
        pytest.skip("the login page renders anonymously by design")
    assert any("require_auth_page" in d for d in decorators), (
        f"{fname}:{func} renders a template without @require_auth_page."
    )


PAGE_PATHS = ["/activity", "/workshop", "/workshop/abc123", "/schedules", "/settings"]


@pytest.mark.parametrize("path", PAGE_PATHS)
def test_unauthenticated_page_redirects_rather_than_returning_json(path, client):
    """The behaviour the AST check is a proxy for."""
    resp = client.get(path)
    assert resp.status_code == 302, (
        f"{path} returned {resp.status_code} "
        f"({resp.headers.get('Content-Type')}) instead of redirecting."
    )
    assert "application/json" not in resp.headers.get("Content-Type", "")
