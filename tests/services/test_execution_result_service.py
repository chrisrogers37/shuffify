"""The durable result boundary accepts only small observed execution facts."""

from copy import deepcopy

from shuffify.services.execution_result_service import (
    build_execution_detail,
    build_live_execution_detail,
    serialize_execution_detail,
)


def test_legacy_and_invalid_details_are_unavailable():
    unavailable = {"detail_status": "unavailable", "version": None, "steps": None}
    assert serialize_execution_detail(None) == unavailable
    assert serialize_execution_detail({"version": 2}) == unavailable
    assert serialize_execution_detail({"version": 1, "operation": "raid", "steps": []}) == unavailable


def test_malformed_stored_discriminators_return_unavailable():
    valid = build_execution_detail(
        operation="raid", execution_id=7, outcome="complete",
        steps=[{"step": "raid", "status": "complete", "resources": [{
            "resource": "collection", "status": "complete", "kind": "collection",
        }], "bookkeeping": [{"kind": "pending_stage", "status": "complete"}]}],
    )
    invalid = []
    for key in ("operation", "outcome", "run_state", "execution_record"):
        value = deepcopy(valid)
        value[key] = []
        invalid.append(value)
    for key in ("step", "status"):
        value = deepcopy(valid)
        value["steps"][0][key] = {}
        invalid.append(value)
    for path in (("resources", "kind"), ("resources", "status"),
                 ("bookkeeping", "kind"), ("bookkeeping", "status")):
        value = deepcopy(valid)
        value["steps"][0][path[0]][0][path[1]] = []
        invalid.append(value)
    for value in invalid:
        assert serialize_execution_detail(value)["detail_status"] == "unavailable"


def test_running_composite_reports_pending_step_and_partial_outcome():
    journal = _Journal()
    journal.steps = [{"step": "raid", "status": "complete", "tracks_added": 2}]
    journal.resources = [r for r in journal.resources if r["step"] == "raid"]
    journal.outcome = lambda: {"aggregate_status": "complete"}
    detail = serialize_execution_detail(build_live_execution_detail(
        journal, 8, "target", run_state="running"
    ))
    assert detail["run_state"] == "running"
    assert detail["outcome"] == "partial"
    assert [(step["step"], step["status"]) for step in detail["steps"]] == [
        ("raid", "complete"), ("drip", "pending")
    ]


def test_write_and_read_allowlist_excludes_track_arrays_errors_and_credentials():
    raw = build_execution_detail(
        operation="drip", execution_id=42, outcome="partial",
        resource_ids={"target": "target", "collection": "collection", "token": "secret"},
        steps=[{
            "step": "drip", "status": "unknown", "counts": {
                "tracks_moved_to_target": 3, "tracks_removed_from_collection": None,
                "tracks_collected": -1, "secret": 7,
            },
            "resources": [{
                "resource": "target", "status": "complete", "kind": "target",
                "affected_count": 3, "dispatched": True,
                "baseline_track_uris": ["spotify:track:private"],
                "error_message": "raw exception", "access_token": "secret",
            }, {
                "resource": "collection", "status": "unknown", "kind": "collection",
                "affected_count": None, "dispatched": True,
            }],
            "bookkeeping": [{"kind": "pending_promotion", "status": "unknown", "error_code": "secret"}],
            "error": "raw exception",
        }],
    )
    text = str(raw)
    assert "spotify:track" not in text
    assert "secret" not in text
    assert "raw exception" not in text
    detail = serialize_execution_detail(raw)
    assert detail["steps"][0]["counts"] == {"tracks_moved_to_target": 3}
    assert detail["steps"][0]["resources"][1]["status"] == "unknown"
    assert "affected_count" not in detail["steps"][0]["resources"][1]


class _Journal:
    kind = "raid_and_drip"
    steps = [
        {"step": "raid", "status": "complete", "tracks_added": 2},
        {"step": "drip", "status": "unknown"},
    ]
    bookkeeping = [
        {"kind": "pending_stage", "step": "raid", "status": "complete", "affected_count": 2, "resource": "target"},
    ]
    resources = [
        {"resource": "collection", "step": "raid", "status": "complete", "dispatched": True,
         "affected_count": 2, "baseline_track_uris": [], "current_track_uris": ["secret1", "secret2"]},
        {"resource": "target", "step": "drip", "status": "restored", "dispatched": True,
         "affected_count": None, "baseline_track_uris": [], "restored_track_uris": []},
        {"resource": "collection", "step": "drip", "status": "unknown", "dispatched": True,
         "affected_count": None, "baseline_track_uris": ["secret1", "secret2"]},
    ]

    def step_resources(self, step):
        return [r for r in self.resources if r["step"] == step]

    def outcome(self):
        return {"aggregate_status": "unknown"}


def test_composite_preserves_earlier_collection_and_uncertain_restoration():
    detail = serialize_execution_detail(build_live_execution_detail(_Journal(), 9, "target"))
    assert detail["execution_id"] == 9
    assert detail["resource_ids"] == {"target": "target", "collection": "collection"}
    assert detail["steps"][0]["counts"] == {"tracks_collected": 2}
    assert detail["steps"][1]["status"] == "unknown"
    assert "tracks_moved_to_target" not in detail["steps"][1].get("counts", {})
    assert detail["steps"][1]["resources"][0]["status"] == "restored"
    assert detail["steps"][1]["resources"][1]["status"] == "unknown"


def test_closed_drip_gate_has_ordered_proven_zero_counts():
    journal = _Journal()
    journal.kind = "drip"
    journal.steps = [{"step": "drip", "status": "not_started"}]
    journal.bookkeeping = [{
        "kind": "pending_promotion", "step": "drip", "status": "not_required",
        "affected_count": 0, "resource": "target",
    }]
    journal.resources = [{
        "resource": "target", "step": "drip", "status": "not_required",
        "dispatched": False, "affected_count": 0,
    }]
    detail = serialize_execution_detail(build_live_execution_detail(journal, 14, "target"))
    assert detail["steps"][0]["counts"] == {
        "tracks_moved_to_target": 0, "tracks_removed_from_collection": 0,
    }


def test_rotation_counts_distinguish_archive_target_and_purge():
    journal = _Journal()
    journal.kind = "rotate"
    journal.steps = [{"step": "rotate", "status": "complete", "tracks_added": 1}]
    journal.bookkeeping = []
    journal.resources = [
        {"resource": "archive", "step": "archive_purge", "status": "complete", "dispatched": True,
         "baseline_track_uris": ["overlap", "older"], "current_track_uris": ["older"],
         "affected_count": 1},
        {"resource": "target", "step": "rotate", "status": "complete", "dispatched": True,
         "baseline_track_uris": ["out", "keep"], "current_track_uris": ["keep", "in"],
         "affected_count": 2},
        {"resource": "archive", "step": "rotate", "status": "complete", "dispatched": True,
         "baseline_track_uris": ["older", "in"], "current_track_uris": ["older", "out"],
         "affected_count": 2},
    ]

    def resources_for(step):
        return journal.resources

    journal.step_resources = resources_for
    detail = serialize_execution_detail(build_live_execution_detail(journal, 15, "target"))
    assert detail["steps"][0]["counts"] == {
        "archive_overlaps_removed": 1, "tracks_returned": 1,
        "tracks_removed_from_target": 1, "tracks_archived": 1,
        "tracks_removed_from_archive": 1,
    }
    assert "baseline_track_uris" not in str(detail)
    assert "older" not in str(detail)
