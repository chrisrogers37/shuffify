"""Timestamps are timezone-aware UTC instants on every backend (SR-033).

These assert the round trip, not the column declaration. Asserting
``timezone=True`` on the columns would have passed against SQLite while the
values still came back naive -- that gap is the defect this closes.
"""

from datetime import datetime, timedelta, timezone

import pytest

from shuffify.models.db import (
    ActivityLog,
    JobExecution,
    LoginHistory,
    Schedule,
    TrackLock,
    User,
    UTCDateTime,
    db,
)

EASTERN = timezone(timedelta(hours=-5))


@pytest.fixture
def user(db_app):
    with db_app.app_context():
        u = User(spotify_id="tz_user", display_name="TZ")
        db.session.add(u)
        db.session.commit()
        yield u


class TestRoundTrip:
    """A written instant comes back aware, in UTC, and unshifted."""

    def test_aware_value_survives_a_round_trip(self, db_app, user):
        with db_app.app_context():
            written = datetime.now(timezone.utc) + timedelta(hours=1)
            lock = TrackLock(
                user_id=user.id,
                spotify_playlist_id="p1",
                track_uri="spotify:track:1",
                position=0,
                expires_at=written,
            )
            db.session.add(lock)
            db.session.commit()
            db.session.expire_all()

            read = db.session.get(TrackLock, lock.id).expires_at

            assert read.tzinfo is not None
            assert read.utcoffset() == timedelta(0)
            assert read == written

    def test_read_value_compares_against_an_aware_now(self, db_app, user):
        """The concrete failure the naive columns produced.

        A naive column made this raise
        ``TypeError: can't compare offset-naive and offset-aware datetimes``,
        which is why call sites had to re-tag before every comparison.
        """
        with db_app.app_context():
            lock = TrackLock(
                user_id=user.id,
                spotify_playlist_id="p1",
                track_uri="spotify:track:1",
                position=0,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            db.session.add(lock)
            db.session.commit()
            db.session.expire_all()

            read = db.session.get(TrackLock, lock.id)
            assert read.expires_at > datetime.now(timezone.utc)
            assert read.is_expired is False

    def test_non_utc_input_is_normalized_not_truncated(self, db_app, user):
        """An aware value in another zone converts; it does not lose its offset.

        Truncating the tzinfo instead of converting would shift the instant by
        the offset -- five hours, silently, for a US/Eastern value.
        """
        with db_app.app_context():
            written = datetime(2026, 6, 1, 12, 0, 0, tzinfo=EASTERN)
            lock = TrackLock(
                user_id=user.id,
                spotify_playlist_id="p1",
                track_uri="spotify:track:1",
                position=0,
                expires_at=written,
            )
            db.session.add(lock)
            db.session.commit()
            db.session.expire_all()

            read = db.session.get(TrackLock, lock.id).expires_at

            assert read == written  # same instant
            assert read.utcoffset() == timedelta(0)  # expressed in UTC
            assert read.hour == 17  # 12:00-05:00 is 17:00Z

    def test_server_defaults_are_aware(self, db_app, user):
        """Columns filled by the model default, not by the caller."""
        with db_app.app_context():
            entry = ActivityLog(
                user_id=user.id,
                activity_type="login",
                description="in",
            )
            db.session.add(entry)
            db.session.commit()
            db.session.expire_all()

            created = db.session.get(ActivityLog, entry.id).created_at
            assert created.tzinfo is not None
            assert created <= datetime.now(timezone.utc)

    def test_nullable_column_stays_none(self, db_app, user):
        with db_app.app_context():
            execution = JobExecution(schedule_id=None, status="running")
            db.session.add(execution)
            db.session.commit()
            db.session.expire_all()

            stored = db.session.get(JobExecution, execution.id)
            assert stored.completed_at is None
            assert stored.started_at.tzinfo is not None


class TestSchemaWideCoverage:
    """No timestamp column may be left naive.

    A per-column list would go stale the first time someone adds a table, so
    this walks the mapper registry instead.
    """

    def test_every_timestamp_column_is_utc_aware(self, db_app):
        with db_app.app_context():
            naive = []
            for mapper in db.Model.registry.mappers:
                for column in mapper.local_table.columns:
                    if isinstance(column.type, UTCDateTime):
                        continue
                    type_name = str(column.type).upper()
                    if "DATETIME" in type_name or "TIMESTAMP" in type_name:
                        naive.append(f"{mapper.local_table.name}.{column.name}")

            assert naive == []

    def test_registry_actually_has_timestamp_columns(self, db_app):
        """Guard the check above against passing vacuously."""
        with db_app.app_context():
            aware = [
                f"{m.local_table.name}.{c.name}"
                for m in db.Model.registry.mappers
                for c in m.local_table.columns
                if isinstance(c.type, UTCDateTime)
            ]
            assert len(aware) >= 25


class TestOrderingUnaffected:
    """Normalizing on write must not disturb ordering or comparison in SQL."""

    def test_desc_ordering_still_correct(self, db_app, user):
        with db_app.app_context():
            base = datetime.now(timezone.utc)
            for offset in (0, 5, 10):
                db.session.add(
                    LoginHistory(
                        user_id=user.id,
                        login_type="oauth_initial",
                        logged_in_at=base + timedelta(minutes=offset),
                    )
                )
            db.session.commit()

            rows = (
                db.session.query(LoginHistory)
                .order_by(LoginHistory.logged_in_at.desc())
                .all()
            )
            times = [r.logged_in_at for r in rows]
            assert times == sorted(times, reverse=True)

    def test_filtering_by_an_aware_bound_works(self, db_app, user):
        with db_app.app_context():
            base = datetime.now(timezone.utc)
            db.session.add(
                Schedule(
                    user_id=user.id,
                    target_playlist_id="p1",
                    target_playlist_name="P1",
                    job_type="shuffle",
                    schedule_type="interval",
                    schedule_value="daily",
                    last_run_at=base - timedelta(days=2),
                )
            )
            db.session.add(
                Schedule(
                    user_id=user.id,
                    target_playlist_id="p2",
                    target_playlist_name="P2",
                    job_type="shuffle",
                    schedule_type="interval",
                    schedule_value="daily",
                    last_run_at=base,
                )
            )
            db.session.commit()

            recent = (
                db.session.query(Schedule)
                .filter(Schedule.last_run_at > base - timedelta(days=1))
                .all()
            )
            assert [s.target_playlist_id for s in recent] == ["p2"]
