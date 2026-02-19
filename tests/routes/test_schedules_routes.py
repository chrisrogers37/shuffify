"""
Tests for schedule routes.

Tests cover the 8 schedule endpoints including CRUD,
toggle, manual run, execution history, and rotation status.
"""

import time
import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db
from shuffify.services.user_service import UserService


@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"
    os.environ.pop("DATABASE_URL", None)

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///:memory:"
    )
    app.config["TESTING"] = True
    app.config["SCHEDULER_ENABLED"] = False

    with app.app_context():
        db.drop_all()
        db.create_all()
        UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def auth_client(db_app):
    """Authenticated test client with session user data."""
    with db_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "expires_at": time.time() + 3600,
                "refresh_token": "test_refresh",
            }
            sess["user_data"] = {
                "id": "user123",
                "display_name": "Test User",
            }
        yield client


# Note: Schedule routes use two auth patterns:
# - GET /schedules uses is_authenticated() directly (302 redirect)
# - All other endpoints use @require_auth_and_db (401 JSON)
# The decorator resolves require_auth/get_db_user from
# shuffify.routes.__init__, NOT from schedules module.


class TestSchedulesAuth:
    """Auth guard tests for all schedule endpoints."""

    def test_get_schedules_unauth_redirects(self, db_app):
        """GET /schedules without auth returns 302 redirect."""
        with db_app.test_client() as client:
            resp = client.get("/schedules")
            assert resp.status_code == 302

    @patch("shuffify.routes.require_auth")
    def test_create_unauth_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(
                "/schedules/create",
                json={"job_type": "shuffle"},
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_update_unauth_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.put(
                "/schedules/1", json={}
            )
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_delete_unauth_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete("/schedules/1")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_toggle_unauth_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post("/schedules/1/toggle")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_run_unauth_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post("/schedules/1/run")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_history_unauth_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/schedules/1/history")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_rotation_status_unauth_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(
                "/playlist/p1/rotation-status"
            )
            assert resp.status_code == 401


class TestGetSchedulesPage:
    """Tests for GET /schedules."""

    @patch("shuffify.routes.schedules.ShuffleService")
    @patch("shuffify.routes.schedules.PlaylistService")
    @patch("shuffify.routes.schedules.get_db_user")
    @patch("shuffify.routes.schedules.AuthService")
    @patch("shuffify.routes.schedules.is_authenticated")
    def test_renders_page_for_authenticated_user(
        self,
        mock_is_auth,
        mock_auth_service,
        mock_get_db_user,
        mock_ps_class,
        mock_shuffle_svc,
        auth_client,
    ):
        mock_is_auth.return_value = True
        mock_client = MagicMock()
        mock_auth_service.get_authenticated_client.return_value = (
            mock_client
        )
        mock_auth_service.get_user_data.return_value = {
            "id": "user123",
            "display_name": "Test User",
        }

        mock_db_user = MagicMock()
        mock_db_user.id = 1
        mock_get_db_user.return_value = mock_db_user

        from shuffify.services.scheduler_service import (
            SchedulerService,
        )

        with patch.object(
            SchedulerService,
            "get_user_schedules",
            return_value=[],
        ):
            mock_ps = MagicMock()
            mock_ps.get_user_playlists.return_value = []
            mock_ps_class.return_value = mock_ps
            mock_shuffle_svc.list_algorithms.return_value = (
                []
            )

            resp = auth_client.get("/schedules")
            assert resp.status_code == 200

    @patch("shuffify.routes.schedules.get_db_user")
    @patch("shuffify.routes.schedules.AuthService")
    @patch("shuffify.routes.schedules.is_authenticated")
    def test_no_db_user_redirects(
        self,
        mock_is_auth,
        mock_auth_service,
        mock_get_db_user,
        auth_client,
    ):
        mock_is_auth.return_value = True
        mock_client = MagicMock()
        mock_auth_service.get_authenticated_client.return_value = (
            mock_client
        )
        mock_auth_service.get_user_data.return_value = {
            "id": "user123",
            "display_name": "Test User",
        }
        mock_get_db_user.return_value = None

        resp = auth_client.get("/schedules")
        assert resp.status_code == 302


class TestCreateSchedule:
    """Tests for POST /schedules/create."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_create_success(
        self,
        mock_auth,
        mock_sched_svc,
        mock_add_job,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()

        # Route checks user.encrypted_refresh_token
        user = UserService.get_by_spotify_id("user123")
        user.encrypted_refresh_token = "enc_token"
        db.session.commit()

        mock_schedule = MagicMock()
        mock_schedule.id = 1
        mock_schedule.job_type = "shuffle"
        mock_schedule.target_playlist_name = "My Playlist"
        mock_schedule.target_playlist_id = "p1"
        mock_schedule.is_enabled = True
        mock_schedule.to_dict.return_value = {"id": 1}
        mock_sched_svc.create_schedule.return_value = (
            mock_schedule
        )

        resp = auth_client.post(
            "/schedules/create",
            json={
                "job_type": "shuffle",
                "target_playlist_id": "p1",
                "target_playlist_name": "My Playlist",
                "algorithm_name": "BasicShuffle",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("shuffify.routes.require_auth")
    def test_missing_refresh_token_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()

        # The real db_user from db_app fixture has
        # no encrypted_refresh_token set
        resp = auth_client.post(
            "/schedules/create",
            json={
                "job_type": "shuffle",
                "target_playlist_id": "p1",
                "target_playlist_name": "My Playlist",
                "algorithm_name": "BasicShuffle",
            },
        )
        assert resp.status_code == 400

    @patch("shuffify.routes.require_auth")
    def test_no_json_body_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()

        # Set encrypted_refresh_token so we get past
        # that check
        user = UserService.get_by_spotify_id("user123")
        user.encrypted_refresh_token = "enc_token"
        db.session.commit()

        resp = auth_client.post(
            "/schedules/create",
            json={},
        )
        assert resp.status_code == 400


class TestUpdateSchedule:
    """Tests for PUT /schedules/<int:schedule_id>."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_update_success(
        self,
        mock_auth,
        mock_sched_svc,
        mock_add_job,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()

        mock_schedule = MagicMock()
        mock_schedule.is_enabled = True
        mock_schedule.target_playlist_id = "p1"
        mock_schedule.target_playlist_name = "My Playlist"
        mock_schedule.to_dict.return_value = {"id": 1}
        mock_sched_svc.update_schedule.return_value = (
            mock_schedule
        )

        resp = auth_client.put(
            "/schedules/1",
            json={"schedule_value": "weekly"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("shuffify.routes.require_auth")
    def test_empty_json_body_returns_400(
        self, mock_auth, auth_client
    ):
        mock_auth.return_value = MagicMock()

        resp = auth_client.put(
            "/schedules/1", json={}
        )
        assert resp.status_code == 400


class TestDeleteSchedule:
    """Tests for DELETE /schedules/<int:schedule_id>."""

    @patch("shuffify.scheduler.remove_job_for_schedule")
    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_delete_success(
        self,
        mock_auth,
        mock_sched_svc,
        mock_remove_job,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()

        resp = auth_client.delete("/schedules/1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch("shuffify.scheduler.remove_job_for_schedule")
    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_delete_not_found_returns_404(
        self,
        mock_auth,
        mock_sched_svc,
        mock_remove_job,
        auth_client,
    ):
        from shuffify.services import ScheduleNotFoundError

        mock_auth.return_value = MagicMock()
        mock_sched_svc.delete_schedule.side_effect = (
            ScheduleNotFoundError("not found")
        )

        resp = auth_client.delete("/schedules/99")
        assert resp.status_code == 404


class TestToggleSchedule:
    """Tests for POST /schedules/<int:schedule_id>/toggle."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_toggle_enables(
        self,
        mock_auth,
        mock_sched_svc,
        mock_add_job,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()

        mock_schedule = MagicMock()
        mock_schedule.is_enabled = True
        mock_schedule.target_playlist_id = "p1"
        mock_schedule.target_playlist_name = "My Playlist"
        mock_schedule.to_dict.return_value = {
            "id": 1,
            "is_enabled": True,
        }
        mock_sched_svc.toggle_schedule.return_value = (
            mock_schedule
        )

        resp = auth_client.post("/schedules/1/toggle")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "enabled" in data["message"]

    @patch("shuffify.scheduler.remove_job_for_schedule")
    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_toggle_disables(
        self,
        mock_auth,
        mock_sched_svc,
        mock_remove_job,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()

        mock_schedule = MagicMock()
        mock_schedule.is_enabled = False
        mock_schedule.target_playlist_id = "p1"
        mock_schedule.target_playlist_name = "My Playlist"
        mock_schedule.to_dict.return_value = {
            "id": 1,
            "is_enabled": False,
        }
        mock_sched_svc.toggle_schedule.return_value = (
            mock_schedule
        )

        resp = auth_client.post("/schedules/1/toggle")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "disabled" in data["message"]


class TestRunScheduleNow:
    """Tests for POST /schedules/<int:schedule_id>/run."""

    @patch(
        "shuffify.routes.schedules.JobExecutorService"
    )
    @patch("shuffify.routes.require_auth")
    def test_run_success(
        self,
        mock_auth,
        mock_executor,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()
        mock_executor.execute_now.return_value = {
            "status": "success"
        }

        resp = auth_client.post("/schedules/1/run")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    @patch(
        "shuffify.routes.schedules.JobExecutorService"
    )
    @patch("shuffify.routes.require_auth")
    def test_run_execution_error_returns_500(
        self,
        mock_auth,
        mock_executor,
        auth_client,
    ):
        from shuffify.services import JobExecutionError

        mock_auth.return_value = MagicMock()
        mock_executor.execute_now.side_effect = (
            JobExecutionError("execution failed")
        )

        resp = auth_client.post("/schedules/1/run")
        assert resp.status_code == 500


class TestScheduleHistory:
    """Tests for GET /schedules/<int:schedule_id>/history."""

    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_returns_history_list(
        self,
        mock_auth,
        mock_sched_svc,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()
        mock_sched_svc.get_execution_history.return_value = [
            {"id": 1, "status": "success"}
        ]

        resp = auth_client.get("/schedules/1/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["history"]) == 1


class TestRotationStatus:
    """Tests for GET /playlist/<id>/rotation-status."""

    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_no_pair_no_schedule(
        self,
        mock_auth,
        mock_sched_svc,
        auth_client,
    ):
        mock_auth.return_value = MagicMock()
        mock_sched_svc.get_user_schedules.return_value = []

        resp = auth_client.get(
            "/playlist/p1/rotation-status"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["has_pair"] is False
        assert data["has_rotation_schedule"] is False
