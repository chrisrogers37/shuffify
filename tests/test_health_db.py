"""
Tests for health check endpoint.
"""

from unittest.mock import patch


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_200(self, client):
        """Health endpoint should return 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_healthy_when_db_available(self, client):
        """Health should return 'healthy' when DB is up."""
        response = client.get("/health")
        data = response.get_json()
        assert data["status"] == "healthy"

    def test_health_returns_degraded_when_db_unavailable(self, client):
        """Health should return 'degraded' when DB is down."""
        with patch(
            "shuffify.is_db_available",
            return_value=False,
        ):
            response = client.get("/health")
            data = response.get_json()
            assert data["status"] == "degraded"

    def test_health_does_not_expose_subsystem_details(self, client):
        """Health response must NOT include a 'checks' key."""
        response = client.get("/health")
        data = response.get_json()
        assert "checks" not in data

    def test_health_does_not_expose_subsystem_on_failure(self, client):
        """Health response must NOT expose details even when degraded."""
        with patch(
            "shuffify.is_db_available",
            return_value=False,
        ):
            response = client.get("/health")
            data = response.get_json()
            assert "checks" not in data
            assert "database" not in str(data)

    def test_health_includes_timestamp(self, client):
        """Health response should include ISO timestamp."""
        response = client.get("/health")
        data = response.get_json()
        assert "timestamp" in data
        assert "T" in data["timestamp"]

    def test_health_response_has_expected_keys(self, client):
        """Health response should contain status, timestamp, and scheduler."""
        response = client.get("/health")
        data = response.get_json()
        assert set(data.keys()) == {
            "status",
            "timestamp",
            "scheduler",
        }

    def test_health_scheduler_metrics_shape(self, client):
        """Scheduler metrics should include expected fields."""
        response = client.get("/health")
        data = response.get_json()
        sched = data["scheduler"]
        assert "scheduler_running" in sched
        assert "jobs_executed" in sched
        assert "jobs_failed" in sched
        assert "jobs_missed" in sched
        assert "last_execution_at" in sched
        assert isinstance(sched["scheduler_running"], bool)
