"""
Orchestration service for the Smart Raid Panel.

Composes UpstreamSourceService and SchedulerService into unified
raid source and schedule management operations.
"""

from contextlib import ExitStack, contextmanager

from shuffify.enums import JobType, ScheduleType
from shuffify.models.db import Schedule, User, db
from shuffify.services.playlist_lock import (
    PlaylistBusyError,
    playlist_configuration_lock,
    playlist_lock,
)


class RaidSyncError(Exception):
    """Error in raid sync operations."""
    pass


@contextmanager
def _source_schedule_mutation(target_playlist_id, *, edits_schedule=False):
    """Keep source and schedule changes in one target-serialized transaction.

    A watch that can edit a schedule's source IDs needs execution then
    configuration. Source-only watches use configuration without blocking a
    running Collect. Never acquire execution inside configuration.
    """
    with ExitStack() as locks:
        if edits_schedule and not locks.enter_context(playlist_lock(target_playlist_id, timeout_s=2.0)):
            raise PlaylistBusyError("Playlist is busy; please retry.")
        if not locks.enter_context(playlist_configuration_lock(target_playlist_id, timeout_s=2.0)):
            raise PlaylistBusyError("Playlist is busy; please retry.")
        try:
            yield
        except Exception:
            db.session.rollback()
            raise


class RaidSyncService:
    """Stateless orchestration for raid panel operations."""

    @staticmethod
    def watch_playlist(
        spotify_id,
        target_playlist_id,
        target_playlist_name,
        source_playlist_id,
        source_playlist_name=None,
        source_url=None,
        auto_schedule=True,
        schedule_value="daily",
        schedule_time=None,
        source_type="own",
        user=None,
    ):
        """
        One-click watch: register source + optionally
        create/update raid schedule.

        Returns dict with 'source' and 'schedule' keys.
        """
        from shuffify.services.scheduler_service import (
            SchedulerService,
        )
        from shuffify.services.upstream_source_service import (
            UpstreamSourceService,
        )

        if user is None:
            user = User.query.filter_by(
                spotify_id=spotify_id
            ).first()
            if not user:
                raise RaidSyncError("User not found")

        with _source_schedule_mutation(target_playlist_id, edits_schedule=auto_schedule):
            # 1. Register source (idempotent)
            source = UpstreamSourceService.add_source(
                spotify_id=spotify_id,
                target_playlist_id=target_playlist_id,
                source_playlist_id=source_playlist_id,
                source_type=source_type,
                source_url=source_url,
                source_name=source_playlist_name,
                commit=False,
            )

            # 2. Optionally create/update raid schedule
            schedule = None
            schedule_written = False
            if auto_schedule:
                schedule = RaidSyncService._find_raid_schedule(
                    user.id, target_playlist_id
                )
                if schedule:
                    # Add source to existing schedule if not present
                    current_ids = schedule.source_playlist_ids or []
                    if source_playlist_id not in current_ids:
                        updated_ids = current_ids + [
                            source_playlist_id
                        ]
                        SchedulerService.update_schedule(
                            schedule.id,
                            user.id,
                            source_playlist_ids=updated_ids,
                        )
                        schedule_written = True
                        db.session.refresh(schedule)
                else:
                    sched_type, sched_val = (
                        RaidSyncService._resolve_schedule_params(
                            schedule_value, schedule_time
                        )
                    )
                    schedule = SchedulerService.create_schedule(
                        user_id=user.id,
                        job_type=JobType.RAID,
                        target_playlist_id=target_playlist_id,
                        target_playlist_name=(
                            target_playlist_name or target_playlist_id
                        ),
                        schedule_type=sched_type,
                        schedule_value=sched_val,
                        source_playlist_ids=[source_playlist_id],
                    )
                    schedule_written = True
                    # APScheduler registration handled in create_schedule

            if not schedule_written:
                db.session.commit()

            return {
                "source": source.to_dict(),
                "schedule": (
                    schedule.to_dict() if schedule else None
                ),
            }

    @staticmethod
    def unwatch_playlist(
        spotify_id, source_id, target_playlist_id,
        user=None,
    ):
        """
        Remove a source and update/delete the raid schedule.
        """
        from shuffify.services.scheduler_service import (
            SchedulerService,
        )
        from shuffify.services.upstream_source_service import (
            UpstreamSourceService,
        )

        with _source_schedule_mutation(target_playlist_id, edits_schedule=True):
            # Get source before deleting (need playlist_id). Scoped to the target
            # playlist as well as the owner: unwatching through a different
            # playlist's route must not reach this source.
            source = UpstreamSourceService.get_source(
                source_id, spotify_id, target_playlist_id
            )
            source_playlist_id = source.source_playlist_id

            # Delete the upstream source
            UpstreamSourceService.delete_source(
                source_id, spotify_id, target_playlist_id, commit=False
            )

            if user is None:
                user = User.query.filter_by(
                    spotify_id=spotify_id
                ).first()
                if not user:
                    db.session.commit()
                    return True

            # Update or delete the raid schedule
            schedule = RaidSyncService._find_raid_schedule(
                user.id, target_playlist_id
            )
            if schedule:
                current_ids = schedule.source_playlist_ids or []
                remaining = [
                    sid for sid in current_ids
                    if sid != source_playlist_id
                ]
                if not remaining:
                    # delete_schedule removes the APScheduler job after the DB
                    # delete commits.
                    SchedulerService.delete_schedule(
                        schedule.id, user.id
                    )
                else:
                    SchedulerService.update_schedule(
                        schedule.id,
                        user.id,
                        source_playlist_ids=remaining,
                    )
            else:
                db.session.commit()

            return True

    @staticmethod
    def get_raid_status(
        spotify_id, target_playlist_id, user=None,
    ):
        """
        Get raid panel summary for a target playlist.
        """
        from shuffify.services.raid_link_service import (
            RaidLinkService,
        )
        from shuffify.services.upstream_source_service import (
            UpstreamSourceService,
        )

        max_sources = (
            UpstreamSourceService.MAX_SOURCES_PER_TARGET
        )

        if user is None:
            user = User.query.filter_by(
                spotify_id=spotify_id
            ).first()
        if not user:
            return {
                "sources": [],
                "schedule": None,
                "raid_link": None,
                "drip_schedule": None,
                "source_count": 0,
                "max_sources": max_sources,
                "has_schedule": False,
                "is_schedule_enabled": False,
                "last_run_at": None,
                "last_status": None,
            }

        sources = UpstreamSourceService.list_sources(
            spotify_id, target_playlist_id
        )
        schedule = RaidSyncService._find_raid_schedule(
            user.id, target_playlist_id
        )
        raid_link = RaidLinkService.get_link_for_playlist(
            user.id, target_playlist_id
        )
        drip_schedule = RaidSyncService._find_drip_schedule(
            user.id, target_playlist_id
        )

        return {
            "sources": [s.to_dict() for s in sources],
            "schedule": (
                schedule.to_dict() if schedule else None
            ),
            "raid_link": (
                raid_link.to_dict()
                if raid_link else None
            ),
            "drip_schedule": (
                drip_schedule.to_dict()
                if drip_schedule else None
            ),
            "source_count": len(sources),
            "max_sources": max_sources,
            "has_schedule": schedule is not None,
            "is_schedule_enabled": (
                schedule.is_enabled if schedule else False
            ),
            "last_run_at": (
                schedule.last_run_at.isoformat()
                if schedule and schedule.last_run_at
                else None
            ),
            "last_status": (
                schedule.last_status if schedule else None
            ),
        }

    @staticmethod
    def _find_raid_schedule(user_id, target_playlist_id):
        """Find a raid schedule for user + target playlist."""
        return Schedule.query.filter(
            Schedule.user_id == user_id,
            Schedule.target_playlist_id == target_playlist_id,
            Schedule.job_type.in_([
                JobType.RAID,
                JobType.RAID_AND_SHUFFLE,
                JobType.RAID_AND_DRIP,
            ]),
        ).first()

    @staticmethod
    def _find_drip_schedule(user_id, target_playlist_id):
        """Find a drip schedule for user + target playlist."""
        return Schedule.query.filter(
            Schedule.user_id == user_id,
            Schedule.target_playlist_id
            == target_playlist_id,
            Schedule.job_type == JobType.DRIP,
        ).first()

    @staticmethod
    def _resolve_schedule_params(
        schedule_value, schedule_time=None
    ):
        """Convert frequency + optional time into
        schedule_type and schedule_value.

        Returns (schedule_type, schedule_value) tuple.
        """
        from shuffify.services.schedule_utils import (
            TIME_CAPABLE_FREQUENCIES,
            build_cron,
        )

        if (
            schedule_time
            and schedule_value in TIME_CAPABLE_FREQUENCIES
        ):
            cron_expr = build_cron(
                schedule_value, schedule_time
            )
            return ScheduleType.CRON, cron_expr
        return ScheduleType.INTERVAL, schedule_value
