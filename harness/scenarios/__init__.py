"""Scenarios: Spotify behaviour as Web API calls and the answers Spotify
should give, replayable against any target that speaks the Web API.

The fake Spotify models behaviour from Spotify's documentation;
harness/fake_spotify/behaviours.json labels each behaviour by how it is
known, "docs-only" or "checked-live". A scenario is the part of that model a
target can replay. tests/test_scenarios.py replays every one against the
fake (FakeTarget). The same files can be replayed against real Spotify with
a dedicated test account and allowlisted playlists; a behaviour whose
scenario passes there is checked live.

A scenario is one JSON file here, named for its id:

    {
      "id": "remove-every-occurrence",
      "summary": "Removing a URI removes every occurrence of it.",
      "behaviours": ["writes.remove-every-occurrence"],
      "playlists": {"canary": "own"},
      "tracks": 2,
      "steps": [
        {"call": "PUT /playlists/{canary}/items",
         "body": {"uris": ["{track1}", "{track2}", "{track1}"]}, "status": 200},
        {"call": "DELETE /playlists/{canary}/items",
         "body": {"items": [{"uri": "{track1}"}]}, "status": 200},
        {"call": "GET /playlists/{canary}/items", "status": 200, "uris": ["{track2}"]}
      ]
    }

Placeholders stand for everything account-specific; a scenario names no
real ID:
- ``{<role>}`` is a playlist the target binds. An ``own`` role is the user's
  own playlist, which the scenario may write; a ``foreign`` role is another
  user's public playlist, which it may only read.
- ``{trackN}`` is the Nth of the ``tracks`` track URIs the target supplies,
  and ``{trackN.id}`` its ID.
- ``{"$repeat": value, "times": N}`` inside a list stands for N copies.

A step is a ``call`` (``"<METHOD> <path under /v1>"``) with an optional
``query`` and JSON ``body``, and what the answer must be: ``status``
(required); ``uris``, the track URIs of the answer's items, in order;
``json``, {dotted.path: value}, each present and equal; ``absent``,
[dotted.path], each missing; ``count``, {dotted.path: n}, a list of that
length; ``no_body``, answered with no body. ``unserved: true`` marks a GET
the fake does not serve on purpose (an endpoint Spotify removed): the fake
records it, and the scenario says what Spotify answers instead.

The rule for a live replay is enforced here, not trusted to each scenario:
``validate`` refuses, before any call, a write (any method but GET) to
anything but an ``own`` playlist, and any call that would create a playlist.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.fake_spotify import shapes

HERE = Path(__file__).parent
KINDS = {"own", "foreign"}
METHODS = {"GET", "PUT", "POST", "DELETE"}
SCENARIO_KEYS = {"id", "summary", "behaviours", "playlists", "tracks", "steps"}
STEP_KEYS = {"call", "query", "body", "status", "uris", "json", "absent", "count", "no_body", "unserved"}
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.]+)\}")
_TRACK = re.compile(r"track([1-9][0-9]*)(\.id)?")
_MISSING = object()


class ScenarioError(ValueError):
    """A scenario that is malformed, or would break the live-replay rule."""


class ScenarioFailure(AssertionError):
    """A target answered a step otherwise than the scenario says Spotify does."""


@dataclass
class Answer:
    status: int
    body: Any  # the parsed JSON, or None
    has_body: bool


def load_all() -> List[dict]:
    """Every scenario here, validated, in id order."""
    found = []
    for path in sorted(HERE.glob("*.json")):
        scenario = json.loads(path.read_text())
        validate(scenario)
        if scenario["id"] != path.stem:
            raise ScenarioError(f"{path.name}: id {scenario['id']!r} is not the file's name")
        found.append(scenario)
    return found


def validate(scenario: dict) -> None:
    """Refuse a malformed scenario, or one a live replay must not run."""
    required = {"id", "summary", "steps"}
    if not isinstance(scenario, dict) or set(scenario) - SCENARIO_KEYS or not required <= set(scenario):
        raise ScenarioError(f"a scenario has an id, a summary and steps, and only {sorted(SCENARIO_KEYS)}")
    name = scenario["id"]
    playlists = scenario.get("playlists", {})
    if not isinstance(playlists, dict) or not set(playlists.values()) <= KINDS:
        raise ScenarioError(f"{name}: each playlist role is one of {sorted(KINDS)}")
    tracks = scenario.get("tracks", 0)
    if isinstance(tracks, bool) or not isinstance(tracks, int) or tracks < 0:
        raise ScenarioError(f"{name}: tracks is how many track URIs it needs")
    behaviours = scenario.get("behaviours")
    if not behaviours or not all(isinstance(b, str) for b in behaviours):
        raise ScenarioError(f"{name}: behaviours names what it checks")
    if not scenario["steps"]:
        raise ScenarioError(f"{name}: no steps")
    for number, step in enumerate(scenario["steps"], 1):
        where = f"{name} step {number}"
        if not isinstance(step, dict) or set(step) - STEP_KEYS or "call" not in step or "status" not in step:
            raise ScenarioError(f"{where}: a step has a call and a status, and only {sorted(STEP_KEYS)}")
        method, _, path = step["call"].partition(" ")
        if method not in METHODS or not path.startswith("/"):
            raise ScenarioError(f"{where}: a call is '<METHOD> /path', the method one of {sorted(METHODS)}")
        for token in _PLACEHOLDER.findall(json.dumps(step)):
            if not (playlists.get(token) or _track_number(token, tracks)):
                raise ScenarioError(f"{where}: {{{token}}} is neither a playlist role nor one of its {tracks} tracks")
        roles = [t for t in _PLACEHOLDER.findall(path) if t in playlists]
        operation = f"{method} {_operation_path(path, playlists)}"
        # The live-replay rule first, so a refusal says that is why.
        if method != "GET":
            if operation == "POST /me/playlists":
                raise ScenarioError(f"{where}: a scenario never creates a playlist")
            if not roles or any(playlists[r] != "own" for r in roles):
                raise ScenarioError(f"{where}: a write goes only to an own playlist")
        if step.get("unserved"):
            if method != "GET" or operation in shapes.SHAPES["operations"]:
                raise ScenarioError(f"{where}: unserved is for a GET the fake does not serve")
        elif operation not in shapes.SHAPES["operations"]:
            raise ScenarioError(f"{where}: {operation} is not an operation in shapes.json")


def _track_number(token: str, tracks: int) -> bool:
    match = _TRACK.fullmatch(token)
    return bool(match) and int(match.group(1)) <= tracks


def _operation_path(path: str, playlists: dict) -> str:
    """The path as shapes.json names it: /playlists/{playlist_id}, /tracks/{id}."""
    def name(match):
        token = match.group(1)
        return "{playlist_id}" if token in playlists else "{id}"
    return _PLACEHOLDER.sub(name, path.split("?", 1)[0])


def fill(value, values: Dict[str, str]):
    """``value`` with every placeholder bound and every $repeat expanded."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole:
            return values[whole.group(1)]
        return _PLACEHOLDER.sub(lambda m: values[m.group(1)], value)
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict) and set(item) == {"$repeat", "times"}:
                out += [fill(item["$repeat"], values)] * item["times"]
            else:
                out.append(fill(item, values))
        return out
    if isinstance(value, dict):
        return {key: fill(item, values) for key, item in value.items()}
    return value


def _at(body, path: str):
    for key in path.split("."):
        if isinstance(body, list) and key.isdigit() and int(key) < len(body):
            body = body[int(key)]
        elif isinstance(body, dict) and key in body:
            body = body[key]
        else:
            return _MISSING
    return body


def _uris(body) -> Optional[List[str]]:
    """The track URIs of an items page, in either response shape."""
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return None
    return [(entry.get("item") or entry.get("track") or {}).get("uri") for entry in items]


def problems(step: dict, answer: Answer, values: Dict[str, str]) -> List[str]:
    """Every way ``answer`` departs from what ``step`` expects."""
    found = []
    if answer.status != step["status"]:
        found.append(f"answered {answer.status}, not {step['status']}")
        return found  # the rest describe a different answer
    if "uris" in step and _uris(answer.body) != fill(step["uris"], values):
        found.append(f"items {_uris(answer.body)}, not {fill(step['uris'], values)}")
    for path, expected in step.get("json", {}).items():
        actual = _at(answer.body, path)
        if actual is _MISSING:
            found.append(f"{path} missing")
        elif actual != fill(expected, values):
            found.append(f"{path} is {actual!r}, not {fill(expected, values)!r}")
    for path in step.get("absent", []):
        if _at(answer.body, path) is not _MISSING:
            found.append(f"{path} present")
    for path, length in step.get("count", {}).items():
        actual = _at(answer.body, path)
        if not isinstance(actual, list) or len(actual) != length:
            found.append(f"{path} holds {len(actual) if isinstance(actual, list) else 'no list'}, not {length}")
    if step.get("no_body") and answer.has_body:
        found.append("answered with a body")
    return found


def run(scenario: dict, target) -> None:
    """Replay ``scenario`` against ``target``; ScenarioFailure at the first
    step answered otherwise.

    A target binds roles and tracks (``bind(playlists, tracks)`` returns the
    placeholder values) and makes calls (``call(method, path, query, body,
    unserved)`` returns an Answer).
    """
    validate(scenario)
    values = target.bind(scenario.get("playlists", {}), scenario.get("tracks", 0))
    for number, step in enumerate(scenario["steps"], 1):
        method, _, path = step["call"].partition(" ")
        answer = target.call(method, fill(path, values), fill(step.get("query", {}), values),
                             fill(step.get("body"), values), bool(step.get("unserved")))
        found = problems(step, answer, values)
        if found:
            raise ScenarioFailure(f"{scenario['id']} step {number} ({step['call']}): {'; '.join(found)}")
