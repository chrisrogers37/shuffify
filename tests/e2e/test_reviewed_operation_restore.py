"""Review and restore known effects through the isolated HTTP API and worker."""

import pytest

from tests.e2e.conftest import App, pid
from tests.paths import api_path

pytestmark = pytest.mark.integration
TARGET = pid("alice-long-haul")


def interrupted_save(alice, fake):
    baseline = fake.uris(TARGET)
    desired = list(reversed(baseline))
    fake.arm(operation="POST /playlists/{playlist_id}/items", kind="quota", playlist_id=TARGET)
    fake.arm(operation="PUT /playlists/{playlist_id}/items", kind="quota", playlist_id=TARGET, skip=1)
    parent = alice.queued(
        f"/workshop/{TARGET}/commit",
        json={
            "baseline_track_uris": baseline,
            "track_uris": desired,
        },
    )
    assert parent["state"] == "needs_attention", parent
    assert fake.uris(TARGET) == desired[:100]
    assert parent["recovery"]["available"] is True, parent["recovery"]
    return parent, baseline


def test_reviewed_restoration_resolves_the_original_and_replays_one_child(alice, fake):
    parent, baseline = interrupted_save(alice, fake)
    context = alice.ok("get", f"/operations/{parent['id']}/restore-context")["context"]
    resource = context["resources"][0]
    assert resource["current_count"] == 100 and resource["desired_count"] == 250
    assert resource["desired_preview"] == baseline[:20]
    body = {"reviewed": context["reviewed"]}
    path = f"/operations/{parent['id']}/restore"
    restored = alice.queued(path, json=body, headers={"Idempotency-Key": "reviewed-restore-once"})
    assert restored["state"] == "succeeded", restored
    assert restored["result"]["parent_operation_id"] == parent["id"]
    assert restored["result"]["parent_resolution"] == "complete"
    assert restored["result"]["restored_resource_count"] == 1
    assert fake.uris(TARGET) == baseline
    resolved = alice.ok("get", f"/operations/{parent['id']}")["operation"]
    assert resolved["state"] == "needs_attention"
    assert resolved["recovery"]["resolved_operation_id"] == restored["id"]
    replay = alice.post(api_path(path), json=body, headers={"Idempotency-Key": "reviewed-restore-once"})
    assert replay.status_code == 202 and replay.json()["operation_id"] == restored["id"]
    fresh = alice.post(api_path(path), json=body, headers={"Idempotency-Key": "fresh-after-resolution"})
    assert fresh.status_code == 409
    assert fresh.json()["error_code"] == "operation_already_restored"
    assert fresh.json()["operation_id"] == restored["id"]


def test_review_change_refuses_restore_before_writing_and_allows_fresh_review(alice, fake):
    parent, _ = interrupted_save(alice, fake)
    context = alice.ok("get", f"/operations/{parent['id']}/restore-context")["context"]
    current = fake.uris(TARGET)
    changed = current[1:] + current[:1]
    saved = alice.queued(
        f"/workshop/{TARGET}/commit",
        json={
            "baseline_track_uris": current,
            "track_uris": changed,
        },
    )
    assert saved["state"] == "succeeded", saved
    before = fake.playlist(TARGET)
    refused = alice.queued(f"/operations/{parent['id']}/restore", json={"reviewed": context["reviewed"]})
    assert refused["state"] == "failed", refused
    assert refused["result"]["error_code"] == "restore_review_changed"
    assert fake.playlist(TARGET) == before
    fresh = alice.ok("get", f"/operations/{parent['id']}/restore-context")["context"]
    assert fresh["reviewed"] != context["reviewed"]


def test_foreign_owner_cannot_read_or_admit_restoration(alice, fake):
    parent, _ = interrupted_save(alice, fake)
    other = App("bob", fake.display_name("bob"))
    before = fake.playlist(TARGET)
    for method, suffix, kwargs in [("GET", "restore-context", {}), ("POST", "restore", {"json": {}})]:
        response = other.request(method, api_path(f"/operations/{parent['id']}/{suffix}"), **kwargs)
        assert response.status_code == 404
    assert fake.playlist(TARGET) == before


def test_unknown_original_write_cannot_be_restored_from_settled_reads(alice, fake):
    baseline = fake.uris(TARGET)
    fake.arm(operation="PUT /playlists/{playlist_id}/items", kind="server_error", playlist_id=TARGET)
    parent = alice.queued(
        f"/workshop/{TARGET}/commit",
        json={
            "baseline_track_uris": baseline,
            "track_uris": list(reversed(baseline)),
        },
    )
    assert parent["state"] == "needs_attention", parent
    before = fake.playlist(TARGET)
    response = alice.get(api_path(f"/operations/{parent['id']}/restore-context"))
    assert response.status_code == 409
    assert response.json()["error_code"] == "write_completion_unknown"
    assert fake.playlist(TARGET) == before
