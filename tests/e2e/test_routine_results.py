"""Persisted routine step details agree with the fake's observed Spotify state."""

from tests.e2e.test_live_operation_outcomes import SOURCE, TARGET, _collection, _create_schedule


def test_composite_late_failure_keeps_collected_step_in_exact_history(alice, fake):
    collection = _collection(alice)
    alice.ok("put", f"/playlist/{TARGET}/raid-link", json={"drip_enabled": True, "drip_count": 2})
    schedule = _create_schedule(alice, "raid_and_drip", TARGET)
    sid = schedule["id"]
    original = fake.uris(TARGET)
    try:
        fake.arm(operation="DELETE /playlists/{playlist_id}/items", kind="quota", playlist_id=collection)
        operation = alice.queued(f"/schedules/{sid}/run")
        assert operation["state"] == "rolled_back", operation
        body = operation["result"]
        detail = body["result"]["result_detail"]
        execution_id = body["result"]["execution_id"]
        history = alice.ok("get", f"/schedules/{sid}/history")["history"]
        exact = next(item for item in history if item["id"] == execution_id)
        assert detail == exact["result_detail"]
        assert detail["execution_id"] == execution_id
        assert detail["run_state"] == "finished"
        assert detail["outcome"] in ("partial", "unknown")
        assert [step["step"] for step in detail["steps"]] == ["raid", "drip"]
        assert detail["steps"][0]["status"] == "complete"
        assert detail["steps"][0]["counts"]["tracks_collected"] == len(fake.uris(collection)) == 5
        assert detail["steps"][1]["status"] != "complete"
        assert fake.uris(TARGET) == original
        assert "spotify:track:" not in str(detail)
        assert SOURCE not in str(detail)
    finally:
        alice.ok("delete", f"/schedules/{sid}")


def test_closed_drip_gate_is_saved_as_not_started(alice, fake):
    collection = _collection(alice)
    alice.write(f"/playlist/{TARGET}/raid-now", json={})
    alice.ok("put", f"/playlist/{TARGET}/raid-link", json={"drip_enabled": True})
    schedule = _create_schedule(alice, "drip", TARGET)
    sid = schedule["id"]
    alice.ok("put", f"/playlist/{TARGET}/raid-link", json={"drip_enabled": False})
    before = (fake.playlist(TARGET), fake.playlist(collection))
    try:
        operation = alice.queued(f"/schedules/{sid}/run")
        assert operation["state"] == "failed", operation
        body = operation["result"]
        detail = body["result"]["result_detail"]
        history = alice.ok("get", f"/schedules/{sid}/history")["history"]
        assert history[0]["id"] == detail["execution_id"]
        assert history[0]["status"] == "skipped"
        assert history[0]["result_detail"] == detail
        assert detail["steps"][0]["step"] == "drip"
        assert detail["steps"][0]["status"] == "not_started"
        assert detail["steps"][0]["counts"] == {
            "tracks_moved_to_target": 0, "tracks_removed_from_collection": 0,
        }
        assert (fake.playlist(TARGET), fake.playlist(collection)) == before
    finally:
        alice.ok("delete", f"/schedules/{sid}")


def test_empty_collection_drip_records_proven_zero_counts(alice, fake):
    collection = _collection(alice)
    assert fake.uris(collection) == []
    alice.ok("put", f"/playlist/{TARGET}/raid-link", json={"drip_enabled": True})
    schedule = _create_schedule(alice, "drip", TARGET)
    sid = schedule["id"]
    original = fake.playlist(TARGET)
    try:
        run = alice.write(f"/schedules/{sid}/run")
        detail = run["result"]["result_detail"]
        assert detail["steps"][0]["status"] == "complete"
        assert detail["steps"][0]["counts"] == {
            "tracks_moved_to_target": 0, "tracks_removed_from_collection": 0,
        }
        history = alice.ok("get", f"/schedules/{sid}/history")["history"]
        assert history[0]["result_detail"] == detail
        assert history[0]["id"] == run["result"]["execution_id"]
        assert fake.uris(collection) == [] and fake.playlist(TARGET) == original
    finally:
        alice.ok("delete", f"/schedules/{sid}")
