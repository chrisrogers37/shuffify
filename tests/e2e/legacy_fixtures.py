"""Local fixtures for persisted configurations that current setup refuses to create."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from config import HarnessConfig
from shuffify.models.db import Schedule, UpstreamSource, User


def seed_unavailable_legacy_source(client, target, schedule_id, source_id, *, legacy_ids):
    """Give an owned paused routine a previously saved source that is now unavailable."""
    HarnessConfig.assert_safe()
    actor = client.ok("get", "/session")["user"]["id"]
    engine = create_engine(HarnessConfig.SQLALCHEMY_DATABASE_URI, poolclass=NullPool)
    try:
        with Session(engine) as storage, storage.begin():
            user = storage.scalar(select(User).where(User.spotify_id == actor))
            assert user is not None and actor == "alice"
            routine = storage.get(Schedule, schedule_id)
            assert routine is not None and routine.user_id == user.id
            assert routine.target_playlist_id == target and not routine.is_enabled
            storage.add(UpstreamSource(
                user_id=user.id, target_playlist_id=target, source_playlist_id=source_id,
                source_name="Unavailable source", source_type="external",
            ))
            routine.source_playlist_ids = list(legacy_ids)
    finally:
        engine.dispose()
