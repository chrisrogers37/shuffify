"""Creation admissions retain one known private identity and one local association receipt."""

import pytest

from tests.e2e.conftest import pid
from tests.paths import api_path

TARGET = pid("alice-morning-mix")


@pytest.mark.parametrize(
    "suffix,body,association",
    [
        ("pair", {"create_new": True, "production_playlist_name": "Morning Mix"}, "pair"),
        ("raid-link", {"create_new": True, "target_playlist_name": "Morning Mix"}, "raid_link"),
        ("setup/spotify-playlists", {"purpose": "archive", "name": "Standalone archive"}, None),
    ],
)
def test_real_creation_queue_replay_keeps_one_private_playlist_and_target_unchanged(
    alice,
    fake,
    suffix,
    body,
    association,
):
    path = f"/playlist/{TARGET}/{suffix}"
    before_ids = {row["id"] for row in fake.playlists()}
    target = fake.playlist(TARGET)
    response = alice.post(api_path(path), json=body, headers={"Idempotency-Key": "one-asset"})
    assert response.status_code == 202, response.text
    operation_id = response.json()["operation_id"]
    completed = alice.wait_operation(response.headers["Location"], operation_id)
    assert completed["state"] == "succeeded", completed
    result = completed["result"]
    created_id = result["playlist"]["id"]
    assert result["privacy_confirmed"] is True
    assert fake.playlist(created_id)["public"] is False
    assert fake.playlist(TARGET) == target
    assert {row["id"] for row in fake.playlists()} - before_ids == {created_id}
    if association:
        assert result[association][("archive" if association == "pair" else "raid") + "_playlist_id"] == created_id
    else:
        context = alice.ok("get", f"/playlist/{TARGET}/setup/context")["context"]
        assert context["archive"] is None and context["collection"] is None
    replay = alice.post(api_path(path), json=body, headers={"Idempotency-Key": "one-asset"})
    assert replay.status_code == 202 and replay.json()["operation_id"] == operation_id
    assert {row["id"] for row in fake.playlists()} - before_ids == {created_id}
