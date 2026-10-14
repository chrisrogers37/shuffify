"""
Shuffify Models Package.

Exports the SQLAlchemy database instance and the database models listed in
__all__; import any other model from shuffify.models.db.
Also exports the Playlist dataclass.

Usage:
    from shuffify.models import db, User, WorkshopSession, UpstreamSource
    from shuffify.models import Playlist  # dataclass
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
    RoutineActivationReceipt,
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
    "RoutineActivationReceipt",
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
