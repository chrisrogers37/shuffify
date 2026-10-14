#!/usr/bin/env python3
"""Extract the shapes of the calls the fake Spotify serves from Spotify's docs.

Spotify publishes an OpenAPI schema for its Web API. This keeps only what the
fake needs -- for each operation shuffify calls, the structure of its query
parameters, its request body and each response (names, types, nullability,
required fields, enums and numeric bounds), the OAuth scopes it requires,
and whether it is deprecated. No descriptions and no example values are
copied. The result is committed as harness/fake_spotify/shapes.json;
the fake checks every request it receives and every response it sends
against it.

    python scripts/extract-spotify-shapes.py                # download, write
    python scripts/extract-spotify-shapes.py --schema FILE  # from a saved copy
    python scripts/extract-spotify-shapes.py --check        # exit 1 if stale

Downloading needs developer.spotify.com reachable. That is Spotify's
documentation site, not its API: nothing here sends a token anywhere.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import yaml

SCHEMA_URL = "https://developer.spotify.com/reference/web-api/open-api-schema.yaml"
OUT = Path(__file__).resolve().parent.parent / "harness" / "fake_spotify" / "shapes.json"

# Every Web API operation shuffify's client makes (shuffify/spotify/api.py),
# and so every one the fake serves. Spotify does not serve
# POST /users/{user_id}/playlists or GET /tracks to Development Mode apps, so
# the client calls POST /me/playlists and GET /tracks/{id} instead.
OPERATIONS = [
    ("GET", "/me"),
    ("GET", "/me/playlists"),
    ("GET", "/playlists/{playlist_id}"),
    ("PUT", "/playlists/{playlist_id}"),
    ("GET", "/playlists/{playlist_id}/items"),
    ("POST", "/playlists/{playlist_id}/items"),
    ("PUT", "/playlists/{playlist_id}/items"),
    ("DELETE", "/playlists/{playlist_id}/items"),
    ("POST", "/me/playlists"),
    ("GET", "/tracks/{id}"),
    ("GET", "/audio-features"),
    ("GET", "/search"),
]

# Kept per node; everything else (descriptions, examples, x-* keys) is not.
_SCALAR_KEYS = ("type", "nullable", "enum", "minimum", "maximum", "default")


def _ref_name(ref):
    return ref.rsplit("/", 1)[-1]


class Extractor:
    def __init__(self, spec):
        self.spec = spec
        self.components = spec["components"]
        self.schemas = {}  # name -> shape, filled as references are reached

    def node(self, schema):
        """The shape of one schema node, with $refs kept as references."""
        if "$ref" in schema:
            name = _ref_name(schema["$ref"])
            self._define(name)
            return {"$ref": name}
        if "allOf" in schema:
            return self._all_of(schema)
        if "oneOf" in schema:
            return {"oneOf": [self.node(s) for s in schema["oneOf"]]}
        out = {k: schema[k] for k in _SCALAR_KEYS if k in schema}
        if schema.get("type") == "array" and "items" in schema:
            out["items"] = self.node(schema["items"])
        if "properties" in schema:
            out["type"] = "object"
            out["properties"] = {k: self.node(v) for k, v in schema["properties"].items()}
        if "required" in schema:
            out["required"] = sorted(schema["required"])
        return out

    def _all_of(self, schema):
        parts = [self.node(s) for s in schema["allOf"]]
        if len(parts) == 1 and "properties" not in schema:
            # "allOf: [Ref]" is how the schema attaches a description to a
            # reference; structurally it is just the reference.
            merged = dict(parts[0])
            if schema.get("nullable"):
                merged["nullable"] = True
            return merged
        properties, required = {}, set()
        for part in parts:
            part = self._resolve(part)
            properties.update(part.get("properties", {}))
            required.update(part.get("required", []))
        out = {"type": "object", "properties": properties}
        if required:
            out["required"] = sorted(required)
        return out

    def _resolve(self, node):
        while "$ref" in node:
            node = self.schemas[node["$ref"]]
        return node

    def _define(self, name):
        if name in self.schemas:
            return
        self.schemas[name] = {}  # placeholder: breaks reference cycles
        self.schemas[name] = self.node(self.components["schemas"][name])

    def response(self, ref_or_inline, fallback_name):
        """The schema name a response's JSON body has, or None for no body."""
        resp = ref_or_inline
        if "$ref" in resp:
            fallback_name = _ref_name(resp["$ref"])
            resp = self.components["responses"][fallback_name]
        content = resp.get("content", {}).get("application/json")
        if not content:
            return None
        schema = content["schema"]
        if "$ref" in schema:
            name = _ref_name(schema["$ref"])
            self._define(name)
            return name
        self.schemas[fallback_name] = self.node(schema)
        return fallback_name

    def parameters(self, op):
        """Query parameters by name (path parameters are the route's own)."""
        out = {}
        for param in op.get("parameters", []):
            if "$ref" in param:
                param = self.components["parameters"][_ref_name(param["$ref"])]
            if param["in"] == "query":
                out[param["name"]] = {**self.node(param.get("schema", {})), "required": bool(param.get("required"))}
        return out

    def request_body(self, op):
        body = op.get("requestBody")
        content = (body or {}).get("content", {}).get("application/json")
        return self.node(content["schema"]) if content else None

    @staticmethod
    def scopes(op):
        """The scopes an operation requires: every one in its one requirement."""
        (requirement,) = op.get("security", [{"oauth_2_0": []}])
        return sorted(requirement["oauth_2_0"])

    def reachable(self, operations):
        """The schemas some operation reaches. allOf parts, inlined, drop out."""
        names = [name for op in operations.values() for name in op["responses"].values() if name]
        seen, todo = set(), [operations]
        while names or todo:
            if names:
                name = names.pop()
                if name not in seen:
                    seen.add(name)
                    todo.append(self.schemas[name])
                continue
            value = todo.pop()
            if isinstance(value, dict):
                if "$ref" in value:
                    names.append(value["$ref"])
                todo.extend(value.values())
            elif isinstance(value, list):
                todo.extend(value)
        return {name: self.schemas[name] for name in sorted(seen)}

    def extract(self):
        operations = {}
        for method, path in OPERATIONS:
            op = self.spec["paths"][path][method.lower()]
            responses = {
                status: self.response(resp, f"{method}{path}:{status}")
                for status, resp in sorted(op["responses"].items())
            }
            operations[f"{method} {path}"] = {
                "deprecated": bool(op.get("deprecated", False)),
                "scopes": self.scopes(op),
                "query": self.parameters(op),
                "body": self.request_body(op),
                "responses": responses,
            }
        return {
            "_about": (
                "Query parameters, request bodies, response shapes and scopes for the "
                "Web API operations shuffify calls, extracted "
                "from Spotify's published OpenAPI schema by scripts/extract-spotify-shapes.py. "
                "Structure only: no descriptions or example values. Regenerate with that "
                "script; do not edit by hand."
            ),
            "source": SCHEMA_URL,
            "source_version": self.spec.get("info", {}).get("version"),
            "operations": operations,
            "schemas": self.reachable(operations),
        }


def render(spec):
    return json.dumps(Extractor(spec).extract(), indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--schema", type=Path, help="a saved copy of the OpenAPI YAML")
    parser.add_argument("--check", action="store_true", help="fail if shapes.json is stale")
    args = parser.parse_args()

    if args.schema:
        raw = args.schema.read_text()
    else:
        with urllib.request.urlopen(SCHEMA_URL, timeout=60) as resp:  # noqa: S310 - fixed https URL
            raw = resp.read().decode()
    text = render(yaml.safe_load(raw))

    if args.check:
        if OUT.read_text() != text:
            print(f"{OUT} is out of date with {SCHEMA_URL}; rerun without --check", file=sys.stderr)
            return 1
        print(f"{OUT.name} matches the current schema")
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
