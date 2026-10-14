"""Boolean server defaults must be valid on PostgreSQL.

They are checked where they are declared: in the models, which build every new
database, and in the revisions, which change existing ones.

PostgreSQL reads an unquoted ``1`` as an integer and refuses it as a BOOLEAN
default, while SQLite reads it as true, so a default like ``sa.text('1')``
works on SQLite and fails on PostgreSQL.

This is a static check so it runs in every CI job without a database. A
boolean default must be one of:

  * ``sa.true()`` / ``sa.false()``  -- rendered per dialect (preferred)
  * ``sa.text('true')`` / ``sa.text('false')``
  * a plain string such as ``'0'`` or ``'false'`` -- rendered as a quoted
    literal, which PostgreSQL accepts as boolean input
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = (ROOT / "shuffify" / "models", ROOT / "migrations" / "versions")

BOOLEAN_TEXT_OK = {"true", "false"}


def _attr_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_boolean_type(node):
    target = node.func if isinstance(node, ast.Call) else node
    return (_attr_name(target) or "").lower() == "boolean"


def _keyword(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _boolean_defaults():
    """Yield (location, server_default node) for every boolean column default."""
    for path in sorted(p for source in SOURCES for p in source.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _attr_name(node.func)
            default = _keyword(node, "server_default")
            if default is None:
                continue
            if name == "Column":
                typed = [a for a in node.args if _is_boolean_type(a)] or [
                    v for v in [_keyword(node, "type_")] if v is not None and _is_boolean_type(v)
                ]
            elif name == "alter_column":
                typed = [
                    v
                    for v in (_keyword(node, "type_"), _keyword(node, "existing_type"))
                    if v is not None and _is_boolean_type(v)
                ]
            else:
                continue
            if typed:
                yield f"{path.relative_to(ROOT)}:{node.lineno}", default


def _is_postgres_safe(default):
    if isinstance(default, ast.Constant):
        # A plain string becomes a quoted literal; None drops the default.
        return default.value is None or isinstance(default.value, str)
    if isinstance(default, ast.Call):
        func = _attr_name(default.func)
        if func in ("true", "false"):
            return True
        if func == "text" and default.args and isinstance(default.args[0], ast.Constant):
            literal = str(default.args[0].value).strip()
            # Single quotes make a string literal. Double quotes would make an
            # identifier, which is not a default value at all.
            return literal.lower() in BOOLEAN_TEXT_OK or literal.startswith("'")
    return False


def test_boolean_server_defaults_are_postgres_safe():
    unsafe = [
        f"{where}: {ast.unparse(default)}" for where, default in _boolean_defaults() if not _is_postgres_safe(default)
    ]
    assert not unsafe, (
        "Boolean server_default that PostgreSQL rejects (an unquoted integer is not a "
        "boolean). Use sa.true() / sa.false():\n  " + "\n  ".join(unsafe)
    )


def test_the_scan_finds_boolean_defaults():
    """Guard the check above against passing because it matched nothing."""
    assert len(list(_boolean_defaults())) >= 1


def test_the_scan_recognises_every_spelling_of_the_type():
    """Hand-written columns say Boolean; types rendered from a database say BOOLEAN."""
    for expr in ("sa.Boolean()", "sa.BOOLEAN()", "db.Boolean", "postgresql.BOOLEAN()"):
        assert _is_boolean_type(ast.parse(expr, mode="eval").body), expr
    assert not _is_boolean_type(ast.parse("sa.Integer()", mode="eval").body)


def test_the_check_rejects_an_unquoted_integer():
    """An unquoted integer default is flagged; quoted and dialect-rendered ones are not."""
    def check(expr):
        return _is_postgres_safe(ast.parse(expr, mode="eval").body)

    assert not check("sa.text('1')")
    assert not check("sa.text('\"1\"')")
    assert check("sa.true()")
    assert check("sa.text(\"'1'\")")
