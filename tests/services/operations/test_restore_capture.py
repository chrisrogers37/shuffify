"""Manual restoration selects only known effects from the unaccepted boundary."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from shuffify.services.operations.restoration import RestoreUnavailable, capture_restore
from tests.services.operations.test_crash_recovery import World, uri


def row(document, *, owner=1, state="needs_attention", targets=("target",)):
    return SimpleNamespace(id=7, user_id=owner, state=state, targets=list(targets), journal=deepcopy(document))


def changed(*, metadata=False):
    world = World([uri(1), uri(1), uri(2)])
    journal = world.journal()
    journal.begin_step("change")
    if metadata:
        journal.freeze_details("target", public=True)
    else:
        journal.freeze_replace("target", [uri(3), uri(3)])
    assert journal.resume()["state"] == "succeeded"
    journal._attention("external_edit")
    return world, journal


def test_track_restore_preserves_duplicate_baseline_and_changes_no_metadata():
    world, journal = changed()
    before = deepcopy(journal.doc)
    captured = capture_restore(row(journal.doc), 1)
    assert captured.operation_id == 7 and captured.reserved_targets == ["target"]
    assert len(captured.resources) == 1
    resource = captured.resources[0]
    assert resource.playlist_id == "target" and resource.track_uris == (uri(1), uri(1), uri(2))
    assert resource.metadata == {}
    assert journal.doc == before
    assert len(world.writes) == 1


def test_metadata_restore_contains_only_the_written_field():
    _world, journal = changed(metadata=True)
    captured = capture_restore(row(journal.doc), 1)
    resource = captured.resources[0]
    assert resource.track_uris is None and resource.metadata == {"public": False}
    assert "name" not in resource.metadata


@pytest.mark.parametrize("state", ["queued", "running", "compensating", "failed", "succeeded", "rolled_back"])
def test_only_owned_terminal_attention_can_be_restored(state):
    _world, journal = changed()
    with pytest.raises(RestoreUnavailable):
        capture_restore(row(journal.doc, state=state), 1)


def test_another_owners_operation_is_not_disclosed():
    _world, journal = changed()
    with pytest.raises(RestoreUnavailable) as caught:
        capture_restore(row(journal.doc, owner=2), 1)
    assert caught.value.code == "operation_not_found" and caught.value.http_status == 404


@pytest.mark.parametrize("state", ["prepared", "rejected", "dispatched"])
def test_no_known_effect_never_authorizes_a_restore_write(state):
    world = World([uri(1)])
    journal = world.journal()
    journal.begin_step("change")
    journal.freeze_add("target", [uri(2)])
    journal.doc["plans"][0]["batches"][0].update(state=state, attempts=int(state != "prepared"))
    journal._attention("unsettled_dispatch" if state == "dispatched" else "external_edit")
    with pytest.raises(RestoreUnavailable):
        capture_restore(row(journal.doc), 1)
    assert world.writes == []


def test_any_unsettled_compensation_dispatch_blocks_restoration():
    _world, journal = changed()
    journal.doc["compensation"] = deepcopy(journal.doc["plans"])
    journal.doc["compensation"][0]["batches"][0]["state"] = "dispatched"
    with pytest.raises(RestoreUnavailable) as caught:
        capture_restore(row(journal.doc), 1)
    assert caught.value.code == "write_completion_unknown"


def test_read_only_dependencies_remain_reserved_but_never_become_restore_targets():
    world = World([uri(1)])
    world.playlists["source"] = deepcopy(world.playlists["target"])
    journal = world.journal()
    journal.begin_step("change")
    journal.observe_resource("source", expected_baseline=world.read("source"))
    journal.freeze_replace("target", [uri(2)])
    journal.resume()
    journal._attention("external_edit")
    captured = capture_restore(row(journal.doc, targets=("source", "target")), 1)
    assert captured.reserved_targets == ["source", "target"]
    assert [resource.playlist_id for resource in captured.resources] == ["target"]


def test_accepted_prefix_and_its_saved_baseline_are_never_restored():
    world = World([uri(1)])
    journal = world.journal()
    journal.begin_step("accepted")
    journal.freeze_add("target", [uri(2)])
    journal.resume()
    proof = journal.verify_step("accepted")
    world.checkpoint(journal.accept_step_document(journal.doc, "accepted", proof=proof, result={}))
    journal = world.journal()
    journal.begin_step("current")
    journal.freeze_replace("target", [uri(3)])
    journal.resume()
    journal._attention("external_edit")
    captured = capture_restore(row(journal.doc), 1)
    assert captured.resources[0].track_uris == (uri(1), uri(2))


def test_a_creation_is_never_an_automatic_restore_candidate():
    world = World([uri(1)])
    journal = world.journal()
    journal.begin_step("asset")
    journal.freeze_create("Example")
    journal.resume()
    journal._attention("created_playlist_requires_review")
    with pytest.raises(RestoreUnavailable) as caught:
        capture_restore(row(journal.doc), 1)
    assert caught.value.code == "created_playlist_requires_review"


def test_a_resolved_parent_returns_the_actual_restoration_identity():
    _world, journal = changed()
    journal.doc["runtime"] = {"restoration": {"operation_id": 12}}
    with pytest.raises(RestoreUnavailable) as caught:
        capture_restore(row(journal.doc), 1)
    assert caught.value.code == "operation_already_restored"
    assert caught.value.operation_id == 12


def test_capture_survives_actual_json_round_trip_without_losing_order_or_types():
    import json

    from shuffify.services.operations.restoration import RestoreCapture

    _world, journal = changed()
    captured = capture_restore(row(journal.doc), 1)
    parsed = RestoreCapture.model_validate(json.loads(json.dumps(captured.model_dump(mode="json"))))
    assert parsed == captured
    assert parsed.resources[0].track_uris == (uri(1), uri(1), uri(2))
    assert type(parsed.targets) is tuple and type(parsed.resources) is tuple


@pytest.mark.parametrize(
    "alter", ["duplicate_target", "duplicate_resource", "foreign_resource", "empty_resources", "bad_field"]
)
def test_capture_schema_rejects_noncanonical_or_unauthorized_shape(alter):
    from pydantic import ValidationError

    from shuffify.services.operations.restoration import RestoreCapture

    _world, journal = changed()
    value = capture_restore(row(journal.doc), 1).model_dump(mode="json")
    if alter == "duplicate_target":
        value["targets"].append("target")
    elif alter == "duplicate_resource":
        value["resources"].append(deepcopy(value["resources"][0]))
    elif alter == "foreign_resource":
        value["resources"][0]["playlist_id"] = "foreign"
    elif alter == "empty_resources":
        value["resources"] = []
    else:
        value["resources"][0]["metadata"] = {"access_token": "private"}
    with pytest.raises(ValidationError):
        RestoreCapture.model_validate(value)


@pytest.mark.parametrize(
    "damage",
    ["foreign_path", "wrong_method", "unknown_kind", "negative_attempts", "boolean_start", "boolean_version"],
)
def test_invalid_private_journal_cannot_expand_write_authority(damage):
    _world, journal = changed()
    document = deepcopy(journal.doc)
    if damage == "foreign_path":
        document["plans"][0]["batches"][0]["path"] = "/playlists/foreign/items"
    elif damage == "wrong_method":
        document["plans"][0]["batches"][0]["method"] = "GET"
    elif damage == "unknown_kind":
        document["plans"][0]["kind"] = "future_write"
    elif damage == "negative_attempts":
        document["plans"][0]["batches"][0]["attempts"] = -1
    elif damage == "boolean_start":
        document["boundaries"]["current"]["start"] = False
    else:
        document["version"] = True
    with pytest.raises(RestoreUnavailable):
        capture_restore(row(document), 1)


def test_large_scope_is_explicitly_unavailable_before_any_provider_read():
    _world, journal = changed()
    with pytest.raises(RestoreUnavailable) as caught:
        capture_restore(row(journal.doc, targets=("target", *(f"guard{index}" for index in range(100)))), 1)
    assert caught.value.code == "restore_scope_too_large"


def test_unknown_metadata_effect_never_silently_expands_or_resolves_restoration():
    _world, journal = changed(metadata=True)
    journal.doc["plans"][0]["batches"][0]["body"]["collaborative"] = True
    with pytest.raises(RestoreUnavailable):
        capture_restore(row(journal.doc), 1)


def test_unreserved_read_only_ledger_resource_cannot_be_omitted_from_the_guard():
    _world, journal = changed()
    journal.doc["resources"]["foreign"] = deepcopy(journal.doc["resources"]["target"])
    with pytest.raises(RestoreUnavailable):
        capture_restore(row(journal.doc), 1)


@pytest.mark.parametrize("metadata", [False, True])
def test_known_rejected_compensation_keeps_original_effects_available_for_review(metadata):
    from shuffify.spotify.exceptions import SpotifyWriteRejectedError

    _world, journal = changed(metadata=metadata)

    def rejected(*_args):
        raise SpotifyWriteRejectedError("Restoration attempt refused")

    journal.dispatch = rejected
    journal.doc["status"] = "failed"
    journal._save()
    with pytest.raises(SpotifyWriteRejectedError):
        journal.compensate()
    journal._attention("compensation_failed")
    assert journal.doc["compensation"][0]["kind"] == "compensate"
    assert journal.doc["compensation"][0]["batches"][0]["state"] == "rejected"
    captured = capture_restore(row(journal.doc), 1)
    assert captured.resources[0].track_uris == (None if metadata else (uri(1), uri(1), uri(2)))
    assert captured.resources[0].metadata == ({"public": False} if metadata else {})


def test_compensation_kind_is_not_permitted_as_a_forward_effect():
    _world, journal = changed()
    journal.doc["plans"][0]["kind"] = "compensate"
    with pytest.raises(RestoreUnavailable):
        capture_restore(row(journal.doc), 1)


def test_a_restoration_child_never_becomes_a_recursive_restoration_parent():
    _world, journal = changed()
    parent = row(journal.doc)
    parent.kind = "operation_restore"
    with pytest.raises(RestoreUnavailable) as caught:
        capture_restore(parent, 1)
    assert caught.value.code == "nested_restoration_unavailable"
