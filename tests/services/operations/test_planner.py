"""A frozen plan keeps each batch's body and the hashes of the states around it."""

import hashlib
import json

import pytest

from shuffify.services.operations.planner import plan, replay

METADATA = {"name": "Target", "public": False}


def uri(index):
    return f"spotify:track:{index:022d}"


def digest(track_uris, metadata=METADATA):
    state = {"track_uris": track_uris, "metadata": metadata}
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_each_batch_records_its_body_and_the_hashes_of_the_states_around_it():
    desired = [uri(index) for index in range(250)]
    _, batches = plan("replace", "target", {"track_uris": [uri(999)], "metadata": METADATA}, uris=desired)
    assert [(item["method"], item["body"], item["before_hash"], item["after_hash"]) for item in batches] == [
        ("PUT", {"uris": desired[:100]}, digest([uri(999)]), digest(desired[:100])),
        ("POST", {"uris": desired[100:200]}, digest(desired[:100]), digest(desired[:200])),
        ("POST", {"uris": desired[200:]}, digest(desired[:200]), digest(desired)),
    ]


@pytest.mark.parametrize("kind,options,expected", [
    ("replace", {"uris": [uri(3), uri(1)]}, [[uri(3), uri(1)]]),
    ("add", {"uris": [uri(3)], "position": 1}, [[uri(1), uri(3), uri(2), uri(1)]]),
    ("add", {"uris": [uri(3)]}, [[uri(1), uri(2), uri(1), uri(3)]]),
    ("remove", {"uris": [uri(1)]}, [[uri(2)]]),
])
def test_replaying_a_plan_gives_the_tracks_after_each_batch(kind, options, expected):
    baseline = {"track_uris": [uri(1), uri(2), uri(1)], "metadata": METADATA}
    _, batches = plan(kind, "target", baseline, **options)
    assert [state["track_uris"] for state in replay(baseline, batches)] == expected


def test_replaying_a_multi_batch_insert_places_each_batch_after_the_one_before():
    baseline = {"track_uris": [uri(1), uri(2)], "metadata": METADATA}
    added = [uri(100 + index) for index in range(150)]
    _, batches = plan("add", "target", baseline, uris=added, position=1)
    assert [state["track_uris"] for state in replay(baseline, batches)] == [
        [uri(1), *added[:100], uri(2)],
        [uri(1), *added, uri(2)],
    ]


def test_replaying_a_details_plan_changes_only_the_written_fields():
    baseline = {"track_uris": [uri(1)], "metadata": METADATA}
    _, batches = plan("details", "target", baseline, details={"name": "Target", "public": True})
    assert replay(baseline, batches) == [{"track_uris": [uri(1)], "metadata": {"name": "Target", "public": True}}]
