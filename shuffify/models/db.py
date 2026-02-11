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
