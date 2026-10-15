"""Actual archive moves are admitted once and preserve ordered requested occurrences."""

from tests.e2e.conftest import pid
from tests.paths import api_path

TARGET, ARCHIVE = pid("alice-morning-mix"), pid("alice-morning-archive")


def _prepared_archive(alice, fake):
    alice.ok(
        "post",
        f"/playlist/{TARGET}/pair",
        json={
            "archive_playlist_id": ARCHIVE,
            "archive_playlist_name": "Archive",
            "production_playlist_name": "Morning Mix",
        },
    )
    target = fake.uris(TARGET)
    source = [target[0], target[0], target[1]]
    alice.write(
        f"/workshop/{ARCHIVE}/commit",
        json={
            "baseline_track_uris": fake.uris(ARCHIVE),
            "track_uris": source,
        },
    )
    return target, source


def test_real_unarchive_queue_preserves_duplicates_and_confirms_both_undo_histories(alice, fake):
    before, source = _prepared_archive(alice, fake)
    requested = [before[0], before[0], before[2]]
    path = f"/playlist/{TARGET}/pair/unarchive"
    payload = {"track_uris": requested}
    queued = alice.queued(path, json=payload, headers={"Idempotency-Key": "unarchive-duplicates"})
    assert queued["state"] == "succeeded"
    assert queued["result"]["unarchived_count"] == 3
    assert queued["result"]["removed_count"] == 2
    assert fake.uris(TARGET) == before + requested
    assert fake.uris(ARCHIVE) == [before[1]]
    replay = alice.post(api_path(path), json=payload, headers={"Idempotency-Key": "unarchive-duplicates"})
    assert replay.status_code == 202 and replay.json()["operation_id"] == queued["id"]
    assert fake.uris(TARGET) == before + requested
    for resource, expected in [(TARGET, before), (ARCHIVE, source)]:
        context = alice.ok("post", f"/playlist/{resource}/live-context", json={"kind": "undo"})
        assert context["context"]["action"]["desired_track_uris"] == expected


def test_terminal_source_refusal_restores_only_this_actual_move(alice, fake):
    before, source = _prepared_archive(alice, fake)
    fake.arm(operation="DELETE /playlists/{playlist_id}/items", kind="quota", playlist_id=ARCHIVE)
    queued = alice.queued(f"/playlist/{TARGET}/pair/unarchive", json={"track_uris": [before[0], before[0]]})
    assert queued["state"] == "rolled_back"
    assert queued["result"]["unarchived_count"] == 0
    assert fake.uris(TARGET) == before
    assert fake.uris(ARCHIVE) == source
