"""
Login history service for recording and querying sign-in events.

Handles creation of login records on OAuth callback, updating logout
timestamps, and querying login history for auditing and analytics.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from flask import Request

from shuffify.models.db import db, LoginHistory
from shuffify.services.base import safe_commit

logger = logging.getLogger(__name__)


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
        IP address, user agent, and session ID.

        Args:
            user_id: The internal database user ID.
            request: The Flask request object (used for IP and UA).
            session_id: The Flask session ID for correlation.
            login_type: One of 'oauth_initial', 'oauth_refresh',
                'session_resume'.

        Returns:
            The created LoginHistory instance.

        Raises:
            LoginHistoryError: If recording fails.
        """
        # Extract IP address, preferring X-Forwarded-For for proxied
        # requests
        ip_address = (
            request.headers.get(
                "X-Forwarded-For", ""
            ).split(",")[0].strip()
            or request.remote_addr
        )

        # Extract and truncate user agent to fit column length
        user_agent = request.headers.get("User-Agent", "")
        if len(user_agent) > 512:
            user_agent = user_agent[:512]

        entry = LoginHistory(
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            session_id=session_id,
            login_type=login_type,
        )
        db.session.add(entry)
        safe_commit(
            f"record login for user_id={user_id}, "
            f"type={login_type}, ip={ip_address}",
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
                login record.

        Returns:
            True if a record was updated, False if no matching record
            was found.

        Raises:
            LoginHistoryError: If the update fails.
        """
        try:
            query = LoginHistory.query.filter_by(
                user_id=user_id
            ).filter(
                LoginHistory.logged_out_at.is_(None)
            )

            if session_id:
                query = query.filter_by(session_id=session_id)

            # Get the most recent open login record
            entry = query.order_by(
                LoginHistory.logged_in_at.desc()
            ).first()

            if not entry:
                logger.debug(
                    f"No open login record found for "
                    f"user_id={user_id} to mark as logged out"
                )
                return False

            entry.logged_out_at = datetime.now(timezone.utc)
            db.session.commit()

            logger.info(
                f"Recorded logout for user_id={user_id}, "
                f"login_history_id={entry.id}"
            )
            return True

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to record logout for user_id="
                f"{user_id}: {e}",
                exc_info=True,
            )
            raise LoginHistoryError(
                f"Failed to record logout: {e}"
            )

    @staticmethod
    def get_recent_logins(
        user_id: int, limit: int = 10
    ) -> List[LoginHistory]:
        """
        Get the most recent login events for a user.

        Args:
            user_id: The internal database user ID.
            limit: Maximum number of records to return (default 10).

        Returns:
            List of LoginHistory instances, most recent first.
        """
        return (
            LoginHistory.query.filter_by(user_id=user_id)
            .order_by(LoginHistory.logged_in_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_login_stats(user_id: int) -> Dict[str, Any]:
        """
        Get summary statistics for a user's login history.

        Returns total logins, average session duration (for completed
        sessions), the most recent login timestamp, and a count of
        logins by type.

        Args:
            user_id: The internal database user ID.

        Returns:
            Dictionary with keys:
                - total_logins (int)
                - avg_session_duration_seconds (float or None)
                - last_login_at (str ISO format or None)
                - logins_by_type (dict of type -> count)
        """
        all_entries = LoginHistory.query.filter_by(
            user_id=user_id
        ).all()

        if not all_entries:
            return {
                "total_logins": 0,
                "avg_session_duration_seconds": None,
                "last_login_at": None,
                "logins_by_type": {},
            }

        total_logins = len(all_entries)

        # Calculate average session duration for completed sessions
        completed = [
            e for e in all_entries if e.logged_out_at is not None
        ]
        avg_duration = None
        if completed:
            durations = [
                (
                    e.logged_out_at - e.logged_in_at
                ).total_seconds()
                for e in completed
            ]
            avg_duration = sum(durations) / len(durations)

        # Most recent login
        most_recent = max(
            all_entries, key=lambda e: e.logged_in_at
        )

        # Count by login type
        logins_by_type: Dict[str, int] = {}
        for e in all_entries:
            logins_by_type[e.login_type] = (
                logins_by_type.get(e.login_type, 0) + 1
            )

        return {
            "total_logins": total_logins,
            "avg_session_duration_seconds": avg_duration,
            "last_login_at": (
                most_recent.logged_in_at.isoformat()
                if most_recent.logged_in_at
                else None
            ),
            "logins_by_type": logins_by_type,
        }
