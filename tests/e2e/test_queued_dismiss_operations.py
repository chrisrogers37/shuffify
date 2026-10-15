"""DB-first inbox dismissals are one accepted operation with separate source facts."""

import pytest

from tests.e2e.conftest import pid
from tests.paths import api_path

TARGET, SOURCE = pid("alice-morning-mix"), pid("bob-finds")


def collect(alice, fake):
    target = fake.uris(TARGET)
    link = alice.write(
        f"/playlist/{TARGET}/raid-link", json={"create_new": True, "target_playlist_name": "Morning Mix"}
    )
    collection = link["raid_link"]["raid_playlist_id"]
    alice.ok(
        "post",
        f"/playlist/{TARGET}/raid-watch",
        json={"source_playlist_id": SOURCE, "source_playlist_name": "Bob's Finds", "auto_schedule": False},
    )
    alice.write(f"/playlist/{TARGET}/raid-now", json={})
    pending = alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]
    assert len(pending) == 5
    return target, collection, pending


@pytest.mark.parametrize("suffix", ["dismiss", "dismiss-all"])
def test_real_queued_dismissal_replays_same_id_and_never_changes_target(alice, fake, suffix):
    target, collection, pending = collect(alice, fake)
    ids = [row["id"] for row in pending] if suffix == "dismiss-all" else [pending[0]["id"], pending[1]["id"]]
    path = f"/playlist/{TARGET}/pending-raids/{suffix}"
    body = {"track_ids": ids} if suffix == "dismiss" else None
    completed = alice.queued(path, json=body, headers={"Idempotency-Key": "one-dismiss"})
    assert completed["state"] == "succeeded"
    result = completed["result"]
    assert result["dismissed_count"] == len(ids) and result["dismissed_ids"] == sorted(ids)
    assert result["database_status"] == "complete" and result["source_status"] == "complete"
    assert result["source_removed_count"] == len(ids)
    assert fake.uris(TARGET) == target
    remaining = [row for row in pending if row["id"] not in ids]
    assert set(fake.uris(collection)) == {row["track_uri"] for row in remaining}
    assert {row["id"] for row in alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]} == {
        row["id"] for row in remaining
    }
    replay = alice.post(api_path(path), json=body, headers={"Idempotency-Key": "one-dismiss"})
    assert replay.status_code == 202 and replay.json()["operation_id"] == completed["id"]
    assert fake.uris(TARGET) == target


def test_terminal_cleanup_rejection_retains_the_confirmed_inbox_dismissal(alice, fake):
    target, collection, pending = collect(alice, fake)
    before = fake.uris(collection)
    fake.arm(operation="DELETE /playlists/{playlist_id}/items", kind="quota", playlist_id=collection)
    completed = alice.queued(f"/playlist/{TARGET}/pending-raids/dismiss", json={"track_ids": [pending[0]["id"]]})
    assert completed["state"] == "failed"
    result = completed["result"]
    assert result["dismissed_count"] == 1 and result["database_status"] == "complete"
    assert result["source_status"] == "failed" and result["source_removed_count"] == 0
    assert fake.uris(TARGET) == target and fake.uris(collection) == before
    assert pending[0]["id"] not in {row["id"] for row in alice.ok("get", f"/playlist/{TARGET}/pending-raids")["tracks"]}
