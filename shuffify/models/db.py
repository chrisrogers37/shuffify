"""
SQLAlchemy database models for Shuffify.

Defines the User, WorkshopSession, and UpstreamSource models
for persistent storage in SQLite.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List

from flask_sqlalchemy import SQLAlchemy

logger = logging.getLogger(__name__)

# The SQLAlchemy instance. Initialized with the Flask app in create_app().
db = SQLAlchemy()


class User(db.Model):
    """
    Spotify user record.

    Created or updated on each OAuth login via the upsert pattern.
    Links to all user-specific data (workshop sessions, upstream sources).
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    spotify_id = db.Column(
        db.String(255), unique=True, nullable=False, index=True
    )
    display_name = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    profile_image_url = db.Column(db.String(1024), nullable=True)
    # Encrypted Spotify refresh token for background job execution.
    # Encrypted with Fernet using a key derived from SECRET_KEY.
    # Set during OAuth callback, updated on token refresh.
    encrypted_refresh_token = db.Column(db.Text, nullable=True)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    workshop_sessions = db.relationship(
        "WorkshopSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    upstream_sources = db.relationship(
        "UpstreamSource",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the User to a dictionary."""
        return {
            "id": self.id,
            "spotify_id": self.spotify_id,
            "display_name": self.display_name,
            "email": self.email,
            "profile_image_url": self.profile_image_url,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "updated_at": (
                self.updated_at.isoformat() if self.updated_at else None
            ),
        }

    def __repr__(self) -> str:
        return f"<User {self.spotify_id} ({self.display_name})>"


class WorkshopSession(db.Model):
    """
    Saved workshop state for a specific playlist.

    Stores the track URI ordering so users can save their workshop
    arrangement and resume later, even after the Flask session expires.
    """

    __tablename__ = "workshop_sessions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    playlist_id = db.Column(db.String(255), nullable=False, index=True)
    session_name = db.Column(db.String(255), nullable=False)
    track_uris_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = db.relationship("User", back_populates="workshop_sessions")

    # Composite index for efficient lookup
    __table_args__ = (
        db.Index(
            "ix_workshop_user_playlist", "user_id", "playlist_id"
        ),
    )

    @property
    def track_uris(self) -> List[str]:
        """Deserialize the stored JSON into a list of URI strings."""
        if not self.track_uris_json:
            return []
        try:
            return json.loads(self.track_uris_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                f"Failed to decode track_uris_json for "
                f"WorkshopSession {self.id}"
            )
            return []

    @track_uris.setter
    def track_uris(self, uris: List[str]) -> None:
        """Serialize a list of URI strings to JSON for storage."""
        self.track_uris_json = json.dumps(uris)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the WorkshopSession to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "playlist_id": self.playlist_id,
            "session_name": self.session_name,
            "track_uris": self.track_uris,
            "track_count": len(self.track_uris),
            "created_at": (
                self.created_at.isoformat()
                if self.created_at
                else None
            ),
            "updated_at": (
                self.updated_at.isoformat()
                if self.updated_at
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<WorkshopSession {self.id}: '{self.session_name}' "
            f"for playlist {self.playlist_id}>"
        )


class UpstreamSource(db.Model):
    """
    Persistent record of an external playlist source configuration.

    Links a source playlist to a target playlist for a specific user.
    """

    __tablename__ = "upstream_sources"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    target_playlist_id = db.Column(
        db.String(255), nullable=False, index=True
    )
    source_playlist_id = db.Column(db.String(255), nullable=False)
    source_url = db.Column(db.String(1024), nullable=True)
    source_type = db.Column(
        db.String(20), nullable=False, default="external"
    )
    source_name = db.Column(db.String(255), nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = db.relationship("User", back_populates="upstream_sources")

    # Composite index
    __table_args__ = (
        db.Index(
            "ix_upstream_user_target",
            "user_id",
            "target_playlist_id",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the UpstreamSource to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "target_playlist_id": self.target_playlist_id,
            "source_playlist_id": self.source_playlist_id,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<UpstreamSource {self.id}: {self.source_type} "
            f"'{self.source_playlist_id}' -> "
            f"'{self.target_playlist_id}'>"
        )


class Schedule(db.Model):
    """
    A configured scheduled operation for a user.

    Each schedule defines a recurring job that runs automatically:
    - raid: Pull new tracks from upstream sources into a target
    - shuffle: Run a shuffle algorithm on a target playlist
    - raid_and_shuffle: Pull new tracks then shuffle
    """

    __tablename__ = "schedules"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    job_type = db.Column(
        db.String(20), nullable=False
    )
    target_playlist_id = db.Column(
        db.String(64), nullable=False
    )
    target_playlist_name = db.Column(
        db.String(255), nullable=True
    )
    source_playlist_ids = db.Column(
        db.JSON, nullable=True, default=list
    )
    algorithm_name = db.Column(
        db.String(64), nullable=True
    )
    algorithm_params = db.Column(
        db.JSON, nullable=True, default=dict
    )
    schedule_type = db.Column(
        db.String(10), nullable=False, default="interval"
    )
    schedule_value = db.Column(
        db.String(100), nullable=False, default="daily"
    )
    is_enabled = db.Column(
        db.Boolean, nullable=False, default=True
    )
    last_run_at = db.Column(db.DateTime, nullable=True)
    last_status = db.Column(db.String(20), nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = db.relationship(
        "User",
        backref=db.backref("schedules", lazy="dynamic"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the Schedule to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "job_type": self.job_type,
            "target_playlist_id": self.target_playlist_id,
            "target_playlist_name": self.target_playlist_name,
            "source_playlist_ids": self.source_playlist_ids or [],
            "algorithm_name": self.algorithm_name,
            "algorithm_params": self.algorithm_params or {},
            "schedule_type": self.schedule_type,
            "schedule_value": self.schedule_value,
            "is_enabled": self.is_enabled,
            "last_run_at": (
                self.last_run_at.isoformat()
                if self.last_run_at
                else None
            ),
            "last_status": self.last_status,
            "last_error": self.last_error,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at
                else None
            ),
            "updated_at": (
                self.updated_at.isoformat()
                if self.updated_at
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<Schedule {self.id}: {self.job_type} on "
            f"{self.target_playlist_name} "
            f"({self.schedule_value}, "
            f"{'enabled' if self.is_enabled else 'disabled'})>"
        )


class JobExecution(db.Model):
    """
    Record of a single job execution for audit/history.
    """

    __tablename__ = "job_executions"

    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(
        db.Integer,
        db.ForeignKey("schedules.id"),
        nullable=False,
        index=True,
    )
    started_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(
        db.String(20), nullable=False, default="running"
    )
    tracks_added = db.Column(
        db.Integer, nullable=True, default=0
    )
    tracks_total = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    # Relationships
    schedule = db.relationship(
        "Schedule",
        backref=db.backref("executions", lazy="dynamic"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the JobExecution to a dictionary."""
        return {
            "id": self.id,
            "schedule_id": self.schedule_id,
            "started_at": (
                self.started_at.isoformat()
                if self.started_at
                else None
            ),
            "completed_at": (
                self.completed_at.isoformat()
                if self.completed_at
                else None
            ),
            "status": self.status,
            "tracks_added": self.tracks_added,
            "tracks_total": self.tracks_total,
            "error_message": self.error_message,
        }

    def __repr__(self) -> str:
        return (
            f"<JobExecution {self.id}: "
            f"schedule={self.schedule_id} "
            f"status={self.status}>"
        )
