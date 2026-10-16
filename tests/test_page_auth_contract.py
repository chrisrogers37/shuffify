"""API auth guards consume injected identities and never render server pages."""

import ast
import pathlib

import pytest

ROUTES_DIR = pathlib.Path(__file__).resolve().parent.parent / "shuffify" / "routes"


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


def test_backend_routes_do_not_render_server_pages():
    handlers = list(_route_handlers())
    assert handlers
    assert not [(file, name) for file, name, _, body in handlers if "render_template(" in body]


# Both guards inject their results as keyword arguments. A handler that
# re-derives one of them runs the same query twice per request: get_db_user()
# has no request-level cache, so the second call is a second round trip.
INJECTED_BY = {
    "require_auth_and_db": {"api", "user"},
    "require_session_and_db": {"user"},
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
    assert "shuffle" in {handler[1] for handler in _guarded_handlers()}


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


API_PATHS = [
    "/api/activity", "/api/library", "/api/routines", "/api/settings",
    "/api/workshop/abc123", "/api/playlist/abc123/setup/context",
]


@pytest.mark.parametrize("path", API_PATHS)
def test_unauthenticated_context_returns_json_without_provider_access(path, client):
    response = client.get(path)
    assert response.status_code == 401
    assert response.is_json and response.json["success"] is False
    assert response.headers["Cache-Control"] == "no-store"
