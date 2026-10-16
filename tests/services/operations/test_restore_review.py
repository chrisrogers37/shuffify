"""A restoration review binds complete current observations but exposes bounded facts."""

from copy import deepcopy

import pytest

from shuffify.services.operations.restoration import RestoreUnavailable, capture_restore
from tests.services.operations.test_restore_capture import changed, row


def test_review_is_read_only_bounded_and_its_payload_round_trips():
    from shuffify.services.operations.restore_review import RestoreReview, build_context

    world, journal = changed()
    world.playlists["target"]["track_uris"] = [f"spotify:track:{index:022d}" for index in range(250)]
    parent = row(journal.doc)
    captured = capture_restore(parent, 1)
    before, writes = deepcopy(world.playlists), deepcopy(world.writes)
    context = build_context(captured, world.read, world.read)
    assert context["operation_id"] == parent.id and context["available"] is True
    resource = context["resources"][0]
    assert resource["role"] == "restore" and resource["current_count"] == 250
    assert len(resource["current_preview"]) == 20 and resource["desired_count"] == 3
    assert resource["desired_preview"] == list(captured.resources[0].track_uris)
    assert RestoreReview.model_validate(context["reviewed"]).model_dump(mode="json") == context["reviewed"]
    assert not {"journal", "baseline", "plans", "compensation", "lease_owner"} & context.keys()
    assert world.playlists == before and world.writes == writes


def test_every_reserved_resource_must_match_before_a_review_is_accepted():
    from shuffify.services.operations.restore_review import assert_reviewed, build_context

    world, journal = changed()
    world.playlists["guard"] = deepcopy(world.playlists["target"])
    captured = capture_restore(row(journal.doc, targets=("guard", "target")), 1)
    context = build_context(captured, world.read, world.read)
    before = {resource: world.read(resource) for resource in captured.targets}
    before["guard"]["snapshot_id"] = "external"
    with pytest.raises(RestoreUnavailable) as caught:
        assert_reviewed(captured, context["reviewed"], before)
    assert caught.value.code == "restore_review_changed"


def test_metadata_previews_are_bounded_while_complete_values_remain_digest_bound():
    from shuffify.services.operations.restore_review import build_context

    world, journal = changed(metadata=True)
    journal.doc["boundaries"]["current"]["baselines"]["target"]["baseline"]["metadata"]["description"] = "d" * 2048
    journal.doc["plans"][0]["batches"][0]["body"]["description"] = "changed"
    world.playlists["target"]["metadata"].update(name="n" * 300, description="c" * 2048)
    captured = capture_restore(row(journal.doc), 1)
    context = build_context(captured, world.read, world.read)
    fact = context["resources"][0]
    assert len(fact["name"]) == 255 and fact["metadata_truncated"] is True
    assert fact["current_metadata"]["description"] == "c" * 1024
    assert fact["desired_metadata"]["description"] == "d" * 1024
    before = context["reviewed"]["reviewed_resources"][0]["digest"]
    world.playlists["target"]["metadata"]["description"] = "c" * 2047 + "different"
    assert build_context(captured, world.read, world.read)["reviewed"]["reviewed_resources"][0]["digest"] != before
