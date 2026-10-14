"""
Orchestration service for the Smart Raid Panel.

Composes UpstreamSourceService, SchedulerService, and
JobExecutorService into unified raid management operations.
"""

import logging
from contextlib import ExitStack, contextmanager

from shuffify.enums import JobType, ScheduleType
from shuffify.models.db import Schedule, User, db
from shuffify.services.live_operation_service import (
    normalized_operation,
    not_started_facts,
    operation_result,
    rejected_result,
)
from shuffify.services.playlist_lock import (
    PlaylistBusyError,
    playlist_configuration_lock,
    playlist_lock,
)

logger = logging.getLogger(__name__)


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
    def raid_now(
        spotify_id, target_playlist_id,
        source_playlist_ids=None,
        user=None,
        review=None,
    ):
        """
        Trigger an immediate one-off raid.

        If source_playlist_ids is None, uses all configured
        sources. ``review`` (a ``ReviewedRun``) binds the run to its review.
        """
        from shuffify.services.upstream_source_service import (
            UpstreamSourceService,
        )

        if user is None:
            user = User.query.filter_by(
                spotify_id=spotify_id
            ).first()
            if not user:
                raise RaidSyncError("User not found")

        if review is not None:
            # A reviewed Collect raids the sources saved when its lock compares
            # the scope, plus only the request's explicit extras: never IDs
            # listed before the lock, which its wait may have unwatched. A
            # reviewed run had sources or extras; losing them is a changed
            # review, refused inside the target lock rather than here.
            source_playlist_ids = list(source_playlist_ids or [])
        else:
            if source_playlist_ids is None:
                sources = UpstreamSourceService.list_sources(
                    spotify_id, target_playlist_id
                )
                # Search-query sources have no source_playlist_id; keep only
                # real playlist ids so None placeholders don't propagate to the
                # inline path, but count all sources for the "any source?"
                # guard so search-only setups are raidable.
                source_playlist_ids = [
                    s.source_playlist_id
                    for s in sources
                    if s.source_playlist_id
                ]
                has_any_source = len(sources) > 0
            else:
                has_any_source = len(source_playlist_ids) > 0

            if not has_any_source:
                raise RaidSyncError(
                    "No sources configured. "
                    "Watch a playlist first."
                )

        from shuffify.services.executors import (
            JobExecutorService,
        )

        # Collect has one meaning regardless of saved routine type or state.
        # The executor still loads persisted sources plus these extra IDs.
        # Return its facts intact, including a failed/unknown invocation.
        return JobExecutorService.execute_raid_for_user(
            user_id=user.id,
            target_playlist_id=target_playlist_id,
            source_playlist_ids=source_playlist_ids,
            review=review,
        )

    @staticmethod
    def watch_search_query(
        spotify_id,
        target_playlist_id,
        target_playlist_name,
        search_query,
        source_name=None,
        auto_schedule=True,
        schedule_value="daily",
        schedule_time=None,
        user=None,
    ):
        """
        Register a search query as a raid source.

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

        with _source_schedule_mutation(target_playlist_id):
            source = UpstreamSourceService.add_search_source(
                spotify_id=spotify_id,
                target_playlist_id=target_playlist_id,
                search_query=search_query,
                source_name=source_name,
                commit=False,
            )

            schedule = None
            schedule_written = False
            if auto_schedule:
                schedule = RaidSyncService._find_raid_schedule(
                    user.id, target_playlist_id
                )
                if not schedule:
                    sched_type, sched_val = (
                        RaidSyncService._resolve_schedule_params(
                            schedule_value, schedule_time
                        )
                    )
                    schedule = SchedulerService.create_schedule(
                        user_id=user.id,
                        job_type=JobType.RAID,
                        schedule_type=sched_type,
                        schedule_value=sched_val,
                        target_playlist_id=target_playlist_id,
                        target_playlist_name=(
                            target_playlist_name
                        ),
                        source_playlist_ids=[],
                    )
                    schedule_written = True
                    # create_schedule registers the job with APScheduler, so the
                    # schedule fires without an app restart.

            if not schedule_written:
                db.session.commit()

            return {
                "source": source,
                "schedule": schedule,
                "source_playlist_ids": (
                    schedule.source_playlist_ids
                    if schedule
                    else []
                ),
            }

    @staticmethod
    def drip_now(
        spotify_id, target_playlist_id, user=None, review=None,
    ):
        """Trigger an immediate drip from raid playlist
        to target. ``review`` binds the run to its review."""
        from shuffify.services.executors import (
            JobExecutorService,
        )
        from shuffify.services.raid_link_service import (
            RaidLinkService,
        )

        if user is None:
            user = User.query.filter_by(
                spotify_id=spotify_id
            ).first()
            if not user:
                raise RaidSyncError("User not found")

        link = RaidLinkService.get_link_for_playlist(
            user.id, target_playlist_id
        )
        # A reviewed Drip had a link; losing it is a changed review, refused
        # inside the target lock rather than here.
        if not link and review is None:
            raise RaidSyncError(
                "No raid playlist link found. "
                "Create a raid link first."
            )

        # This explicitly requested action always runs a transient, forced
        # Drip. Saved routines retain their own link gate and execution history.
        # The force is a parameter, never a persisted link or schedule mutation.
        return JobExecutorService.execute_drip_for_user(
            user_id=user.id,
            target_playlist_id=target_playlist_id,
            target_playlist_name=link.target_playlist_name if link else None,
            drip_count=link.drip_count if link else None,
            review=review,
        )

    @staticmethod
    def raid_and_drip_now(
        spotify_id, target_playlist_id, source_playlist_ids=None, user=None,
        review=None,
    ):
        """Run each declared step once, retaining every earlier outcome.

        There is no parent execution row. Each child retains its actual
        identity, including an explicitly unstarted Drip after an incomplete
        Raid. A second-step failure never hides already collected tracks.
        A reviewed raid child compares the combined scope in its lock; the
        drip child compares the drip part that comparison matched in its own.
        """
        children = []
        for step, run, kwargs in (
            ("raid", RaidSyncService.raid_now,
             {"source_playlist_ids": source_playlist_ids}),
            ("drip", RaidSyncService.drip_now, {}),
        ):
            if children and children[-1][1]["operation"][
                "aggregate_status"
            ] != "complete":
                result = rejected_result(
                    step, error_code="previous_step_incomplete", http_status=502,
                )
                result["status"] = "skipped"
            else:
                if review is not None:
                    kwargs["review"] = review if step == "raid" else review.drip_child()
                try:
                    result = run(
                        spotify_id=spotify_id,
                        target_playlist_id=target_playlist_id,
                        user=user,
                        **kwargs,
                    )
                except RaidSyncError:
                    result = rejected_result(
                        step, error_code="preflight_failed", http_status=400,
                    )
                except Exception:
                    logger.exception("Immediate %s outcome unavailable", step)
                    result = rejected_result(
                        step, error_code="outcome_unavailable", unknown=True,
                        http_status=502,
                    )
                operation = normalized_operation(result, step)
                if operation is not result.get("operation"):
                    # Missing or contradictory verification facts cannot
                    # establish success or authorize the dependent step.
                    result = {**result, "operation": operation, "http_status": 502}
            if result.get("execution_state") == "not_started" or result.get("status") == "skipped":
                # A child that never ran (not started, or skipped: its lock was busy or an earlier step
                # did not complete) changed nothing: report its inbox and target facts as such.
                facts = result["operation"]
                unstarted_bookkeeping, unstarted_resources = not_started_facts(
                    [step], target_playlist_id, bookkeeping=facts["bookkeeping"], resources=facts["resources"],
                )
                result = {**result, "operation": {
                    **facts, "bookkeeping": facts["bookkeeping"] + unstarted_bookkeeping,
                    "resources": facts["resources"] + unstarted_resources,
                }}
            children.append((step, result))

        resources, bookkeeping, steps = [], [], []
        for step, result in children:
            operation = result["operation"]
            resources.extend(operation.get("resources", []))
            bookkeeping.extend(operation.get("bookkeeping", []))
            steps.append({
                "step": step,
                "execution_id": result.get("execution_id"),
                "result_detail": result.get("result_detail"),
                "started_at": result.get("started_at"),
                "execution_state": result.get("execution_state", "unknown"),
                "status": result.get("status", "unknown"),
                "aggregate_status": operation["aggregate_status"],
                "error_code": operation.get("error_code"),
                "tracks_added": result.get("tracks_added"),
                "tracks_total": result.get("tracks_total"),
            })
        incomplete = next((result for _step, result in children
                           if result["operation"]["aggregate_status"] != "complete"), None)
        operation = operation_result(
            "raid_and_drip", resources=resources, bookkeeping=bookkeeping,
            steps=steps,
            error_code=(incomplete["operation"].get("error_code")
                        if incomplete else None),
        )
        complete = operation["aggregate_status"] == "complete"
        http_status = 200 if complete else 502
        if children[0][1]["operation"]["aggregate_status"] == "partial":
            # Preserve a first-step preflight/busy rejection. Once the first
            # step completes, later incompletion is a composite partial 502.
            http_status = children[0][1].get("http_status", 502)
        durable_children = [result.get("result_detail") for _step, result in children]
        any_durable = any(detail and detail.get("detail_status") == "available" for detail in durable_children)
        return {
            "status": "success" if complete else "failed",
            "tracks_raided": children[0][1].get("tracks_added"),
            "tracks_dripped": children[1][1].get("tracks_added"),
            "operation": operation,
            "result_detail": {
                "detail_status": "available" if any_durable else "unavailable",
                "version": 1 if any_durable else None,
                "operation": "raid_and_drip" if any_durable else None,
                "steps": [
                    {"step": step, "execution_id": result.get("execution_id"),
                     "result_detail": result.get("result_detail")}
                    for step, result in children
                ] if any_durable else None,
            },
            "http_status": http_status,
            # The first incomplete child's own copy, e.g. a refused review's.
            "message": incomplete.get("message") if incomplete else None,
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

    @staticmethod
    def update_raid_schedule(
        spotify_id,
        target_playlist_id,
        user=None,
        **kwargs,
    ):
        """Update an existing raid schedule.

        Accepts schedule_value, schedule_time, is_enabled.
        """
        from shuffify.services.scheduler_service import (
            SchedulerService,
        )

        if user is None:
            user = User.query.filter_by(
                spotify_id=spotify_id
            ).first()
            if not user:
                raise RaidSyncError("User not found")

        schedule = RaidSyncService._find_raid_schedule(
            user.id, target_playlist_id
        )
        if not schedule:
            raise RaidSyncError(
                "No raid schedule found"
            )

        # Build update fields
        update_fields = {}

        sched_value = kwargs.get("schedule_value")
        sched_time = kwargs.get("schedule_time")
        is_enabled = kwargs.get("is_enabled")

        if sched_value is not None:
            sched_type, sched_val = (
                RaidSyncService._resolve_schedule_params(
                    sched_value, sched_time
                )
            )
            update_fields["schedule_type"] = sched_type
            update_fields["schedule_value"] = sched_val
        elif sched_time is not None:
            # Time changed but frequency didn't — use
            # current schedule_value as base
            current_val = schedule.schedule_value
            sched_type, sched_val = (
                RaidSyncService._resolve_schedule_params(
                    current_val, sched_time
                )
            )
            update_fields["schedule_type"] = sched_type
            update_fields["schedule_value"] = sched_val

        if is_enabled is not None:
            update_fields["is_enabled"] = is_enabled

        if not update_fields:
            return schedule

        updated = SchedulerService.update_schedule(
            schedule.id, user.id, **update_fields
        )

        # APScheduler sync is handled inside update_schedule.

        return updated
