"""Read-only metadata dependencies authorize creation without an item pipeline."""

from copy import deepcopy

import pytest

from shuffify.services.operations.journal import DurableJournal, JournalError, NeedsAttention
from tests.services.operations.test_crash_recovery import World, uri


def journal(world):
    def no_items(resource):
        pytest.fail("Metadata authorization cannot depend on readable playlist items")

    def metadata(resource):
        state = world.read(resource)
        return {key: state[key] for key in ("metadata", "snapshot_id")}

    return DurableJournal(
        lambda: deepcopy(world.saved), world.checkpoint, no_items, world.dispatch, read_metadata=metadata
    )


def baseline(world):
    return {
        "scope": "metadata",
        "metadata": deepcopy(world.playlists["target"]["metadata"]),
        "snapshot_id": world.playlists["target"]["snapshot_id"],
    }


def test_metadata_read_only_dependency_has_no_mutation_and_recovers_without_item_reads():
    world = World([uri(1)])
    frozen = baseline(world)
    operation = journal(world)
    operation.observe_resource("target", expected_baseline=frozen, scope="metadata")
    assert world.saved["resources"]["target"]["scope"] == "metadata"
    assert world.saved["plans"] == []
    world.playlists["target"]["track_uris"] = ["unsupported:item"]
    world.playlists["target"]["snapshot_id"] = "tracks-changed"
    recovered = journal(world)
    recovered.observe_resource("target", expected_baseline=frozen, scope="metadata")
    assert recovered.resume()["state"] == "succeeded"
    assert world.writes == []
    assert recovered.compensate()["state"] == "rolled_back"
    assert world.writes == []


@pytest.mark.parametrize("field", ["name", "public"])
def test_metadata_dependency_conflict_is_attention_without_write_or_restore(field):
    world = World([uri(1)])
    frozen = baseline(world)
    journal(world).observe_resource("target", expected_baseline=frozen, scope="metadata")
    world.playlists["target"]["metadata"][field] = "External" if field == "name" else True
    with pytest.raises(NeedsAttention):
        journal(world).observe_resource("target", expected_baseline=frozen, scope="metadata")
    assert journal(world).verify_terminal()["state"] == "needs_attention"
    assert journal(world).restore_plan()["resources"] == []
    assert world.writes == []


@pytest.mark.parametrize("scope", [None, "", "items", True])
def test_invalid_scope_refuses_before_authorization(scope):
    world = World()
    with pytest.raises(JournalError, match="scope"):
        journal(world).observe_resource("target", expected_baseline=baseline(world), scope=scope)
    assert world.trace == []


def test_explicit_metadata_scope_requires_metadata_shaped_expected_baseline():
    world = World()
    with pytest.raises(JournalError, match="scope"):
        journal(world).observe_resource("target", expected_baseline=world.playlists["target"], scope="metadata")
    assert world.trace == []
