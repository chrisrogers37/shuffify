"""
Tests for enhanced health check endpoint with database status.
"""

from unittest.mock import patch


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_200(self, client):
        """Health endpoint should return 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_includes_database_check(self, client):
        """Health response should include database check."""
        response = client.get("/health")
        data = response.get_json()
        assert "checks" in data
        assert "database" in data["checks"]

    def test_health_database_ok_when_available(self, client):
        """Database check should be 'ok' when DB is available."""
        response = client.get("/health")
        data = response.get_json()
        assert data["status"] == "healthy"
        assert data["checks"]["database"] == "ok"

    def test_health_database_unavailable(self, client):
        """Database check should report 'unavailable' on failure."""
        with patch(
            "shuffify.is_db_available",
            return_value=False,
        ):
            response = client.get("/health")
            data = response.get_json()
            assert data["status"] == "degraded"
            assert data["checks"]["database"] == "unavailable"

    def test_health_includes_timestamp(self, client):
        """Health response should include ISO timestamp."""
        response = client.get("/health")
        data = response.get_json()
        assert "timestamp" in data
        assert "T" in data["timestamp"]
