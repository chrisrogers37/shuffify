"""
Tests for WorkshopSessionService.

Tests cover save, list, get, update, delete, and limit enforcement.
"""

import pytest

from shuffify.models.db import db
from shuffify.services.user_service import UserService
from shuffify.services.workshop_session_service import (
    WorkshopSessionService,
    WorkshopSessionError,
    WorkshopSessionNotFoundError,
    WorkshopSessionLimitError,
    MAX_SESSIONS_PER_PLAYLIST,
)


@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def app_ctx(db_app):
    """Provide app context with a test user."""
    with db_app.app_context():
        UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield


class TestWorkshopSessionServiceSave:
    """Tests for save_session."""

    def test_save_session(self, app_ctx):
        """Should save a new workshop session."""
        uris = ["spotify:track:a", "spotify:track:b"]
        ws = WorkshopSessionService.save_session(
            "user123", "playlist1", "My Save", uris
        )

        assert ws.id is not None
        assert ws.session_name == "My Save"
        assert ws.track_uris == uris
        assert ws.playlist_id == "playlist1"

    def test_save_session_strips_name(self, app_ctx):
        """Should strip whitespace from session name."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "  Padded Name  ", ["uri"]
        )
        assert ws.session_name == "Padded Name"

    def test_save_session_empty_name_raises(self, app_ctx):
        """Should reject empty session name."""
        with pytest.raises(
            WorkshopSessionError, match="cannot be empty"
        ):
            WorkshopSessionService.save_session(
                "user123", "p1", "", ["uri"]
            )

    def test_save_session_whitespace_name_raises(
        self, app_ctx
    ):
        """Should reject whitespace-only session name."""
        with pytest.raises(
            WorkshopSessionError, match="cannot be empty"
        ):
            WorkshopSessionService.save_session(
                "user123", "p1", "   ", ["uri"]
            )

    def test_save_session_unknown_user_raises(self, app_ctx):
        """Should raise error when user not found."""
        with pytest.raises(
            WorkshopSessionError, match="User not found"
        ):
            WorkshopSessionService.save_session(
                "nonexistent", "p1", "Test", ["uri"]
            )

    def test_save_session_limit_enforcement(self, app_ctx):
        """Should reject saves beyond the per-playlist limit."""
        for i in range(MAX_SESSIONS_PER_PLAYLIST):
            WorkshopSessionService.save_session(
                "user123", "p1", f"Session {i}", [f"uri{i}"]
            )

        with pytest.raises(
            WorkshopSessionLimitError, match="Maximum"
        ):
            WorkshopSessionService.save_session(
                "user123", "p1", "One Too Many", ["uri"]
            )

    def test_save_session_limit_is_per_playlist(
        self, app_ctx
    ):
        """Limit should be per-playlist, not global."""
        for i in range(MAX_SESSIONS_PER_PLAYLIST):
            WorkshopSessionService.save_session(
                "user123",
                "playlist_A",
                f"Session {i}",
                [f"uri{i}"],
            )

        # Should succeed on a different playlist
        ws = WorkshopSessionService.save_session(
            "user123", "playlist_B", "Session on B", ["uri"]
        )
        assert ws.id is not None

    def test_save_session_empty_uris(self, app_ctx):
        """Should allow saving with an empty track list."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Empty Session", []
        )
        assert ws.track_uris == []


class TestWorkshopSessionServiceList:
    """Tests for list_sessions."""

    def test_list_sessions_returns_sessions(self, app_ctx):
        """Should return all sessions for user and playlist."""
        WorkshopSessionService.save_session(
            "user123", "p1", "A", ["uri_a"]
        )
        WorkshopSessionService.save_session(
            "user123", "p1", "B", ["uri_b"]
        )

        sessions = WorkshopSessionService.list_sessions(
            "user123", "p1"
        )
        assert len(sessions) == 2

    def test_list_sessions_filters_by_playlist(
        self, app_ctx
    ):
        """Should only return sessions for the playlist."""
        WorkshopSessionService.save_session(
            "user123", "p1", "A", ["a"]
        )
        WorkshopSessionService.save_session(
            "user123", "p2", "B", ["b"]
        )

        sessions = WorkshopSessionService.list_sessions(
            "user123", "p1"
        )
        assert len(sessions) == 1
        assert sessions[0].session_name == "A"

    def test_list_sessions_unknown_user_returns_empty(
        self, app_ctx
    ):
        """Should return empty list for unknown user."""
        sessions = WorkshopSessionService.list_sessions(
            "ghost", "p1"
        )
        assert sessions == []

    def test_list_sessions_ordered_by_most_recent(
        self, app_ctx
    ):
        """Should return most recent first."""
        WorkshopSessionService.save_session(
            "user123", "p1", "First", ["a"]
        )
        WorkshopSessionService.save_session(
            "user123", "p1", "Second", ["b"]
        )

        sessions = WorkshopSessionService.list_sessions(
            "user123", "p1"
        )
        # Most recent first
        assert sessions[0].session_name == "Second"


class TestWorkshopSessionServiceGet:
    """Tests for get_session."""

    def test_get_session_success(self, app_ctx):
        """Should return the session by ID."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Test", ["uri"]
        )

        result = WorkshopSessionService.get_session(
            ws.id, "user123"
        )
        assert result.session_name == "Test"

    def test_get_session_wrong_user_raises(self, app_ctx):
        """Should raise when session belongs to another user."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Test", ["uri"]
        )

        # Create another user
        UserService.upsert_from_spotify({
            "id": "other_user",
            "display_name": "Other",
            "images": [],
        })

        with pytest.raises(WorkshopSessionNotFoundError):
            WorkshopSessionService.get_session(
                ws.id, "other_user"
            )

    def test_get_session_nonexistent_id_raises(
        self, app_ctx
    ):
        """Should raise for non-existent session ID."""
        with pytest.raises(WorkshopSessionNotFoundError):
            WorkshopSessionService.get_session(
                99999, "user123"
            )


class TestWorkshopSessionServiceUpdate:
    """Tests for update_session."""

    def test_update_track_uris(self, app_ctx):
        """Should update the track URIs."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Original", ["old_uri"]
        )

        updated = WorkshopSessionService.update_session(
            ws.id, "user123", ["new_uri_a", "new_uri_b"]
        )
        assert updated.track_uris == [
            "new_uri_a",
            "new_uri_b",
        ]

    def test_update_session_name(self, app_ctx):
        """Should update the session name."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Old Name", ["uri"]
        )

        updated = WorkshopSessionService.update_session(
            ws.id,
            "user123",
            ["uri"],
            session_name="New Name",
        )
        assert updated.session_name == "New Name"


class TestWorkshopSessionServiceDelete:
    """Tests for delete_session."""

    def test_delete_session(self, app_ctx):
        """Should delete the session."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "To Delete", ["uri"]
        )
        ws_id = ws.id

        result = WorkshopSessionService.delete_session(
            ws_id, "user123"
        )
        assert result is True

        with pytest.raises(WorkshopSessionNotFoundError):
            WorkshopSessionService.get_session(
                ws_id, "user123"
            )

    def test_delete_session_wrong_user_raises(
        self, app_ctx
    ):
        """Should raise when session belongs to another user."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Test", ["uri"]
        )

        UserService.upsert_from_spotify({
            "id": "other_user",
            "display_name": "Other",
            "images": [],
        })

        with pytest.raises(WorkshopSessionNotFoundError):
            WorkshopSessionService.delete_session(
                ws.id, "other_user"
            )
