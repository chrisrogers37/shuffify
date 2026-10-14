"""
Tests for the validate_json() helper in shuffify.routes.
"""

import pytest
from pydantic import BaseModel, field_validator


class SampleSchema(BaseModel):
    """A simple Pydantic model for testing."""

    name: str
    count: int = 0

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


@pytest.fixture
def app():
    """Create a minimal Flask app for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_client_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_client_secret"
    os.environ["SPOTIFY_REDIRECT_URI"] = (
        "http://localhost:5000/callback"
    )
    os.environ["SECRET_KEY"] = "test-secret-key"
    os.environ.pop("DATABASE_URL", None)
    from shuffify import create_app

    app = create_app("development")
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///:memory:"
    )
    app.config["SCHEDULER_ENABLED"] = False
    return app


class TestValidateJson:
    """Tests for the validate_json() helper."""

    def test_valid_input_returns_parsed_model(self, app):
        """Valid JSON body returns (model, None)."""
        from shuffify.routes import validate_json

        with app.test_request_context(
            "/test",
            method="POST",
            json={"name": "hello", "count": 5},
        ):
            result, err = validate_json(SampleSchema)
            assert err is None
            assert result is not None
            assert result.name == "hello"
            assert result.count == 5

    def test_missing_json_body_returns_400(self, app):
        """No JSON body returns (None, 400 response)."""
        from shuffify.routes import validate_json

        with app.test_request_context(
            "/test",
            method="POST",
            content_type="text/plain",
            data="not json",
        ):
            result, err = validate_json(SampleSchema)
            assert result is None
            assert err is not None
            response, status_code = err
            assert status_code == 400
            data = response.get_json()
            assert data["success"] is False
            assert "Request body must be JSON" in data[
                "message"
            ]

    def test_validation_error_returns_400(self, app):
        """Invalid data returns (None, 400) with message."""
        from shuffify.routes import validate_json

        # Missing required 'name' field
        with app.test_request_context(
            "/test",
            method="POST",
            json={"count": 5},
        ):
            result, err = validate_json(SampleSchema)
            assert result is None
            assert err is not None
            response, status_code = err
            assert status_code == 400
            data = response.get_json()
            assert data["success"] is False
            assert "Validation error:" in data["message"]

    def test_validation_error_with_custom_validator(
        self, app
    ):
        """Custom field validator error message is
        included."""
        from shuffify.routes import validate_json

        with app.test_request_context(
            "/test",
            method="POST",
            json={"name": "   ", "count": 1},
        ):
            result, err = validate_json(SampleSchema)
            assert result is None
            assert err is not None
            response, status_code = err
            assert status_code == 400
            data = response.get_json()
            assert "Validation error:" in data["message"]

    def test_extra_fields_ignored(self, app):
        """Extra fields in input are silently ignored
        (Pydantic default)."""
        from shuffify.routes import validate_json

        with app.test_request_context(
            "/test",
            method="POST",
            json={
                "name": "hello",
                "count": 3,
                "extra_field": "ignored",
            },
        ):
            result, err = validate_json(SampleSchema)
            assert err is None
            assert result is not None
            assert result.name == "hello"
            assert not hasattr(result, "extra_field")

    def test_defaults_applied(self, app):
        """Optional fields use defaults when omitted."""
        from shuffify.routes import validate_json

        with app.test_request_context(
            "/test",
            method="POST",
            json={"name": "hello"},
        ):
            result, err = validate_json(SampleSchema)
            assert err is None
            assert result.count == 0

    def test_empty_json_body_returns_400(self, app):
        """Empty request body (no Content-Type) returns
        400."""
        from shuffify.routes import validate_json

        with app.test_request_context(
            "/test",
            method="POST",
        ):
            result, err = validate_json(SampleSchema)
            assert result is None
            assert err is not None
            _, status_code = err
            assert status_code == 400
