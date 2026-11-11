"""A routine run when Spotify refuses the app, the grant or the account, against the fake.

The fake answers as armed (harness/fake_spotify/faults.py): the token endpoint refuses the app
(``invalid_client``) or the grant (``invalid_grant``), or a Web API call is refused for a user the
app does not list. The run ends `failed` and names which of the three it was, so what acts on it
never reads a message. Each test runs the schedule through the app (/schedules/<id>/run) and checks
Spotify's side in the fake's playlist.
"""

import pytest

from tests.e2e.conftest import pid
from tests.routine_flow import create_routine

LONG_HAUL = pid("alice-long-haul")  # 250 tracks: a PUT of 100, then two POSTs


@pytest.mark.parametrize(
    "fault, code",
    [
        # The worker builds its client from the stored refresh token, so its first call is a refresh.
        ({"kind": "app_rejected"}, "spotify_app_rejected"),
        ({"kind": "grant_revoked"}, "spotify_grant_revoked"),
        # A write is a call only the worker makes, so the fault cannot land on the web side.
        (
            {"operation": "PUT /playlists/{playlist_id}/items", "kind": "user_not_allowed", "playlist_id": LONG_HAUL},
            "spotify_user_not_allowed",
        ),
    ],
    ids=["app_rejected", "grant_revoked", "user_not_allowed"],
)
def test_a_run_refused_for_the_app_the_grant_or_the_account_is_named_so(alice, fake, fault, code):
    before = fake.uris(LONG_HAUL)
    schedule_id = create_routine(alice, LONG_HAUL)["id"]
    fake.arm(**fault)

    operation = alice.queued(f"/schedules/{schedule_id}/run")

    assert operation["state"] == "failed", operation
    outcome = operation["result"]
    assert outcome["success"] is False
    assert outcome["operation"]["error_code"] == code
    assert fake.uris(LONG_HAUL) == before, "a refused run changed the playlist"
    assert [f["fired"] for f in fake.faults()] == [1]
