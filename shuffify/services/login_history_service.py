"""
Login history service for recording and querying sign-in events.

Handles creation of login records on OAuth callback, updating logout
timestamps, and querying login history for auditing and analytics.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional

from flask import Request

from shuffify.models.db import LoginHistory, db
from shuffify.services import signed_digest
from shuffify.services.base import safe_commit

logger = logging.getLogger(__name__)

# Login history stores a keyed digest of each session id, never the id itself:
# the id is the session cookie's value. The digest still matches a sign-out to
# its sign-in.
SESSION_DIGEST_PURPOSE = "shuffify.login.session.v1"


def _session_digest(session_id: Optional[str]) -> Optional[str]:
    """HMAC-SHA256 of a session id under a key derived from SECRET_KEY."""
    if not session_id:
        return None
    key = signed_digest.key(SESSION_DIGEST_PURPOSE)
    return hmac.new(key, session_id.encode("utf-8"), hashlib.sha256).hexdigest()


class LoginHistoryError(Exception):
    """Base exception for login history operations."""

    pass


class LoginHistoryNotFoundError(LoginHistoryError):
    """Raised when a login history record cannot be found."""

    pass


class LoginHistoryService:
    """Service for managing login history records."""

    @staticmethod
    def record_login(
        user_id: int,
        request: Request,
        session_id: Optional[str] = None,
        login_type: str = "oauth_initial",
    ) -> LoginHistory:
        """
        Record a new login event.

        Creates a LoginHistory record capturing the login timestamp,
        IP address, user agent, and a keyed digest of the session ID.

        Args:
            user_id: The internal database user ID.
            request: The Flask request object (used for IP and UA).
            session_id: The Flask session ID. Its keyed digest is stored,
                so record_logout can match this sign-in.
            login_type: One of 'oauth_initial', 'oauth_refresh',
                'session_resume'.

        Returns:
            The created LoginHistory instance.

        Raises:
            LoginHistoryError: If recording fails.
        """
        ip_address = request.remote_addr

        # Extract and truncate user agent to fit column length
        user_agent = request.headers.get("User-Agent", "")
        if len(user_agent) > 512:
            user_agent = user_agent[:512]

        entry = LoginHistory(
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            session_id=_session_digest(session_id),
            login_type=login_type,
        )
        db.session.add(entry)
        safe_commit(
            f"record login for user_id={user_id}, type={login_type}, ip={ip_address}",
            LoginHistoryError,
        )
        return entry

    @staticmethod
    def record_logout(
        user_id: int,
        session_id: Optional[str] = None,
    ) -> bool:
        """
        Update the most recent login record with a logout timestamp.

        Finds the most recent LoginHistory record for the user
        (optionally filtered by session_id) that has no logged_out_at
        value, and sets it to the current UTC time.

        Args:
            user_id: The internal database user ID.
            session_id: Optional Flask session ID to match a specific
                login record, by its stored digest.

        Returns:
            True if a record was updated, False if no matching record
            was found.

        Raises:
            LoginHistoryError: If the update fails.
        """
        query = LoginHistory.query.filter_by(user_id=user_id).filter(
            LoginHistory.logged_out_at.is_(None)
        )

        if session_id:
            query = query.filter_by(session_id=_session_digest(session_id))

        # Get the most recent open login record
        entry = query.order_by(LoginHistory.logged_in_at.desc()).first()

        if not entry:
            logger.debug(
                f"No open login record found for "
                f"user_id={user_id} to mark as logged out"
            )
            return False

        entry.logged_out_at = datetime.now(timezone.utc)
        safe_commit(
            f"record logout for user_id={user_id}",
            LoginHistoryError,
        )
        return True
