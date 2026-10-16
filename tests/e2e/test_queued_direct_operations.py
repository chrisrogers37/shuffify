"""The running API admits direct writes and an idle worker wakes to complete them."""

import time

from tests.e2e.conftest import pid

TARGET = pid("alice-morning-mix")


def complete(alice, path, *, key, **options):
    response = alice.post(path, headers={"Idempotency-Key": key}, **options)
    assert response.status_code == 202, response.text
    accepted = response.json()
    operation_id = accepted["operation_id"]
    location = response.headers["Location"]
    assert location == f"/api/operations/{operation_id}"
    replay = alice.post(path, headers={"Idempotency-Key": key}, **options)
    assert replay.status_code == 202 and replay.json()["operation_id"] == operation_id
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        polled = alice.get(location)
        assert polled.status_code == 200
        operation = polled.json()["operation"]
        assert operation["id"] == operation_id
        if operation["state"] not in {"queued", "running", "compensating"}:
            assert operation["state"] == "succeeded", operation
            assert operation["result"]["success"] is True, operation
            return operation
        time.sleep(0.05)
    raise AssertionError(f"The idle worker did not complete accepted operation {operation_id}")


def test_all_direct_commands_cross_the_real_api_worker_and_spotify_transport(alice, fake):
    before = fake.playlist(TARGET)
    shuffle = complete(alice, f"/api/shuffle/{TARGET}", key="shuffle", data={"algorithm": "BasicShuffle"})
    after = fake.uris(TARGET)
    assert after != before["uris"] and sorted(after) == sorted(before["uris"])
    assert shuffle["result"]["playlist_state"]["current_index"] == 1
    # A review reads the confirmed receipt before any Undo is admitted.
    review = alice.post(f"/api/playlist/{TARGET}/live-context", json={"kind": "undo"})
    assert review.status_code == 200
    assert review.json()["context"]["action"]["desired_track_uris"] == before["uris"]
    undo = complete(alice, f"/api/undo/{TARGET}", key="undo")
    assert fake.uris(TARGET) == before["uris"]
    assert undo["result"]["playlist_state"]["current_index"] == 0
    complete(alice, f"/api/playlist/{TARGET}/toggle-visibility", key="privacy", json={"public": not before["public"]})
    assert fake.playlist(TARGET)["public"] is not before["public"]
    assert fake.uris(TARGET) == before["uris"]
    snapshot = alice.ok("post", f"/playlist/{TARGET}/snapshots", json={
        "playlist_name": "Morning Mix", "track_uris": before["uris"],
    })["snapshot"]
    desired = list(reversed(before["uris"]))
    complete(alice, f"/api/workshop/{TARGET}/commit", key="commit", json={
        "baseline_track_uris": before["uris"], "track_uris": desired,
    })
    assert fake.uris(TARGET) == desired
    restored = complete(alice, f"/api/snapshots/{snapshot['id']}/restore", key="snapshot", json={})
    assert fake.uris(TARGET) == before["uris"]
    assert restored["result"]["snapshot"]["id"] == snapshot["id"]
