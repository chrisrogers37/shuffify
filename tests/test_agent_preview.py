"""Exercise the two agent tools through their local dispatch boundary."""

import socket
from copy import deepcopy

import jsonschema
import pytest
from sqlalchemy.engine import Engine

from harness.agent_preview.tools import ToolError, call, contracts, load_fixture

PLAN = {"playlist_id": "NightBus", "action": "newest_first"}


def test_contracts_and_fixture_conversation_are_read_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Fixture tools must run without network or database access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)
    workspace = load_fixture()
    original = deepcopy(workspace)

    tools = {tool["name"]: tool for tool in contracts()}
    assert set(tools) == {"plan_playlist_operation", "get_operation_status"}

    def invoke(name, args):
        contract = tools[name]
        assert contract["annotations"]["readOnlyHint"] is True
        jsonschema.validate(args, contract["inputSchema"])
        output = call(workspace, name, args)
        jsonschema.validate(output, contract["outputSchema"])
        return output

    preview = invoke("plan_playlist_operation", PLAN)
    assert preview["state"] == "preview" and "operation_id" not in preview
    assert (preview["snapshot_id"], preview["track_count"], preview["changed_positions"]) == ("night-bus-v8", 5, 3)
    assert not preview["truncated"]
    assert [slot["after"]["name"] for slot in preview["positions"]] == [
        "Northbound", "Station ID", "Afterglow", "Afterglow", "Paper Moon",
    ]
    assert preview["positions"][1]["locked"] and preview["positions"][1]["before"] == preview["positions"][1]["after"]
    assert invoke("plan_playlist_operation", PLAN) == preview

    succeeded = invoke("get_operation_status", {"operation_id": 41})["operation"]
    attention = invoke("get_operation_status", {"operation_id": 42})["operation"]
    assert succeeded["state"] == "succeeded" and not succeeded["recovery"]["available"]
    assert attention["completed_at"] and attention["state"] == "needs_attention"
    assert attention["recovery"] == {"available": True, "action": "restore", "reason": "external_edit"}
    assert workspace == original


def test_status_rederives_restore_eligibility_from_private_journal():
    workspace = load_fixture()
    seed = next(row for row in workspace["operations"] if row["id"] == 42)
    seed["journal"]["plans"][0]["batches"][0]["state"] = "dispatched"
    # A stale recovery hint cannot turn an uncertain write into a reviewable one.
    assert seed["journal"]["recovery"]["available"]
    operation = call(workspace, "get_operation_status", {"operation_id": 42})["operation"]
    assert operation["recovery"] == {"available": False, "action": None, "reason": "write_completion_unknown"}
    assert {"plans", "journal", "payload", "lease_owner", "user_id"}.isdisjoint(operation)


@pytest.mark.parametrize(("name", "owned", "missing", "code"), [
    ("plan_playlist_operation", PLAN, {**PLAN, "playlist_id": "Missing"}, "playlist_not_found"),
    ("get_operation_status", {"operation_id": 41}, {"operation_id": 999}, "operation_not_found"),
])
def test_other_owner_and_missing_look_identical(name, owned, missing, code):
    workspace = load_fixture()
    workspace["owner_id"] += 1
    errors = []
    for arguments in (owned, missing):
        with pytest.raises(ToolError) as error:
            call(workspace, name, arguments)
        errors.append((error.value.code, str(error.value)))
    assert errors[0] == errors[1] and errors[0][0] == code


@pytest.mark.parametrize(("name", "args", "code"), [
    ("plan_playlist_operation", {**PLAN, "workspace_id": "other"}, "invalid_arguments"),
    ("plan_playlist_operation", {**PLAN, "execute": True}, "invalid_arguments"),
    ("plan_playlist_operation", {**PLAN, "action": "delete"}, "invalid_arguments"),
    ("get_operation_status", {"operation_id": True}, "invalid_arguments"),
    ("get_operation_status", {"operation_id": "41"}, "invalid_arguments"),
    ("get_operation_status", {"operation_id": 0}, "invalid_arguments"),
    ("get_operation_status", {"operation_id": 41, "restore": True}, "invalid_arguments"),
    ("restore", {"operation_id": 41}, "unknown_tool"),
])
def test_only_declared_reads_are_available(name, args, code):
    with pytest.raises(ToolError) as error:
        call(load_fixture(), name, args)
    assert error.value.code == code


def test_bounded_preview_counts_changes_across_the_whole_playlist():
    workspace = load_fixture()
    playlist = workspace["playlists"]["NightBus"]
    first, _, _, last, _ = playlist["tracks"]
    playlist["tracks"] = [first] * 20 + [last]
    playlist["locked_positions"] = {}
    preview = call(workspace, "plan_playlist_operation", PLAN)
    assert (len(preview["positions"]), preview["track_count"], preview["changed_positions"]) == (20, 21, 2)
    assert preview["truncated"]

    playlist["tracks"] = []
    empty = call(workspace, "plan_playlist_operation", PLAN)
    assert empty["positions"] == [] and not empty["truncated"]
    assert empty["track_count"] == empty["changed_positions"] == 0
