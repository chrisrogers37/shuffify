"""Routine page contexts read registration once, and never infer it from enabled rows."""

import pytest

from shuffify.models.db import Schedule, User, db
from shuffify.services.routine_activation_service import RoutineActivationService
from tests.playlist_setup_fixtures import SetupSpotify
from tests.routes.test_page_contexts import context_api as context_api


def rows():
    user = User.query.filter_by(spotify_id="user123").one()
    schedules = [
        Schedule(
            user_id=user.id,
            job_type="shuffle",
            target_playlist_id="target",
            target_playlist_name="Target",
            algorithm_name="BasicShuffle",
            schedule_type="interval",
            schedule_value="daily",
            is_enabled=enabled,
        )
        for enabled in (True, False)
    ]
    db.session.add_all(schedules)
    db.session.commit()
    return user, schedules


@pytest.mark.parametrize("path", ["/api/routines", "/schedules"])
def test_each_page_list_batches_worker_memory_once(auth_client, context_api, db_app, monkeypatch, path):
    context_api.get_user_playlists.return_value = []
    user, schedules = rows()
    calls = []

    def unavailable(self, ids):
        calls.append(ids)
        return None

    monkeypatch.setattr("shuffify.services.worker_control.WorkerControlClient.registrations", unavailable)
    response = auth_client.get(path)
    assert response.status_code == 200
    assert len(calls) == 1 and set(calls[0]) == {row.id for row in schedules}
    if path.startswith("/api"):
        assert all(row["registration"]["status"] == "unavailable" for row in response.json["routines"])
        assert all(row["state"] == "needs_attention" for row in response.json["routines"])
    else:
        assert b"Saved" in response.data


def test_setup_context_batches_and_activation_direct_writes_emit_postcommit_hint(
    auth_client,
    context_api,
    db_app,
    monkeypatch,
):
    import shuffify.services.schedule_notifications as notifications
    import tests.test_routine_activation as activation

    user, schedules = rows()
    calls, sent, poked = [], [], []
    monkeypatch.setattr(
        "shuffify.services.worker_control.WorkerControlClient.registrations", lambda self, ids: calls.append(ids)
    )
    monkeypatch.setattr(notifications, "publish", lambda session: sent.append(session.in_transaction()))
    monkeypatch.setattr(notifications, "poke", lambda app: poked.append(True))
    api = SetupSpotify()
    payload, status = RoutineActivationService.context(api, user, "target")
    assert status == 200 and len(calls) == 1
    assert all(row["registration"]["status"] == "unavailable" for row in payload["routines"])
    request = activation._request("direct-activation", create_another=True)
    review = activation._review(user, api, request)
    answer, status = activation._activate(user, api, request, review)
    assert status == 200 and sent == [True] and poked == [True]
    routine = db.session.get(Schedule, answer["schedule_id"])
    answer, status = RoutineActivationService.set_state(
        api,
        user,
        routine.id,
        {
            "desired_enabled": False,
            "expected_revision": activation.schedule_revision(routine),
        },
    )
    assert status == 200 and sent == [True, True] and poked == [True, True]
