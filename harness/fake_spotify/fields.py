"""Spotify's ``fields`` query parameter: return only the named parts of a response.

Spotify's syntax is a comma-separated list of keys, with parentheses to
select keys inside one (``items(track(name,href))``); selecting inside a list
applies to every element. That is all shuffify sends, so it is all the fake
models. The reference also allows dots (``items.added_by.id``) and a leading
``!`` to exclude a key; the fake refuses those with a 400 rather than guess.
"""

from typing import Any, Dict, Optional

# key -> what to keep inside it, or None for all of it
Selection = Dict[str, Optional["Selection"]]

MAX_DEPTH = 16  # deeper than any Spotify object; stops runaway recursion


class FieldsError(ValueError):
    pass


def parse(expr: str) -> Selection:
    selection, pos = _parse_list(expr, 0, 0)
    if pos != len(expr):
        raise FieldsError(f"unexpected {expr[pos]!r} at {pos} in fields={expr!r}")
    return selection


def _parse_list(expr: str, pos: int, depth: int):
    if depth > MAX_DEPTH:
        raise FieldsError(f"fields={expr!r} nests too deeply")
    selection: Selection = {}
    while True:
        start = pos
        while pos < len(expr) and expr[pos] not in ",()":
            pos += 1
        name = expr[start:pos].strip()
        if not name:
            raise FieldsError(f"empty field name in fields={expr!r}")
        if "." in name or name.startswith("!"):
            raise FieldsError(f"the fake does not model {name!r}: only keys and parentheses")
        nested = None
        if expr.startswith("(", pos):
            nested, pos = _parse_list(expr, pos + 1, depth + 1)
            if not expr.startswith(")", pos):
                raise FieldsError(f"unclosed '(' in fields={expr!r}")
            pos += 1
        selection[name] = nested
        if not expr.startswith(",", pos):
            return selection, pos
        pos += 1


def apply(value: Any, selection: Optional[Selection]) -> Any:
    if selection is None:
        return value
    if isinstance(value, list):
        return [apply(v, selection) for v in value]
    if not isinstance(value, dict):
        return value
    return {key: apply(value[key], nested) for key, nested in selection.items() if key in value}
