"""
Tests for schedule routes.

Tests cover the 8 schedule endpoints including CRUD,
toggle, manual run, execution history, and rotation status.
"""

from unittest.mock import patch, MagicMock

from shuffify.models.db import db
from shuffify.services.user_service import UserService



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

    @patch(
        "shuffify.routes.schedules.PlaylistPairService"
    )
    @patch(
        "shuffify.routes.schedules.UpstreamSourceService"
    )
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
        mock_upstream_svc,
        mock_pair_svc,
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
        mock_db_user.spotify_id = "user123"
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
            mock_upstream_svc.list_all_sources_for_user.return_value = (
                []
            )
            mock_pair_svc.get_pairs_for_user.return_value = (
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


class TestAPSchedulerErrorHandling:
    """Tests that APScheduler failures don't mask route success."""

    def _make_schedule_mock(self):
        """Helper to create a mock schedule."""
        mock_schedule = MagicMock()
        mock_schedule.id = 1
        mock_schedule.job_type = "shuffle"
        mock_schedule.target_playlist_name = "My Playlist"
        mock_schedule.target_playlist_id = "p1"
        mock_schedule.is_enabled = True
        mock_schedule.schedule_value = "daily"
        mock_schedule.to_dict.return_value = {"id": 1}
        return mock_schedule

    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_create_succeeds_despite_key_error(
        self,
        mock_auth,
        mock_sched_svc,
        auth_client,
    ):
        """Non-RuntimeError from APScheduler must not mask success."""
        mock_auth.return_value = MagicMock()

        user = UserService.get_by_spotify_id("user123")
        user.encrypted_refresh_token = "enc_token"
        db.session.commit()

        mock_sched_svc.create_schedule.return_value = (
            self._make_schedule_mock()
        )

        with patch(
            "shuffify.scheduler.add_job_for_schedule",
            side_effect=KeyError("ConflictingId"),
        ):
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

    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_create_succeeds_despite_runtime_error(
        self,
        mock_auth,
        mock_sched_svc,
        auth_client,
    ):
        """RuntimeError from APScheduler still handled (regression)."""
        mock_auth.return_value = MagicMock()

        user = UserService.get_by_spotify_id("user123")
        user.encrypted_refresh_token = "enc_token"
        db.session.commit()

        mock_sched_svc.create_schedule.return_value = (
            self._make_schedule_mock()
        )

        with patch(
            "shuffify.scheduler.add_job_for_schedule",
            side_effect=RuntimeError("Scheduler not init"),
        ):
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

    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_update_succeeds_despite_apscheduler_error(
        self,
        mock_auth,
        mock_sched_svc,
        auth_client,
    ):
        """Update route catches non-RuntimeError from APScheduler."""
        mock_auth.return_value = MagicMock()

        mock_schedule = self._make_schedule_mock()
        mock_sched_svc.update_schedule.return_value = (
            mock_schedule
        )

        with patch(
            "shuffify.scheduler.add_job_for_schedule",
            side_effect=TypeError("bad trigger"),
        ):
            resp = auth_client.put(
                "/schedules/1",
                json={"schedule_value": "weekly"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True

    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_toggle_succeeds_despite_apscheduler_error(
        self,
        mock_auth,
        mock_sched_svc,
        auth_client,
    ):
        """Toggle route catches non-RuntimeError from APScheduler."""
        mock_auth.return_value = MagicMock()

        mock_schedule = self._make_schedule_mock()
        mock_sched_svc.toggle_schedule.return_value = (
            mock_schedule
        )

        with patch(
            "shuffify.scheduler.add_job_for_schedule",
            side_effect=KeyError("ConflictingId"),
        ):
            resp = auth_client.post(
                "/schedules/1/toggle"
            )
            assert resp.status_code == 200

    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_apscheduler_failure_logs_warning(
        self,
        mock_auth,
        mock_sched_svc,
        auth_client,
    ):
        """APScheduler failure should log a warning."""
        mock_auth.return_value = MagicMock()

        user = UserService.get_by_spotify_id("user123")
        user.encrypted_refresh_token = "enc_token"
        db.session.commit()

        mock_sched_svc.create_schedule.return_value = (
            self._make_schedule_mock()
        )

        with patch(
            "shuffify.scheduler.add_job_for_schedule",
            side_effect=KeyError("ConflictingId"),
        ), patch(
            "shuffify.routes.schedules.logger"
        ) as mock_logger:
            auth_client.post(
                "/schedules/create",
                json={
                    "job_type": "shuffle",
                    "target_playlist_id": "p1",
                    "target_playlist_name": "My Playlist",
                    "algorithm_name": "BasicShuffle",
                },
            )
            mock_logger.warning.assert_called()


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


class TestWorkshopLinkage:
    """Tests for Workshop data loading in GET /schedules."""

    @patch(
        "shuffify.routes.schedules.PlaylistPairService"
    )
    @patch(
        "shuffify.routes.schedules.UpstreamSourceService"
    )
    @patch("shuffify.routes.schedules.ShuffleService")
    @patch("shuffify.routes.schedules.PlaylistService")
    @patch("shuffify.routes.schedules.get_db_user")
    @patch("shuffify.routes.schedules.AuthService")
    @patch("shuffify.routes.schedules.is_authenticated")
    def test_loads_upstream_sources_map(
        self,
        mock_is_auth,
        mock_auth_service,
        mock_get_db_user,
        mock_ps_class,
        mock_shuffle_svc,
        mock_upstream_svc,
        mock_pair_svc,
        auth_client,
    ):
        """Route loads upstream sources grouped by target."""
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
        mock_db_user.spotify_id = "user123"
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

            # Two sources for same target
            src1 = MagicMock()
            src1.target_playlist_id = "p1"
            src1.to_dict.return_value = {
                "id": 1,
                "source_playlist_id": "ext1",
            }
            src2 = MagicMock()
            src2.target_playlist_id = "p1"
            src2.to_dict.return_value = {
                "id": 2,
                "source_playlist_id": "ext2",
            }
            mock_upstream_svc.list_all_sources_for_user.return_value = [
                src1,
                src2,
            ]
            mock_pair_svc.get_pairs_for_user.return_value = (
                []
            )

            resp = auth_client.get("/schedules")
            assert resp.status_code == 200
            mock_upstream_svc.list_all_sources_for_user.assert_called_once_with(
                "user123"
            )

    @patch(
        "shuffify.routes.schedules.PlaylistPairService"
    )
    @patch(
        "shuffify.routes.schedules.UpstreamSourceService"
    )
    @patch("shuffify.routes.schedules.ShuffleService")
    @patch("shuffify.routes.schedules.PlaylistService")
    @patch("shuffify.routes.schedules.get_db_user")
    @patch("shuffify.routes.schedules.AuthService")
    @patch("shuffify.routes.schedules.is_authenticated")
    def test_loads_pairs_by_playlist(
        self,
        mock_is_auth,
        mock_auth_service,
        mock_get_db_user,
        mock_ps_class,
        mock_shuffle_svc,
        mock_upstream_svc,
        mock_pair_svc,
        auth_client,
    ):
        """Route loads playlist pairs keyed by production ID."""
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
        mock_db_user.spotify_id = "user123"
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
            mock_upstream_svc.list_all_sources_for_user.return_value = (
                []
            )

            pair = MagicMock()
            pair.production_playlist_id = "p1"
            pair.to_dict.return_value = {
                "archive_playlist_name": "Archive",
            }
            mock_pair_svc.get_pairs_for_user.return_value = [
                pair
            ]

            resp = auth_client.get("/schedules")
            assert resp.status_code == 200
            mock_pair_svc.get_pairs_for_user.assert_called_once_with(
                1
            )

    @patch(
        "shuffify.routes.schedules.PlaylistPairService"
    )
    @patch(
        "shuffify.routes.schedules.UpstreamSourceService"
    )
    @patch("shuffify.routes.schedules.ShuffleService")
    @patch("shuffify.routes.schedules.PlaylistService")
    @patch("shuffify.routes.schedules.get_db_user")
    @patch("shuffify.routes.schedules.AuthService")
    @patch("shuffify.routes.schedules.is_authenticated")
    def test_sources_grouped_by_target(
        self,
        mock_is_auth,
        mock_auth_service,
        mock_get_db_user,
        mock_ps_class,
        mock_shuffle_svc,
        mock_upstream_svc,
        mock_pair_svc,
        auth_client,
    ):
        """Sources from different targets are grouped correctly."""
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
        mock_db_user.spotify_id = "user123"
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

            # Sources for two different targets
            src_a = MagicMock()
            src_a.target_playlist_id = "p1"
            src_a.to_dict.return_value = {"id": 1}
            src_b = MagicMock()
            src_b.target_playlist_id = "p2"
            src_b.to_dict.return_value = {"id": 2}
            mock_upstream_svc.list_all_sources_for_user.return_value = [
                src_a,
                src_b,
            ]
            mock_pair_svc.get_pairs_for_user.return_value = (
                []
            )

            resp = auth_client.get("/schedules")
            assert resp.status_code == 200

    @patch(
        "shuffify.routes.schedules.PlaylistPairService"
    )
    @patch(
        "shuffify.routes.schedules.UpstreamSourceService"
    )
    @patch("shuffify.routes.schedules.ShuffleService")
    @patch("shuffify.routes.schedules.PlaylistService")
    @patch("shuffify.routes.schedules.get_db_user")
    @patch("shuffify.routes.schedules.AuthService")
    @patch("shuffify.routes.schedules.is_authenticated")
    def test_empty_sources_and_pairs_handled(
        self,
        mock_is_auth,
        mock_auth_service,
        mock_get_db_user,
        mock_ps_class,
        mock_shuffle_svc,
        mock_upstream_svc,
        mock_pair_svc,
        auth_client,
    ):
        """Route works when user has no sources or pairs."""
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
        mock_db_user.spotify_id = "user123"
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
            mock_upstream_svc.list_all_sources_for_user.return_value = (
                []
            )
            mock_pair_svc.get_pairs_for_user.return_value = (
                []
            )

            resp = auth_client.get("/schedules")
            assert resp.status_code == 200


class TestBackendValidation:
    """Tests for defense-in-depth validation in create_schedule."""

    @patch(
        "shuffify.routes.schedules.UpstreamSourceService"
    )
    @patch("shuffify.routes.require_auth")
    def test_raid_with_invalid_sources_returns_400(
        self,
        mock_auth,
        mock_upstream_svc,
        auth_client,
    ):
        """Raid with source IDs not in Workshop is rejected."""
        mock_auth.return_value = MagicMock()

        user = UserService.get_by_spotify_id("user123")
        user.encrypted_refresh_token = "enc_token"
        db.session.commit()

        # No sources configured in Workshop
        mock_upstream_svc.list_sources.return_value = []

        resp = auth_client.post(
            "/schedules/create",
            json={
                "job_type": "raid",
                "target_playlist_id": "p1",
                "target_playlist_name": "My Playlist",
                "source_playlist_ids": ["unknown_id"],
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "not configured" in data["message"]

    @patch(
        "shuffify.routes.schedules.UpstreamSourceService"
    )
    @patch("shuffify.scheduler.add_job_for_schedule")
    @patch(
        "shuffify.routes.schedules.SchedulerService"
    )
    @patch("shuffify.routes.require_auth")
    def test_raid_with_valid_sources_passes(
        self,
        mock_auth,
        mock_sched_svc,
        mock_add_job,
        mock_upstream_svc,
        auth_client,
    ):
        """Raid with valid Workshop sources passes validation."""
        mock_auth.return_value = MagicMock()

        user = UserService.get_by_spotify_id("user123")
        user.encrypted_refresh_token = "enc_token"
        db.session.commit()

        src = MagicMock()
        src.source_playlist_id = "ext1"
        mock_upstream_svc.list_sources.return_value = [src]

        mock_schedule = MagicMock()
        mock_schedule.id = 1
        mock_schedule.job_type = "raid"
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
                "job_type": "raid",
                "target_playlist_id": "p1",
                "target_playlist_name": "My Playlist",
                "source_playlist_ids": ["ext1"],
            },
        )
        assert resp.status_code == 200

    @patch(
        "shuffify.routes.schedules.PlaylistPairService"
    )
    @patch("shuffify.routes.require_auth")
    def test_rotate_without_pair_returns_400(
        self,
        mock_auth,
        mock_pair_svc,
        auth_client,
    ):
        """Rotate without archive pair is rejected."""
        mock_auth.return_value = MagicMock()

        user = UserService.get_by_spotify_id("user123")
        user.encrypted_refresh_token = "enc_token"
        db.session.commit()

        mock_pair_svc.get_pair_for_playlist.return_value = (
            None
        )

        resp = auth_client.post(
            "/schedules/create",
            json={
                "job_type": "rotate",
                "target_playlist_id": "p1",
                "target_playlist_name": "My Playlist",
                "algorithm_params": {
                    "rotation_mode": "archive_oldest",
                    "rotation_count": 5,
                },
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "archive pair" in data["message"]


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
