"""
Shuffify Models Package.

Exports the SQLAlchemy database instance and all database models.
Also exports the existing Playlist dataclass for backward compatibility.

Usage:
    from shuffify.models import db, User, WorkshopSession, UpstreamSource
    from shuffify.models import Playlist  # existing dataclass
"""

from shuffify.models.db import (
    db,
    User,
    UserSettings,
    WorkshopSession,
    UpstreamSource,
    Schedule,
    JobExecution,
    LoginHistory,
    PlaylistSnapshot,
    ActivityLog,
)
from shuffify.models.playlist import Playlist

__all__ = [
    "db",
    "User",
    "UserSettings",
    "WorkshopSession",
    "UpstreamSource",
    "Schedule",
    "JobExecution",
    "LoginHistory",
    "PlaylistSnapshot",
    "ActivityLog",
    "Playlist",
]
