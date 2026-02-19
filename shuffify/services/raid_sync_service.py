"""
Orchestration service for the Smart Raid Panel.

Composes UpstreamSourceService, SchedulerService, and
JobExecutorService into unified raid management operations.
"""

import logging

from shuffify.models.db import db, Schedule, User
from shuffify.enums import JobType, ScheduleType

logger = logging.getLogger(__name__)


class RaidSyncError(Exception):
    """Error in raid sync operations."""
    pass


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
    ):
        """
        One-click watch: register source + optionally
        create/update raid schedule.

        Returns dict with 'source' and 'schedule' keys.
        """
        from shuffify.services.upstream_source_service import (
            UpstreamSourceService,
        )
        from shuffify.services.scheduler_service import (
            SchedulerService,
        )

        user = User.query.filter_by(
            spotify_id=spotify_id
        ).first()
        if not user:
            raise RaidSyncError("User not found")

        # 1. Register source (idempotent)
        source = UpstreamSourceService.add_source(
            spotify_id=spotify_id,
            target_playlist_id=target_playlist_id,
            source_playlist_id=source_playlist_id,
            source_type="own",
            source_url=source_url,
            source_name=source_playlist_name,
        )

        # 2. Optionally create/update raid schedule
        schedule = None
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
                    db.session.refresh(schedule)
            else:
                schedule = SchedulerService.create_schedule(
                    user_id=user.id,
                    job_type=JobType.RAID,
                    target_playlist_id=target_playlist_id,
                    target_playlist_name=(
                        target_playlist_name or target_playlist_id
                    ),
                    schedule_type=ScheduleType.INTERVAL,
                    schedule_value=schedule_value,
                    source_playlist_ids=[source_playlist_id],
                )
                # Register with APScheduler
                try:
                    from shuffify.scheduler import (
                        add_job_for_schedule,
                    )
                    from flask import current_app
                    add_job_for_schedule(
                        schedule, current_app._get_current_object()
                    )
                except Exception as e:
                    logger.warning(
                        "Could not register schedule with "
                        "APScheduler: %s", e
                    )

        return {
            "source": source.to_dict(),
            "schedule": (
                schedule.to_dict() if schedule else None
            ),
        }

    @staticmethod
    def unwatch_playlist(
        spotify_id, source_id, target_playlist_id
    ):
        """
        Remove a source and update/delete the raid schedule.
        """
        from shuffify.services.upstream_source_service import (
            UpstreamSourceService,
        )
        from shuffify.services.scheduler_service import (
            SchedulerService,
        )

        # Get source before deleting (need playlist_id)
        source = UpstreamSourceService.get_source(
            source_id, spotify_id
        )
        source_playlist_id = source.source_playlist_id

        # Delete the upstream source
        UpstreamSourceService.delete_source(
            source_id, spotify_id
        )

        user = User.query.filter_by(
            spotify_id=spotify_id
        ).first()
        if not user:
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
                # Delete schedule and remove from APScheduler
                try:
                    from shuffify.scheduler import (
                        remove_job_for_schedule,
                    )
                    remove_job_for_schedule(schedule.id)
                except Exception as e:
                    logger.warning(
                        "Could not remove job from APScheduler: "
                        "%s", e
                    )
                SchedulerService.delete_schedule(
                    schedule.id, user.id
                )
            else:
                SchedulerService.update_schedule(
                    schedule.id,
                    user.id,
                    source_playlist_ids=remaining,
                )

        return True

    @staticmethod
    def get_raid_status(spotify_id, target_playlist_id):
        """
        Get raid panel summary for a target playlist.
        """
        from shuffify.services.upstream_source_service import (
            UpstreamSourceService,
        )

        user = User.query.filter_by(
            spotify_id=spotify_id
        ).first()
        if not user:
            return {
                "sources": [],
                "schedule": None,
                "source_count": 0,
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

        return {
            "sources": [s.to_dict() for s in sources],
            "schedule": (
                schedule.to_dict() if schedule else None
            ),
            "source_count": len(sources),
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
    def raid_now(
        spotify_id, target_playlist_id,
        source_playlist_ids=None,
    ):
        """
        Trigger an immediate one-off raid.

        If source_playlist_ids is None, uses all configured
        sources.
        """
        from shuffify.services.upstream_source_service import (
            UpstreamSourceService,
        )

        user = User.query.filter_by(
            spotify_id=spotify_id
        ).first()
        if not user:
            raise RaidSyncError("User not found")

        if source_playlist_ids is None:
            sources = UpstreamSourceService.list_sources(
                spotify_id, target_playlist_id
            )
            source_playlist_ids = [
                s.source_playlist_id for s in sources
            ]

        if not source_playlist_ids:
            raise RaidSyncError(
                "No sources configured. "
                "Watch a playlist first."
            )

        schedule = RaidSyncService._find_raid_schedule(
            user.id, target_playlist_id
        )

        if schedule:
            return RaidSyncService._execute_raid_via_scheduler(
                schedule, user
            )
        else:
            return RaidSyncService._execute_raid_inline(
                user, target_playlist_id,
                source_playlist_ids,
            )

    @staticmethod
    def _execute_raid_via_scheduler(schedule, user):
        """Execute raid through existing schedule's job
        executor."""
        from shuffify.services.job_executor_service import (
            JobExecutorService,
            JobExecutionError,
        )

        try:
            result = JobExecutorService.execute_now(
                schedule.id, user.id
            )
            db.session.refresh(schedule)
            return {
                "tracks_added": 0,
                "tracks_total": 0,
                "status": result.get("status", "success"),
            }
        except JobExecutionError as e:
            raise RaidSyncError(str(e))

    @staticmethod
    def _execute_raid_inline(
        user, target_playlist_id, source_playlist_ids
    ):
        """Execute raid without a schedule (inline)."""
        from shuffify.services.job_executor_service import (
            JobExecutorService,
        )

        try:
            api = JobExecutorService._get_spotify_api(user)
            target_tracks = api.get_playlist_tracks(
                target_playlist_id
            )
            target_uris = {
                t.get("uri")
                for t in target_tracks
                if t.get("uri")
            }

            new_uris = JobExecutorService._fetch_raid_sources(
                api, source_playlist_ids, target_uris
            )

            if new_uris:
                JobExecutorService._batch_add_tracks(
                    api, target_playlist_id, new_uris
                )

            return {
                "tracks_added": len(new_uris),
                "tracks_total": (
                    len(target_tracks) + len(new_uris)
                ),
                "status": "success",
            }
        except RaidSyncError:
            raise
        except Exception as e:
            raise RaidSyncError(
                f"Raid execution failed: {e}"
            )

    @staticmethod
    def _find_raid_schedule(user_id, target_playlist_id):
        """Find a raid schedule for user + target playlist."""
        return Schedule.query.filter(
            Schedule.user_id == user_id,
            Schedule.target_playlist_id == target_playlist_id,
            Schedule.job_type.in_([
                JobType.RAID,
                JobType.RAID_AND_SHUFFLE,
            ]),
        ).first()
