"""
A failed database write never puts driver text in a response.

Every request path that used to echo the exception raises a driver error at
commit, built from a sentinel statement, bound value and host. The response
carries the authored sentence and none of the sentinels, while deliberate
messages of the same exception classes are unchanged.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from shuffify.enums import SnapshotType
from shuffify.models.db import Schedule, User, db
from shuffify.services.playlist_snapshot_service import PlaylistSnapshotService
from shuffify.services.user_settings_service import UserSettingsService
from shuffify.services.workshop_session_service import WorkshopSessionService
from tests.paths import api_path

STATEMENT = "INSERT INTO sentinel_table_zq (sentinel_column_zq) VALUES (?)"
BOUND_VALUE = "SENTINEL-BOUND-VALUE-ZQ"
HOST = "db.sentinel.invalid"
HOST_MESSAGE = f'could not translate host name "{HOST}" to address'

SENTINELS = (
    "[SQL:", "[parameters:", "sqlalche.me", "sentinel_table_zq", "sentinel_column_zq",
    BOUND_VALUE, HOST, "5432", "OperationalError",
)

# A statement failure carries SQL and its values; a connection failure, the host.
DRIVER_FAILURES = {
    "statement": OperationalError(STATEMENT, (BOUND_VALUE,), Exception("rejected")),
    "connection": OperationalError(None, None, Exception(f"{HOST_MESSAGE}:5432")),
}


@pytest.fixture(params=sorted(DRIVER_FAILURES))
def failing_commit(request, db_app):
    """Arm after the test's own setup: every commit then raises the driver error."""
    failure = DRIVER_FAILURES[request.param]
    assert (BOUND_VALUE in str(failure)) or (HOST in str(failure))

    def arm():
        return patch.object(db.session, "commit", side_effect=failure)

    return arm


@pytest.fixture
def user(auth_client):
    return User.query.filter_by(spotify_id="user123").one()


def assert_authored(resp, status, message):
    body = resp.get_data(as_text=True)
    assert resp.status_code == status, body
    assert resp.get_json()["message"] == message
    for sentinel in SENTINELS:
        assert sentinel not in body


@patch("shuffify.routes.require_auth")
class TestWriteFailuresReturnAuthoredMessages:
    def test_create_snapshot(self, mock_auth, auth_client, failing_commit):
        mock_auth.return_value = MagicMock()
        with failing_commit():
            resp = auth_client.post(
                api_path("/playlist/p1/snapshots"),
                json={"playlist_name": "SENTINEL-NAME", "track_uris": []},
            )
        assert_authored(resp, 500, "Failed to create snapshot.")
        assert "SENTINEL-NAME" not in resp.get_data(as_text=True)

    def test_delete_snapshot(self, mock_auth, auth_client, user, failing_commit):
        mock_auth.return_value = MagicMock()
        snapshot = PlaylistSnapshotService.create_snapshot(user.id, "p1", "Name", [], SnapshotType.MANUAL)
        with failing_commit():
            resp = auth_client.delete(api_path(f"/snapshots/{snapshot.id}"))
        assert_authored(resp, 500, "Failed to delete snapshot.")

    def test_save_workshop_session(self, mock_auth, auth_client, failing_commit):
        mock_auth.return_value = MagicMock()
        with failing_commit():
            resp = auth_client.post(
                api_path("/workshop/p1/sessions"),
                json={"session_name": "SENTINEL-NAME", "track_uris": []},
            )
        assert_authored(resp, 500, "Failed to save session.")
        assert "SENTINEL-NAME" not in resp.get_data(as_text=True)

    def test_update_workshop_session(self, mock_auth, auth_client, failing_commit):
        mock_auth.return_value = MagicMock()
        session_id = WorkshopSessionService.save_session("user123", "p1", "Saved", []).id
        with failing_commit():
            resp = auth_client.put(
                api_path(f"/workshop/sessions/{session_id}"),
                json={"session_name": "SENTINEL-NAME", "track_uris": []},
            )
        assert_authored(resp, 500, "Failed to update session.")
        assert "SENTINEL-NAME" not in resp.get_data(as_text=True)

    def test_delete_workshop_session(self, mock_auth, auth_client, failing_commit):
        mock_auth.return_value = MagicMock()
        session_id = WorkshopSessionService.save_session("user123", "p1", "Saved", []).id
        with failing_commit():
            resp = auth_client.delete(api_path(f"/workshop/sessions/{session_id}"))
        assert_authored(resp, 500, "Failed to delete session.")

    def test_update_settings(self, mock_auth, auth_client, user, failing_commit):
        mock_auth.return_value = MagicMock()
        UserSettingsService.get_or_create(user.id)
        with failing_commit():
            resp = auth_client.post(api_path("/settings"), json={"max_snapshots_per_playlist": 7})
        assert_authored(resp, 500, "Failed to save settings.")

    def test_update_raid_link(self, mock_auth, auth_client, raid_target, failing_commit):
        mock_auth.return_value = MagicMock()
        with failing_commit():
            resp = auth_client.put(api_path("/playlist/target/raid-link"), json={"drip_count": 4})
        assert_authored(resp, 500, "Failed to update raid link")

    def test_delete_raid_link(self, mock_auth, auth_client, raid_target, failing_commit):
        mock_auth.return_value = MagicMock()
        with failing_commit():
            resp = auth_client.delete(api_path("/playlist/target/raid-link"))
        assert_authored(resp, 500, "Failed to delete raid link")

    def test_delete_schedule(self, mock_auth, auth_client, user, failing_commit):
        mock_auth.return_value = MagicMock()
        row = Schedule(
            user_id=user.id, job_type="shuffle", target_playlist_id="target",
            target_playlist_name="Target", algorithm_name="BasicShuffle", algorithm_params={},
        )
        db.session.add(row)
        db.session.commit()
        with failing_commit():
            resp = auth_client.delete(api_path(f"/schedules/{row.id}"))
        assert_authored(resp, 500, "Schedule operation failed.")


@patch("shuffify.routes.require_auth")
class TestDeliberateMessagesAreKept:
    def test_workshop_empty_name_message(self, mock_auth, auth_client):
        mock_auth.return_value = MagicMock()
        with patch("shuffify.routes.workshop.WorkshopSessionService.save_session") as save:
            from shuffify.services import WorkshopSessionError

            save.side_effect = WorkshopSessionError("Session name cannot be empty")
            resp = auth_client.post(api_path("/workshop/p1/sessions"), json={"session_name": "x", "track_uris": []})
        assert (resp.status_code, resp.get_json()["message"]) == (500, "Session name cannot be empty")

    def test_workshop_limit_message(self, mock_auth, auth_client):
        mock_auth.return_value = MagicMock()
        for number in range(10):
            WorkshopSessionService.save_session("user123", "p1", f"S{number}", [])
        resp = auth_client.post(api_path("/workshop/p1/sessions"), json={"session_name": "extra", "track_uris": []})
        assert resp.status_code == 400
        assert resp.get_json()["message"] == (
            "Maximum of 10 saved sessions per playlist reached. Delete an existing session first."
        )

    def test_settings_validation_message(self, mock_auth, auth_client):
        mock_auth.return_value = MagicMock()
        from shuffify.services import UserSettingsError

        with patch("shuffify.routes.settings.UserSettingsService.update") as update:
            update.side_effect = UserSettingsError("Invalid theme 'x'. Valid: dark, light, system")
            resp = auth_client.post(api_path("/settings"), json={"max_snapshots_per_playlist": 7})
        assert (resp.status_code, resp.get_json()["message"]) == (400, "Invalid theme 'x'. Valid: dark, light, system")

    def test_schedule_error_message(self, mock_auth, auth_client):
        mock_auth.return_value = MagicMock()
        from shuffify.services import ScheduleError

        with patch("shuffify.routes.schedules.SchedulerService.delete_schedule") as delete:
            delete.side_effect = ScheduleError("Schedule changed while it was being updated. Try again.")
            resp = auth_client.delete(api_path("/schedules/1"))
        assert (resp.status_code, resp.get_json()["message"]) == (
            500, "Schedule changed while it was being updated. Try again."
        )


@patch("shuffify.routes.require_auth")
def test_failed_write_is_still_logged_with_its_cause(mock_auth, auth_client, failing_commit, caplog):
    mock_auth.return_value = MagicMock()
    with caplog.at_level(logging.ERROR), failing_commit():
        auth_client.post(api_path("/playlist/p1/snapshots"), json={"playlist_name": "N", "track_uris": []})
    records = [r for r in caplog.records if r.name == "shuffify.services.base"]
    assert records and records[0].exc_info is not None
    assert isinstance(records[0].exc_info[1], OperationalError)


@patch("shuffify.routes.require_auth")
def test_real_statement_failure_hides_parameters(mock_auth, auth_client):
    mock_auth.return_value = MagicMock()
    db.session.execute(text(
        "CREATE TRIGGER reject BEFORE INSERT ON playlist_snapshots "
        "BEGIN SELECT RAISE(ABORT, 'rejected'); END"))
    db.session.commit()
    resp = auth_client.post(
        api_path("/playlist/p1/snapshots"), json={"playlist_name": "SENTINEL-NAME", "track_uris": []}
    )
    assert_authored(resp, 500, "Failed to create snapshot.")
    assert "SENTINEL-NAME" not in resp.get_data(as_text=True)
    with pytest.raises(Exception) as raised:
        db.session.execute(text("INSERT INTO playlist_snapshots (user_id) VALUES (:v)"), {"v": "SENTINEL-NAME"})
    assert "[SQL parameters hidden due to hide_parameters=True]" in str(raised.value)
    assert "SENTINEL-NAME" not in str(raised.value)
    db.session.rollback()
