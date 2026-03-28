"""
Tests for LoginHistoryService.

Tests cover login recording, logout recording, recent logins
retrieval, and login statistics computation.
"""

import pytest
from unittest.mock import Mock

from shuffify.models.db import db, LoginHistory
from shuffify.services.user_service import UserService
from shuffify.services.login_history_service import (
    LoginHistoryService,
)


@pytest.fixture
def app_ctx(db_app):
    """Provide app context with a test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield result.user


@pytest.fixture
def mock_request():
    """Create a mock Flask request object."""
    req = Mock()
    req.remote_addr = "192.168.1.100"
    headers_data = {
        "X-Forwarded-For": "",
        "User-Agent": "Mozilla/5.0 TestBrowser",
    }
    req.headers = Mock()
    req.headers.get = lambda key, default="": (
        headers_data.get(key, default)
    )
    return req


@pytest.fixture
def mock_request_with_proxy():
    """Create a mock request with X-Forwarded-For header."""
    req = Mock()
    req.remote_addr = "10.0.0.1"
    headers_data = {
        "X-Forwarded-For": "203.0.113.50, 70.41.3.18",
        "User-Agent": "Chrome/100",
    }
    req.headers = Mock()
    req.headers.get = lambda key, default="": (
        headers_data.get(key, default)
    )
    return req


class TestRecordLogin:
    """Tests for record_login."""

    def test_record_login_basic(
        self, app_ctx, mock_request
    ):
        """Should create a login history record."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            session_id="sess_abc",
            login_type="oauth_initial",
        )

        assert entry.id is not None
        assert entry.user_id == app_ctx.id
        assert entry.ip_address == "192.168.1.100"
        assert entry.user_agent == "Mozilla/5.0 TestBrowser"
        assert entry.session_id == "sess_abc"
        assert entry.login_type == "oauth_initial"
        assert entry.logged_in_at is not None
        assert entry.logged_out_at is None

    def test_record_login_uses_forwarded_ip(
        self, app_ctx, mock_request_with_proxy
    ):
        """Should prefer X-Forwarded-For over remote_addr."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request_with_proxy,
            login_type="oauth_initial",
        )

        assert entry.ip_address == "203.0.113.50"

    def test_record_login_falls_back_to_remote_addr(
        self, app_ctx, mock_request
    ):
        """Should use remote_addr when X-Forwarded-For is empty."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )

        assert entry.ip_address == "192.168.1.100"

    def test_record_login_truncates_long_user_agent(
        self, app_ctx
    ):
        """Should truncate user agent strings longer than 512."""
        req = Mock()
        req.remote_addr = "1.2.3.4"
        long_ua = "A" * 600
        req.headers = Mock()
        req.headers.get = lambda key, default="": {
            "X-Forwarded-For": "",
            "User-Agent": long_ua,
        }.get(key, default)

        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=req,
            login_type="oauth_initial",
        )

        assert len(entry.user_agent) == 512

    def test_record_login_no_session_id(
        self, app_ctx, mock_request
    ):
        """Should allow None session_id."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )

        assert entry.session_id is None

    def test_record_login_different_types(
        self, app_ctx, mock_request
    ):
        """Should record different login types."""
        for login_type in [
            "oauth_initial",
            "oauth_refresh",
            "session_resume",
        ]:
            entry = LoginHistoryService.record_login(
                user_id=app_ctx.id,
                request=mock_request,
                login_type=login_type,
            )
            assert entry.login_type == login_type


class TestRecordLogout:
    """Tests for record_logout."""

    def test_record_logout_updates_most_recent(
        self, app_ctx, mock_request
    ):
        """Should set logged_out_at on the most recent open record."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            session_id="sess_1",
            login_type="oauth_initial",
        )

        result = LoginHistoryService.record_logout(
            user_id=app_ctx.id,
            session_id="sess_1",
        )

        assert result is True

        # Refresh from DB
        updated = db.session.get(LoginHistory, entry.id)
        assert updated.logged_out_at is not None

    def test_record_logout_no_matching_record(
        self, app_ctx
    ):
        """Should return False when no open record exists."""
        result = LoginHistoryService.record_logout(
            user_id=app_ctx.id,
            session_id="nonexistent",
        )

        assert result is False

    def test_record_logout_without_session_id(
        self, app_ctx, mock_request
    ):
        """Should match any open record when session_id is None."""
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )

        result = LoginHistoryService.record_logout(
            user_id=app_ctx.id,
        )

        assert result is True

    def test_record_logout_only_updates_open_record(
        self, app_ctx, mock_request
    ):
        """Should not re-update an already logged-out record."""
        # First login and logout
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            session_id="sess_1",
            login_type="oauth_initial",
        )
        LoginHistoryService.record_logout(
            user_id=app_ctx.id,
            session_id="sess_1",
        )

        # Try to logout again -- no open record
        result = LoginHistoryService.record_logout(
            user_id=app_ctx.id,
            session_id="sess_1",
        )
        assert result is False

    def test_record_logout_correct_session(
        self, app_ctx, mock_request
    ):
        """Should only logout the matching session."""
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            session_id="sess_1",
            login_type="oauth_initial",
        )
        entry2 = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            session_id="sess_2",
            login_type="oauth_initial",
        )

        LoginHistoryService.record_logout(
            user_id=app_ctx.id,
            session_id="sess_1",
        )

        # sess_2 should still be open
        updated2 = db.session.get(LoginHistory, entry2.id)
        assert updated2.logged_out_at is None
