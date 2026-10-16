"""
Tests for schedule routes.

Tests cover exact schedule deletion, manual execution, history and the management page.
"""

from unittest.mock import MagicMock, patch

from shuffify.models.db import Schedule, User, db
from tests.paths import api_path
from tests.queued_operations import post_and_run
from tests.routes.live_review_helpers import serving
from tests.routes.test_live_raid_outcomes import MutableSpotify, uri


class TestSchedulesAuth:
    """Auth guard tests for all schedule endpoints."""

    def test_get_routines_unauthenticated_returns_json(self, db_app):
        """Routines context uses the same signed-out JSON contract as other reads."""
        with db_app.test_client() as client:
            resp = client.get("/api/routines")
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_delete_unauth_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.delete(api_path("/schedules/1"))
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_run_unauth_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.post(api_path("/schedules/1/run"))
            assert resp.status_code == 401

    @patch("shuffify.routes.require_auth")
    def test_history_unauth_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get(api_path("/schedules/1/history"))
            assert resp.status_code == 401


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

        resp = auth_client.delete(api_path("/schedules/1"))
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

        resp = auth_client.delete(api_path("/schedules/99"))
        assert resp.status_code == 404


class TestRunScheduleNow:
    """An accepted saved routine executes through the explicit worker driver."""

    @staticmethod
    def routine():
        user = User.query.filter_by(spotify_id="user123").one()
        row = Schedule(user_id=user.id, job_type="shuffle", target_playlist_id="target",
                       target_playlist_name="Target", algorithm_name="BasicShuffle", algorithm_params={})
        db.session.add(row)
        db.session.commit()
        return row

    def test_run_success(self, auth_client):
        row, api = self.routine(), MutableSpotify()
        api.playlists["target"] = [uri(number) for number in range(1, 5)]
        with serving(api):
            resp = post_and_run(auth_client, api, api_path(f"/schedules/{row.id}/run"))
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert resp.accepted.status_code == 202
        assert resp.operation["state"] == "succeeded"
        assert data["success"] is True
        assert data["result"]["execution_id"] == resp.operation["id"]
        assert data["result"]["tracks_total"] == 4
        assert data["operation"]["kind"] == "shuffle"
        assert "operation" not in data["result"]

    def test_run_read_failure_has_no_write_and_unknown_total_without_raw_error(self, auth_client):
        from shuffify.spotify.exceptions import SpotifyAPIError

        row, api = self.routine(), MutableSpotify()

        def unavailable(*args, **kwargs):
            raise SpotifyAPIError("private execution detail")

        api.get_playlist = unavailable
        with serving(api):
            resp = post_and_run(auth_client, api, api_path(f"/schedules/{row.id}/run"))
        data = resp.get_json()
        assert resp.status_code == 200, data
        assert resp.operation["state"] in {"failed", "needs_attention"}
        assert data["success"] is False
        assert data["operation"]["aggregate_status"] == "partial"
        assert data["operation"]["retryable"] is False
        assert data["result"]["execution_id"] == resp.operation["id"]
        assert data["result"]["tracks_added"] == 0
        assert data["result"]["tracks_total"] is None
        assert api.writes == []
        assert "private execution detail" not in resp.get_data(as_text=True)


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

        resp = auth_client.get(api_path("/schedules/1/history"))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["history"]) == 1


class TestRoutinesContext:
    def test_reads_owned_routines_and_playlist_labels_without_editor_dependencies(self, auth_client):
        user_id = User.query.filter_by(spotify_id="user123").one().id
        with patch("shuffify.routes.require_auth", autospec=True, return_value=object()), patch(
            "shuffify.routes.contexts.PlaylistService", autospec=True
        ) as playlists, patch(
            "shuffify.routes.contexts.SchedulerService.get_user_schedules", autospec=True, return_value=[]
        ) as routines, patch(
            "shuffify.services.upstream_source_service.UpstreamSourceService.list_all_sources_for_user", autospec=True
        ) as sources, patch(
            "shuffify.services.playlist_pair_service.PlaylistPairService.get_pairs_for_user", autospec=True
        ) as pairs, patch(
            "shuffify.services.shuffle_service.ShuffleService.list_algorithms", autospec=True
        ) as algorithms:
            playlists.return_value.get_user_playlists.return_value = [{"id": "target", "name": "Target"}]
            response = auth_client.get("/api/routines")
        assert response.status_code == 200
        assert response.json["routines"] == []
        assert response.json["playlists"] == [{"id": "target", "name": "Target"}]
        routines.assert_called_once_with(user_id)
        playlists.return_value.get_user_playlists.assert_called_once_with()
        sources.assert_not_called()
        pairs.assert_not_called()
        algorithms.assert_not_called()

    def test_missing_local_user_returns_bounded_json(self, auth_client):
        with patch("shuffify.routes.require_auth", autospec=True, return_value=object()), patch(
            "shuffify.routes.get_db_user", autospec=True, return_value=None
        ):
            response = auth_client.get("/api/routines")
        assert response.status_code == 401
        assert response.json["success"] is False
