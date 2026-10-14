"""The scenarios replay against the fake, and the behaviour labels stay honest.

harness/scenarios/ holds Spotify behaviour as Web API calls with the answers
Spotify should give; harness/fake_spotify/behaviours.json labels each
behaviour the fake models docs-only or checked-live. Every scenario must pass
against the fake, so a live replay (the canary) that fails one
has found the fake, or the documentation, wrong about Spotify. The labels
and the scenarios must agree.
"""

import copy
import json
from pathlib import Path

import pytest

from harness import scenarios
from harness.scenarios.fake import FakeTarget

BEHAVIOURS = json.loads(
    (Path(scenarios.__file__).parents[1] / "fake_spotify" / "behaviours.json").read_text()
)["behaviours"]
SCENARIOS = scenarios.load_all()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_every_scenario_passes_against_the_fake(scenario):
    target = FakeTarget()
    scenarios.run(scenario, target)
    assert target.violations() == [], "the scenario sent something Spotify does not document"


def test_a_scenario_fails_when_the_fake_departs_from_it():
    """Not vacuous: an answer other than the scenario's is a failure, naming the step."""
    scenario = copy.deepcopy(next(s for s in SCENARIOS if s["id"] == "remove-every-occurrence"))
    scenario["steps"][-1]["uris"] = ["{track1}"]
    with pytest.raises(scenarios.ScenarioFailure, match=r"remove-every-occurrence step 3 .*items"):
        scenarios.run(scenario, FakeTarget())


def test_behaviours_are_labelled_and_say_how_they_are_known():
    ids = [b["id"] for b in BEHAVIOURS]
    assert len(ids) == len(set(ids)), "a behaviour is listed twice"
    for b in BEHAVIOURS:
        assert b["label"] in {"docs-only", "checked-live"}, b["id"]
        assert b["summary"] and b["source"], b["id"]
        assert ("checked" in b) == (b["label"] == "checked-live"), f"{b['id']}: only checked-live says how"


def test_every_behaviour_names_its_scenario_or_why_there_is_none():
    by_id = {s["id"]: s for s in SCENARIOS}
    for b in BEHAVIOURS:
        if b["scenario"] is None:
            assert b.get("why"), f"{b['id']}: no scenario and no reason"
        else:
            assert b["scenario"] in by_id, f"{b['id']}: no scenario {b['scenario']!r}"
            assert b["id"] in by_id[b["scenario"]]["behaviours"], f"{b['scenario']} does not say it checks {b['id']}"


def test_every_scenario_checks_listed_behaviours():
    listed = {b["id"] for b in BEHAVIOURS}
    for s in SCENARIOS:
        assert set(s["behaviours"]) <= listed, f"{s['id']} checks a behaviour behaviours.json does not list"


def _scenario(step, tracks=1):
    return {"id": "x", "summary": "x", "behaviours": ["x"], "playlists": {"mine": "own", "theirs": "foreign"},
            "tracks": tracks, "steps": [step]}


@pytest.mark.parametrize("step, problem", [
    ({"call": "PUT /playlists/{theirs}/items", "body": {"uris": []}, "status": 403}, "only to an own playlist"),
    ({"call": "DELETE /playlists/{theirs}/items", "body": {"items": []}, "status": 403}, "only to an own playlist"),
    ({"call": "PUT /playlists/0testplaylist000000004/items", "body": {"uris": []}, "status": 200},
     "only to an own playlist"),
    ({"call": "POST /me/playlists", "body": {"name": "x"}, "status": 201}, "never creates a playlist"),
    ({"call": "GET /browse/new-releases", "status": 200}, "not an operation in shapes.json"),
    ({"call": "GET /me", "status": 200, "unserved": True}, "unserved is for a GET the fake does not serve"),
    ({"call": "GET /tracks/{track2.id}", "status": 200}, "neither a playlist role nor one of its 1 tracks"),
    ({"call": "GET /me", "status": 200, "expect": {}}, "a step has a call and a status"),
    ({"call": "get /me", "status": 200}, "a call is '<METHOD> /path'"),
])
def test_validate_refuses_what_a_live_replay_must_not_run(step, problem):
    with pytest.raises(scenarios.ScenarioError, match=problem):
        scenarios.validate(_scenario(step))


def test_placeholders_fill_paths_bodies_and_repeats():
    values = {"mine": "PL", "track1": "spotify:track:A", "track1.id": "A"}
    assert scenarios.fill("/playlists/{mine}/items", values) == "/playlists/PL/items"
    assert scenarios.fill({"uris": [{"$repeat": "{track1}", "times": 3}], "ids": "{track1.id}"}, values) == {
        "uris": ["spotify:track:A"] * 3, "ids": "A"}
