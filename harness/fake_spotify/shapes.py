"""Check requests and responses against the shapes from Spotify's OpenAPI schema.

shapes.json holds, for each operation shuffify calls, its query parameters,
its request body and the schema of each response. ``response_problems()`` and
``request_problems()`` report every way a request or response departs from
them: an undocumented field or parameter, a missing required one, a wrong
type, a null where none is allowed, a value outside an enum or a bound.

Spotify's schema describes the full API. The fake answers as Spotify answers
apps in Development Mode: those answers leave out some fields, a few of them
marked required in the schema. They are listed in DEV_MODE_REMOVED: the fake
must leave them out, and an absent one is not a missing required field.
"""

import json
import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

SHAPES = json.loads(Path(__file__).with_name("shapes.json").read_text())

# https://developer.spotify.com/documentation/web-api/references/changes/february-2026
DEV_MODE_REMOVED = {
    "PrivateUserObject": {"country", "email", "explicit_content", "followers", "product"},
    "TrackObject": {"available_markets", "popularity"},
    "SimplifiedAlbumObject": {"available_markets"},
}

# Where Spotify is known to behave differently from its schema, each with how
# we know. Applied to the loaded shapes, so the fake follows them.
#
# The schema allows limit=0; shuffify's archive view records Spotify rejecting
# it (shuffify/routes/playlist_pairs.py, "Spotify API rejects limit=0").
# offset is "the index of the first item to return", so never negative, and
# the Get Current User's Playlists reference gives "Maximum offset: 100.000".
for _operation in ("GET /me/playlists", "GET /playlists/{playlist_id}/items", "GET /search"):
    _query = SHAPES["operations"][_operation]["query"]
    _query["limit"]["minimum"] = 1
    _query["offset"].setdefault("minimum", 0)
SHAPES["operations"]["GET /me/playlists"]["query"]["offset"]["maximum"] = 100_000

# Spotify's changelog: a 429 for a spent quota carries
# {"error": {"status": 429, "message": "Too many requests", "reason":
# "QUOTA_EXCEEDED"}}, a "reason" the published ErrorObject lacks
# (https://developer.spotify.com/documentation/web-api/references/changes/july-2026).
# Only the 429's error object takes the field.
_error_object = SHAPES["schemas"]["ErrorObject"]
SHAPES["schemas"]["TooManyRequestsErrorObject"] = {
    **_error_object,
    "properties": {**_error_object["properties"], "reason": {"type": "string", "enum": ["QUOTA_EXCEEDED"]}},
}
SHAPES["schemas"]["TooManyRequests"]["properties"]["error"] = {"$ref": "TooManyRequestsErrorObject"}

# Spotify documents these for every endpoint, all with the same error body
# (https://developer.spotify.com/documentation/web-api/concepts/api-calls,
# "Response Status Codes"); the per-operation schema lists only 401/403/429.
GLOBAL_ERROR_STATUSES = {"400", "404", "500", "502", "503"}


def response_problems(operation: str, status: int, body: Any, has_body: bool,
                      partial: bool = False) -> List[str]:
    """Every way a response departs from what Spotify documents, including a
    status it does not document for the operation at all.

    ``partial`` is for responses cut down by a ``fields`` filter: what is
    present must still fit, but nothing is required.
    """
    responses = SHAPES["operations"][operation]["responses"]
    key = str(status)
    if key not in responses and key not in GLOBAL_ERROR_STATUSES:
        return [f"Spotify documents no {status} for {operation}"]
    # The global statuses share the {"error": ErrorObject} body of a 401.
    schema = responses.get(key, responses["401"])
    if schema is None:
        return [f"{operation} {status} is documented with no body"] if has_body else []
    return _check(body, {"$ref": schema}, "$", None, _Context(partial))


def request_problems(operation: str, args: dict, body: Any) -> Tuple[List[str], List[str]]:
    """``(invalid, undocumented)`` for an incoming request.

    *invalid* breaks a documented constraint -- a bad type, an out-of-range
    limit, a missing required field -- and Spotify would answer it with 400.
    *undocumented* names something Spotify does not document at all, which it
    would ignore; the fake records it, because it means the client is sending
    something it believes matters that Spotify does not read.
    """
    spec = SHAPES["operations"][operation]
    invalid, undocumented = [], []
    for name in args:
        if name not in spec["query"]:
            undocumented.append(f"?{name}: not a documented parameter")
    for name, rule in spec["query"].items():
        if name in args:
            invalid += _check_param(name, args[name], rule)
        elif rule["required"]:
            invalid.append(f"?{name}: required")
    if spec["body"] is not None:
        # No body at all is checked as an empty one, so its required fields
        # are reported missing rather than skipped.
        ctx = _Context(unknown=undocumented)
        invalid += _check({} if body is None else body, spec["body"], "body", None, ctx)
    return invalid, undocumented


def _check_param(name: str, raw: str, rule: dict) -> List[str]:
    kind = rule.get("type")
    values = raw.split(",") if kind == "array" else [raw]
    item_rule = rule.get("items", rule) if kind == "array" else rule
    for value in values:
        if item_rule.get("type") == "integer":
            if not re.fullmatch(r"-?[0-9]+", value):  # int() would take " 1" and "1_0"
                return [f"Invalid {name}"]
            number = int(value)
            if number < item_rule.get("minimum", number) or number > item_rule.get("maximum", number):
                return [f"Invalid {name}"]
        if "enum" in item_rule and value not in item_rule["enum"]:
            return [f"Invalid {name}"]
    return []


class _Context:
    def __init__(self, partial: bool = False, unknown: Optional[list] = None):
        self.partial = partial
        self.unknown = unknown  # collect undocumented keys here instead of failing


def _check(value: Any, node: dict, path: str, owner: Optional[str], ctx: _Context) -> List[str]:
    if "$ref" in node:
        name = node["$ref"]
        if value is None:
            return [] if node.get("nullable") else [f"{path}: null, but {name} is not nullable"]
        return _check(value, SHAPES["schemas"][name], path, name, ctx)
    if value is None:
        return [] if node.get("nullable") else [f"{path}: null, but the schema allows no null"]
    if "oneOf" in node:
        attempts = [_check(value, option, path, owner, ctx) for option in node["oneOf"]]
        if any(not a for a in attempts):
            return []
        return [f"{path}: matches none of its oneOf options"] + min(attempts, key=len)
    if "enum" in node and value not in node["enum"]:
        return [f"{path}: {value!r} is not one of {node['enum']}"]
    kind = node.get("type")
    if kind == "object" or "properties" in node:
        return _check_object(value, node, path, owner, ctx)
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path}: expected an array, got {type(value).__name__}"]
        out = []
        for i, item in enumerate(value):
            out += _check(item, node.get("items", {}), f"{path}[{i}]", None, ctx)
        return out
    expected = _SCALARS.get(kind)
    if expected and not expected(value):
        return [f"{path}: expected {kind}, got {type(value).__name__}"]
    return []


def _check_object(value, node, path, owner, ctx) -> List[str]:
    if not isinstance(value, dict):
        return [f"{path}: expected an object, got {type(value).__name__}"]
    properties = node.get("properties")
    if properties is None:
        return []  # a free-form object
    removed = DEV_MODE_REMOVED.get(owner, set())
    out = []
    for key in [] if ctx.partial else node.get("required", []):
        if key not in value and key not in removed:
            out.append(f"{path}.{key}: required, missing")
    for key, item in value.items():
        if key not in properties:
            message = f"{path}.{key}: not in Spotify's schema"
            (ctx.unknown if ctx.unknown is not None else out).append(message)
        elif key in removed:
            out.append(f"{path}.{key}: removed for Development Mode apps")
        else:
            out += _check(item, properties[key], f"{path}.{key}", None, ctx)
    return out


_SCALARS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}
