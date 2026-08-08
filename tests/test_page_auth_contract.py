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
LOGIN_PAGE_FILE = "core.py"
LOGIN_PAGE_FUNC = "index"


def _iter_functions():
    """Yield (filename, FunctionDef) for every function defined in routes/."""
    for path in sorted(ROUTES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path.name, node


def _decorator_names(node):
    """The bare name of each decorator, ignoring any call arguments."""
    for dec in node.decorator_list:
        base = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(base, ast.Name):
            yield base.id
        elif isinstance(base, ast.Attribute):
            yield base.attr


def _route_handlers():
    """Yield (filename, funcname, decorator_sources, body_source) per route."""
    for fname, node in _iter_functions():
        decorators = [ast.unparse(d) for d in node.decorator_list]
        if not any(".route(" in d for d in decorators):
            continue
        yield fname, node.name, decorators, ast.unparse(node)


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
    if fname == LOGIN_PAGE_FILE and func == LOGIN_PAGE_FUNC:
        pytest.skip("the login page renders anonymously by design")
    assert any("require_auth_page" in d for d in decorators), (
        f"{fname}:{func} renders a template without @require_auth_page."
    )


# Both guards inject their results as keyword arguments. A handler that
# re-derives one of them runs the same query twice per request: get_db_user()
# has no request-level cache, so the second call is a second round trip.
INJECTED_BY = {
    "require_auth_page": {"api", "user", "spotify_profile"},
    "require_auth_and_db": {"api", "user"},
}

# The call a handler would re-derive each injected kwarg from.
REDERIVES = {
    "get_db_user": "user",
    "require_auth": "api",
    "get_user_data": "spotify_profile",
}


def _called_names(node):
    """Every function/method name called anywhere inside a node."""
    names = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _guarded_handlers():
    """Yield (filename, funcname, guard, declared_kwargs, called_names)."""
    for fname, node in _iter_functions():
        guard = next(
            (d for d in _decorator_names(node) if d in INJECTED_BY), None
        )
        if guard is None:
            continue
        declared = {
            a.arg
            for a in node.args.args + node.args.kwonlyargs
            if a.arg in INJECTED_BY[guard]
        }
        yield fname, node.name, guard, declared, _called_names(node)


def test_guarded_handlers_exist_to_check():
    """Guard against the collector silently matching nothing."""
    assert len(list(_guarded_handlers())) >= 70


@pytest.mark.parametrize(
    "fname,func,guard,declared,called",
    list(_guarded_handlers()),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_guarded_handler_does_not_rederive_injected_kwarg(fname, func, guard, declared, called):
    """A handler must consume what its guard injects, not query for it again."""
    redundant = sorted({REDERIVES[c] for c in called if c in REDERIVES} & declared)
    assert not redundant, (
        f"{fname}:{func} declares {redundant} -- which @{guard} already "
        f"injects -- but calls "
        f"{sorted(c for c in called if REDERIVES.get(c) in redundant)} to "
        "derive it again. That is a second query per request. Consume the "
        "injected keyword argument instead."
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
