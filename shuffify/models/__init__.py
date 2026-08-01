"""
Shuffify Models Package.

Exports the SQLAlchemy database instance and all database models.
Also exports the existing Playlist dataclass for backward compatibility.

Usage:
    from shuffify.models import db, User, WorkshopSession, UpstreamSource
    from shuffify.models import Playlist  # existing dataclass
"""

from shuffify.models.db import (
    ActivityLog,
    JobExecution,
    LoginHistory,
    PendingRaidTrack,
    PlaylistPair,
    PlaylistPreference,
    PlaylistSnapshot,
    RaidPlaylistLink,
    Schedule,
    ScrapedPlaylistCache,
    UpstreamSource,
    User,
    UserSettings,
    WorkshopSession,
    db,
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
    "PlaylistPair",
    "RaidPlaylistLink",
    "PlaylistPreference",
    "PendingRaidTrack",
    "ScrapedPlaylistCache",
    "Playlist",
]
