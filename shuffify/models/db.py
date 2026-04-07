"""
SQLAlchemy database models for Shuffify.

Defines the User, UserSettings, WorkshopSession, UpstreamSource,
Schedule, JobExecution, ActivityLog, PlaylistPair, and
PlaylistPreference models for persistent storage.
Supports PostgreSQL (production) and SQLite (development/testing).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List

from flask_sqlalchemy import SQLAlchemy
from shuffify.enums import (
    ScheduleType,
    IntervalValue,
    SnapshotType,
    PendingRaidStatus,
    LockTier,
)

logger = logging.getLogger(__name__)

# The SQLAlchemy instance. Initialized with the Flask app in create_app().
db = SQLAlchemy()


class User(db.Model):
    """
    Spotify user record.

    Created or updated on each OAuth login via the upsert pattern.
    Links to all user-specific data (workshop sessions, upstream sources,
    playlist snapshots).
    Serves as the user dimension table with login tracking and
    extended Spotify profile fields.
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

    # Login tracking
    last_login_at = db.Column(db.DateTime, nullable=True)
    login_count = db.Column(
        db.Integer, nullable=False, default=0
    )

    # Account status
    is_active = db.Column(
        db.Boolean, nullable=False, default=True
    )

    # Extended Spotify profile fields
    country = db.Column(db.String(10), nullable=True)
    spotify_product = db.Column(db.String(50), nullable=True)
    spotify_uri = db.Column(db.String(255), nullable=True)

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
    login_history = db.relationship(
        "LoginHistory",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="LoginHistory.logged_in_at.desc()",
    )
    settings = db.relationship(
        "UserSettings",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    playlist_pairs = db.relationship(
        "PlaylistPair",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    raid_playlist_links = db.relationship(
        "RaidPlaylistLink",
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
            "last_login_at": (
                self.last_login_at.isoformat()
                if self.last_login_at
                else None
            ),
            "login_count": self.login_count,
            "is_active": self.is_active,
            "country": self.country,
            "spotify_product": self.spotify_product,
            "spotify_uri": self.spotify_uri,
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
        return f"<User {self.spotify_id} ({self.display_name})>"


class UserSettings(db.Model):
    """
    User preferences and configuration.

    One-to-one relationship with User. Created automatically on
    first login via UserService.upsert_from_spotify().
    """

    __tablename__ = "user_settings"

    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Shuffle defaults
    default_algorithm = db.Column(
        db.String(64), nullable=True
    )
    default_algorithm_params = db.Column(
        db.JSON, nullable=True
    )

    # UI preferences
    theme = db.Column(
        db.String(10), nullable=False, default="system"
    )

    # Feature toggles
    notifications_enabled = db.Column(
        db.Boolean, nullable=False, default=False
    )
    auto_snapshot_enabled = db.Column(
        db.Boolean, nullable=False, default=True
    )
    max_snapshots_per_playlist = db.Column(
        db.Integer, nullable=False, default=10
    )

    # Dashboard preferences
    dashboard_show_recent_activity = db.Column(
        db.Boolean, nullable=False, default=True
    )

    # Extensible JSON field for future preferences
    extra = db.Column(db.JSON, nullable=True)

    # Timestamps
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
    user = db.relationship("User", back_populates="settings")

    # Valid theme choices (used by service layer for validation)
    VALID_THEMES = {"light", "dark", "system"}

    __table_args__ = (
        db.CheckConstraint(
            "theme IN ('light', 'dark', 'system')",
            name="ck_user_settings_theme",
        ),
        db.CheckConstraint(
            "max_snapshots_per_playlist >= 1 "
            "AND max_snapshots_per_playlist <= 50",
            name="ck_user_settings_max_snapshots",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the UserSettings to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "default_algorithm": self.default_algorithm,
            "default_algorithm_params": (
                self.default_algorithm_params or {}
            ),
            "theme": self.theme,
            "notifications_enabled": self.notifications_enabled,
            "auto_snapshot_enabled": (
                self.auto_snapshot_enabled
            ),
            "max_snapshots_per_playlist": (
                self.max_snapshots_per_playlist
            ),
            "dashboard_show_recent_activity": (
                self.dashboard_show_recent_activity
            ),
            "extra": self.extra or {},
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
            f"<UserSettings user_id={self.user_id} "
            f"theme={self.theme}>"
        )


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
    source_playlist_id = db.Column(db.String(255), nullable=True)
    source_url = db.Column(db.String(1024), nullable=True)
    source_type = db.Column(
        db.String(20), nullable=False, default="external"
    )
    source_name = db.Column(db.String(255), nullable=True)
    search_query = db.Column(db.String(500), nullable=True)
    last_resolved_at = db.Column(db.DateTime, nullable=True)
    last_resolve_pathway = db.Column(db.String(30), nullable=True)
    last_resolve_status = db.Column(
        db.String(20), nullable=True
    )  # "success", "partial", "failed"
    last_track_count = db.Column(db.Integer, nullable=True)
    raid_count = db.Column(
        db.Integer, nullable=False, default=5
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = db.relationship("User", back_populates="upstream_sources")

    __table_args__ = (
        db.Index(
            "ix_upstream_user_target",
            "user_id",
            "target_playlist_id",
        ),
        db.CheckConstraint(
            "source_type IN ('own', 'external', "
            "'search_query')",
            name="ck_upstream_source_type",
        ),
        db.CheckConstraint(
            "raid_count >= 1 AND raid_count <= 100",
            name="ck_upstream_raid_count_range",
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
            "search_query": self.search_query,
            "last_resolved_at": (
                self.last_resolved_at.isoformat()
                if self.last_resolved_at
                else None
            ),
            "last_resolve_pathway": self.last_resolve_pathway,
            "last_resolve_status": self.last_resolve_status,
            "last_track_count": self.last_track_count,
            "raid_count": self.raid_count,
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
        db.String(10), nullable=False,
        default=ScheduleType.INTERVAL,
    )
    schedule_value = db.Column(
        db.String(100), nullable=False,
        default=IntervalValue.DAILY,
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

    __table_args__ = (
        db.Index(
            "ix_schedules_user_target_type",
            "user_id",
            "target_playlist_id",
            "job_type",
        ),
        db.CheckConstraint(
            "job_type IN ('raid', 'shuffle', "
            "'raid_and_shuffle', 'raid_and_drip', "
            "'rotate', 'drip')",
            name="ck_schedules_job_type",
        ),
        db.CheckConstraint(
            "schedule_type IN ('interval', 'cron')",
            name="ck_schedules_schedule_type",
        ),
    )

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


class LoginHistory(db.Model):
    """
    Record of a single user login event.

    Created on each OAuth callback. The logged_out_at field is updated
    when the user explicitly logs out or when the session expires.
    """

    __tablename__ = "login_history"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    logged_in_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    logged_out_at = db.Column(db.DateTime, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    session_id = db.Column(db.String(255), nullable=True)
    login_type = db.Column(db.String(20), nullable=False)

    # Relationships
    user = db.relationship(
        "User", back_populates="login_history"
    )

    __table_args__ = (
        db.CheckConstraint(
            "login_type IN ('oauth_initial', "
            "'oauth_refresh', 'session_resume')",
            name="ck_login_history_type",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the LoginHistory to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "logged_in_at": (
                self.logged_in_at.isoformat()
                if self.logged_in_at
                else None
            ),
            "logged_out_at": (
                self.logged_out_at.isoformat()
                if self.logged_out_at
                else None
            ),
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "session_id": self.session_id,
            "login_type": self.login_type,
        }

    def __repr__(self) -> str:
        return (
            f"<LoginHistory {self.id}: user={self.user_id} "
            f"type={self.login_type} "
            f"at={self.logged_in_at}>"
        )


class PlaylistSnapshot(db.Model):
    """
    Point-in-time snapshot of a playlist's track ordering.

    Captured automatically before mutations (shuffle, raid, commit)
    or manually by the user. Enables restoring a playlist to any
    previous state, even across sessions.
    """

    __tablename__ = "playlist_snapshots"

    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    playlist_id = db.Column(
        db.String(255), nullable=False, index=True
    )
    playlist_name = db.Column(
        db.String(255), nullable=False
    )
    track_uris_json = db.Column(db.Text, nullable=False)
    track_count = db.Column(
        db.Integer, nullable=False, default=0
    )
    snapshot_type = db.Column(
        db.String(30),
        nullable=False,
        default=SnapshotType.MANUAL,
    )
    trigger_description = db.Column(
        db.String(500), nullable=True
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = db.relationship(
        "User",
        backref=db.backref(
            "playlist_snapshots", lazy="dynamic"
        ),
    )

    __table_args__ = (
        db.Index(
            "ix_snapshot_user_playlist_created",
            "user_id",
            "playlist_id",
            "created_at",
        ),
        db.CheckConstraint(
            "snapshot_type IN ("
            "'auto_pre_shuffle', 'auto_pre_raid', "
            "'auto_pre_commit', 'auto_pre_rotate', "
            "'auto_pre_drip', 'manual', "
            "'scheduled_pre_execution')",
            name="ck_snapshot_type",
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
                f"PlaylistSnapshot {self.id}"
            )
            return []

    @track_uris.setter
    def track_uris(self, uris: List[str]) -> None:
        """Serialize a list of URI strings to JSON for storage."""
        self.track_uris_json = json.dumps(uris)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the PlaylistSnapshot to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
            "track_uris": self.track_uris,
            "track_count": self.track_count,
            "snapshot_type": self.snapshot_type,
            "trigger_description": self.trigger_description,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<PlaylistSnapshot {self.id}: "
            f"{self.snapshot_type} "
            f"for playlist {self.playlist_id} "
            f"({self.track_count} tracks)>"
        )


class ActivityLog(db.Model):
    """
    Unified activity log for tracking all user actions.

    Every significant user action (shuffle, workshop commit, schedule
    change, etc.) is recorded here for audit and dashboard display.
    Logging is non-blocking: failures must never prevent the primary
    operation from succeeding.
    """

    __tablename__ = "activity_log"

    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    activity_type = db.Column(
        db.String(50), nullable=False, index=True
    )
    description = db.Column(
        db.String(500), nullable=False
    )
    playlist_id = db.Column(
        db.String(255), nullable=True
    )
    playlist_name = db.Column(
        db.String(255), nullable=True
    )
    metadata_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # Relationships
    user = db.relationship(
        "User",
        backref=db.backref(
            "activities",
            lazy="dynamic",
            cascade="all, delete-orphan",
        ),
    )

    # Composite index for efficient recent activity queries
    __table_args__ = (
        db.Index(
            "ix_activity_user_created",
            "user_id",
            "created_at",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the ActivityLog to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "activity_type": self.activity_type,
            "description": self.description,
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
            "metadata": self.metadata_json,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<ActivityLog {self.id}: "
            f"{self.activity_type} "
            f"by user {self.user_id}>"
        )


class PlaylistPair(db.Model):
    """
    Links a production playlist to an archive playlist.

    When tracks are removed from the production playlist in the workshop,
    they are automatically added to the archive playlist on commit.
    """

    __tablename__ = "playlist_pairs"

    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    production_playlist_id = db.Column(
        db.String(255), nullable=False
    )
    production_playlist_name = db.Column(
        db.String(255), nullable=True
    )
    archive_playlist_id = db.Column(
        db.String(255), nullable=False
    )
    archive_playlist_name = db.Column(
        db.String(255), nullable=True
    )
    auto_archive_on_remove = db.Column(
        db.Boolean, nullable=False, default=True
    )
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
        "User", back_populates="playlist_pairs"
    )

    # Unique constraint: one pair per user per production playlist
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "production_playlist_id",
            name="uq_user_production_playlist",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the PlaylistPair to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "production_playlist_id": (
                self.production_playlist_id
            ),
            "production_playlist_name": (
                self.production_playlist_name
            ),
            "archive_playlist_id": self.archive_playlist_id,
            "archive_playlist_name": (
                self.archive_playlist_name
            ),
            "auto_archive_on_remove": (
                self.auto_archive_on_remove
            ),
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
            f"<PlaylistPair {self.id}: "
            f"'{self.production_playlist_name}' -> "
            f"'{self.archive_playlist_name}'>"
        )


class RaidPlaylistLink(db.Model):
    """
    Links a target playlist to a raid staging playlist.

    Raid sources feed into the raid playlist, and tracks drip from
    the raid playlist into the target. Mirrors the PlaylistPair
    pattern (production/archive) for the upstream direction.
    """

    __tablename__ = "raid_playlist_links"

    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    target_playlist_id = db.Column(
        db.String(255), nullable=False
    )
    target_playlist_name = db.Column(
        db.String(255), nullable=True
    )
    raid_playlist_id = db.Column(
        db.String(255), nullable=False
    )
    raid_playlist_name = db.Column(
        db.String(255), nullable=True
    )
    drip_count = db.Column(
        db.Integer, nullable=False, default=3
    )
    drip_enabled = db.Column(
        db.Boolean, nullable=False, default=False
    )
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
        "User", back_populates="raid_playlist_links"
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "target_playlist_id",
            name="uq_raid_link_user_target",
        ),
        db.CheckConstraint(
            "drip_count >= 1 AND drip_count <= 50",
            name="ck_raid_link_drip_count",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the RaidPlaylistLink to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "target_playlist_id": (
                self.target_playlist_id
            ),
            "target_playlist_name": (
                self.target_playlist_name
            ),
            "raid_playlist_id": self.raid_playlist_id,
            "raid_playlist_name": (
                self.raid_playlist_name
            ),
            "drip_count": self.drip_count,
            "drip_enabled": self.drip_enabled,
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
            f"<RaidPlaylistLink {self.id}: "
            f"'{self.target_playlist_name}' <- "
            f"'{self.raid_playlist_name}'>"
        )


class PlaylistPreference(db.Model):
    """
    Per-user playlist display preferences.

    Controls the ordering, visibility, and pinning of playlists
    on the dashboard. Created on-demand when a user customizes
    their playlist arrangement.
    """

    __tablename__ = "playlist_preferences"

    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    spotify_playlist_id = db.Column(
        db.String(255), nullable=False
    )
    sort_order = db.Column(
        db.Integer, nullable=False, default=0
    )
    is_hidden = db.Column(
        db.Boolean, nullable=False, default=False
    )
    is_pinned = db.Column(
        db.Boolean, nullable=False, default=False
    )
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

    user = db.relationship(
        "User",
        backref=db.backref(
            "playlist_preferences",
            lazy="dynamic",
            cascade="all, delete-orphan",
        ),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "spotify_playlist_id",
            name="uq_user_spotify_playlist",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "spotify_playlist_id": self.spotify_playlist_id,
            "sort_order": self.sort_order,
            "is_hidden": self.is_hidden,
            "is_pinned": self.is_pinned,
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
            f"<PlaylistPreference {self.id}: "
            f"playlist={self.spotify_playlist_id} "
            f"order={self.sort_order} "
            f"{'hidden' if self.is_hidden else 'visible'} "
            f"{'pinned' if self.is_pinned else 'unpinned'}>"
        )


class TrackLock(db.Model):
    """
    Per-track position lock within a playlist.

    Two lock tiers:
    - 'standard': auto-expires after 30 days.
    - 'super': permanent until manually removed.

    Locked tracks are excluded from shuffle reordering,
    rotation swap-outs, and cannot be dragged in the
    Workshop UI.
    """

    __tablename__ = "track_locks"

    STANDARD_EXPIRY_DAYS = 30

    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    spotify_playlist_id = db.Column(
        db.String(255), nullable=False
    )
    track_uri = db.Column(
        db.String(255), nullable=False
    )
    position = db.Column(
        db.Integer, nullable=False
    )
    lock_tier = db.Column(
        db.String(20),
        nullable=False,
        default=LockTier.STANDARD,
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = db.Column(
        db.DateTime, nullable=True
    )

    user = db.relationship(
        "User",
        backref=db.backref(
            "track_locks",
            lazy="dynamic",
            cascade="all, delete-orphan",
        ),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "spotify_playlist_id",
            "track_uri",
            name="uq_track_lock_user_playlist_track",
        ),
        db.Index(
            "ix_track_lock_playlist",
            "user_id",
            "spotify_playlist_id",
        ),
    )

    @property
    def is_expired(self) -> bool:
        """Check if this lock has expired."""
        if self.expires_at is None:
            return False
        now = datetime.now(timezone.utc)
        expires = self.expires_at
        # Handle naive datetimes from SQLite
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now > expires

    @property
    def is_active(self) -> bool:
        """Check if this lock is currently active."""
        return not self.is_expired

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "spotify_playlist_id": self.spotify_playlist_id,
            "track_uri": self.track_uri,
            "position": self.position,
            "lock_tier": self.lock_tier,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at
                else None
            ),
            "expires_at": (
                self.expires_at.isoformat()
                if self.expires_at
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<TrackLock {self.id}: "
            f"playlist={self.spotify_playlist_id} "
            f"pos={self.position} "
            f"tier={self.lock_tier}>"
        )


class PendingRaidTrack(db.Model):
    """
    A track staged by a raid for user review before adding
    to the target playlist.

    Raids write here instead of directly to Spotify. The user
    then promotes (adds to playlist) or dismisses tracks from
    the workshop Track Inbox.
    """

    __tablename__ = "pending_raid_tracks"

    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )
    target_playlist_id = db.Column(
        db.String(255), nullable=False
    )
    track_uri = db.Column(
        db.String(255), nullable=False
    )
    track_name = db.Column(
        db.String(500), nullable=False
    )
    track_artists = db.Column(
        db.String(1000), nullable=True
    )
    track_album = db.Column(
        db.String(500), nullable=True
    )
    track_image_url = db.Column(
        db.String(1024), nullable=True
    )
    track_duration_ms = db.Column(
        db.Integer, nullable=True
    )
    source_playlist_id = db.Column(
        db.String(255), nullable=True
    )
    source_name = db.Column(
        db.String(255), nullable=True
    )
    status = db.Column(
        db.String(20),
        nullable=False,
        default=PendingRaidStatus.PENDING,
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    resolved_at = db.Column(
        db.DateTime, nullable=True
    )

    # Relationships
    user = db.relationship(
        "User",
        backref=db.backref(
            "pending_raid_tracks",
            lazy="dynamic",
            cascade="all, delete-orphan",
        ),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "target_playlist_id",
            "track_uri",
            name="uq_pending_raid_track",
        ),
        db.Index(
            "ix_pending_raid_user_playlist_status",
            "user_id",
            "target_playlist_id",
            "status",
        ),
        db.CheckConstraint(
            "status IN ('pending', 'promoted', "
            "'dismissed')",
            name="ck_pending_raid_status",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "target_playlist_id": self.target_playlist_id,
            "track_uri": self.track_uri,
            "track_name": self.track_name,
            "track_artists": self.track_artists,
            "track_album": self.track_album,
            "track_image_url": self.track_image_url,
            "track_duration_ms": self.track_duration_ms,
            "source_playlist_id": self.source_playlist_id,
            "source_name": self.source_name,
            "status": self.status,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at
                else None
            ),
            "resolved_at": (
                self.resolved_at.isoformat()
                if self.resolved_at
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<PendingRaidTrack {self.id}: "
            f"'{self.track_name}' "
            f"status={self.status}>"
        )


# =============================================================================
# Scraped Playlist Cache
# =============================================================================


class ScrapedPlaylistCache(db.Model):
    """
    Database-backed cache for tracks scraped from public
    Spotify playlist pages.

    Shared across users (no user_id FK) since scrape results
    are playlist-level. Replaces the previous Redis-based cache
    with persistence across app restarts.
    """

    __tablename__ = "scraped_playlist_cache"

    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )
    playlist_id = db.Column(
        db.String(255), nullable=False, index=True
    )
    track_uris_json = db.Column(db.Text, nullable=False)
    track_count = db.Column(
        db.Integer, nullable=False, default=0
    )
    scraped_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    scrape_pathway = db.Column(
        db.String(50), nullable=True
    )
    expires_at = db.Column(
        db.DateTime, nullable=False
    )

    __table_args__ = (
        db.Index(
            "ix_scrape_cache_playlist_expires",
            "playlist_id",
            "expires_at",
        ),
        db.UniqueConstraint(
            "playlist_id",
            name="uq_scrape_cache_playlist_id",
        ),
    )

    @property
    def track_uris(self) -> List[str]:
        """Deserialize the stored JSON into a list of URIs."""
        if not self.track_uris_json:
            return []
        try:
            return json.loads(self.track_uris_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Failed to decode track_uris_json for "
                "ScrapedPlaylistCache %s",
                self.id,
            )
            return []

    @track_uris.setter
    def track_uris(self, uris: List[str]) -> None:
        """Serialize a list of URI strings to JSON."""
        self.track_uris_json = json.dumps(uris)

    def __repr__(self) -> str:
        return (
            f"<ScrapedPlaylistCache "
            f"playlist={self.playlist_id} "
            f"tracks={self.track_count}>"
        )
