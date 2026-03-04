"""
Schedule routes: CRUD and execution for scheduled operations.
"""

import logging

from flask import (
    session,
    redirect,
    url_for,
    flash,
    jsonify,
    render_template,
    current_app,
)

from shuffify.routes import (
    main,
    is_authenticated,
    require_auth_and_db,
    clear_session_and_show_login,
    json_error,
    json_success,
    get_db_user,
    log_activity,
    validate_json,
)
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    UpstreamSourceService,
    PlaylistPairService,
    AuthenticationError,
    PlaylistError,
    ScheduleError,
)
from shuffify.schemas import (
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
)
from shuffify.services.scheduler_service import (
    SchedulerService,
)
from shuffify.services.executors import (
    JobExecutorService,
)
from shuffify.enums import ActivityType, JobType

logger = logging.getLogger(__name__)


@main.route("/schedules")
def schedules():
    """Render the Schedules management page."""
    if not is_authenticated():
        return redirect(url_for("main.index"))

    try:
        client = AuthService.get_authenticated_client(
            session["spotify_token"]
        )
        user = AuthService.get_user_data(client)

        db_user = get_db_user()
        if not db_user:
            flash(
                "Please log in again to access schedules.",
                "error",
            )
            return redirect(url_for("main.index"))

        user_schedules = (
            SchedulerService.get_user_schedules(db_user.id)
        )

        playlist_service = PlaylistService(client)
        playlists = playlist_service.get_user_playlists()

        algorithms = ShuffleService.list_algorithms()

        # Load Workshop data for dynamic form rendering.
        # Degrade gracefully if queries fail (e.g. pending
        # migrations) — the page still renders without this
        # data; users just can't see raid sources or pairs
        # in the create-schedule modal.
        upstream_sources_map = {}
        try:
            upstream_sources = (
                UpstreamSourceService
                .list_all_sources_for_user(
                    db_user.spotify_id
                )
            )
            for src in upstream_sources:
                target_id = src.target_playlist_id
                if target_id not in upstream_sources_map:
                    upstream_sources_map[target_id] = []
                upstream_sources_map[target_id].append(
                    src.to_dict()
                )
        except Exception as e:
            logger.warning(
                "Could not load upstream sources for "
                "schedules page: %s [type=%s]",
                e,
                type(e).__name__,
            )

        pairs_by_playlist = {}
        try:
            pairs = PlaylistPairService.get_pairs_for_user(
                db_user.id
            )
            pairs_by_playlist = {
                p.production_playlist_id: p.to_dict()
                for p in pairs
            }
        except Exception as e:
            logger.warning(
                "Could not load playlist pairs for "
                "schedules page: %s [type=%s]",
                e,
                type(e).__name__,
            )

        return render_template(
            "schedules.html",
            user=user,
            schedules=[
                s.to_dict() for s in user_schedules
            ],
            playlists=playlists,
            algorithms=algorithms,
            upstream_sources_map=upstream_sources_map,
            pairs_by_playlist=pairs_by_playlist,
        )

    except AuthenticationError as e:
        logger.error(
            "Auth error loading schedules: %s", e
        )
        return clear_session_and_show_login(
            "Your session has expired. "
            "Please log in again."
        )
    except (PlaylistError, ScheduleError) as e:
        logger.error(
            "Service error loading schedules page: %s "
            "[type=%s]",
            e,
            type(e).__name__,
        )
        flash(
            "Could not load schedule data. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("main.index"))
    except Exception as e:
        logger.error(
            "Unexpected error loading schedules page: %s "
            "[type=%s]",
            e,
            type(e).__name__,
            exc_info=True,
        )
        flash(
            "Something went wrong loading schedules. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("main.index"))


@main.route("/schedules/create", methods=["POST"])
@require_auth_and_db
def create_schedule(client=None, user=None):
    """Create a new scheduled operation."""
    if not user.encrypted_refresh_token:
        return json_error(
            "Your account needs a fresh login to enable "
            "scheduled operations. Please log out and "
            "log back in.",
            400,
        )

    create_request, err = validate_json(
        ScheduleCreateRequest
    )
    if err:
        return err

    # Defense-in-depth: validate raid sources exist in Workshop
    if create_request.job_type in ("raid", "raid_and_shuffle"):
        if create_request.source_playlist_ids:
            sources = (
                UpstreamSourceService.list_sources(
                    user.spotify_id,
                    create_request.target_playlist_id,
                )
            )
            valid_ids = {
                s.source_playlist_id for s in sources
            }
            invalid = [
                sid
                for sid in create_request.source_playlist_ids
                if sid not in valid_ids
            ]
            if invalid:
                return json_error(
                    "Some raid sources are not configured "
                    "in the Workshop. Please set them up "
                    "first.",
                    400,
                )

    # Defense-in-depth: validate rotation pair exists
    if create_request.job_type == "rotate":
        pair = PlaylistPairService.get_pair_for_playlist(
            user_id=user.id,
            production_playlist_id=(
                create_request.target_playlist_id
            ),
        )
        if not pair:
            return json_error(
                "This playlist needs an archive pair "
                "configured in the Workshop before "
                "rotation can be scheduled.",
                400,
            )

    schedule = SchedulerService.create_schedule(
        user_id=user.id,
        job_type=create_request.job_type,
        target_playlist_id=(
            create_request.target_playlist_id
        ),
        target_playlist_name=(
            create_request.target_playlist_name
        ),
        schedule_type=create_request.schedule_type,
        schedule_value=create_request.schedule_value,
        source_playlist_ids=(
            create_request.source_playlist_ids
        ),
        algorithm_name=create_request.algorithm_name,
        algorithm_params=create_request.algorithm_params,
    )

    try:
        from shuffify.scheduler import (
            add_job_for_schedule,
        )

        add_job_for_schedule(
            schedule,
            current_app._get_current_object(),
        )
    except Exception as e:
        logger.warning(
            "Could not register schedule %d with "
            "APScheduler: %s [type=%s]",
            schedule.id,
            e,
            type(e).__name__,
        )

    logger.info(
        f"User {user.spotify_id} created schedule "
        f"{schedule.id}: {schedule.job_type} on "
        f"{schedule.target_playlist_name}"
    )

    log_activity(
        user_id=user.id,
        activity_type=ActivityType.SCHEDULE_CREATE,
        description=(
            f"Created {schedule.job_type} schedule "
            f"for '{schedule.target_playlist_name}'"
        ),
        playlist_id=schedule.target_playlist_id,
        playlist_name=schedule.target_playlist_name,
        metadata={
            "schedule_id": schedule.id,
            "job_type": schedule.job_type,
            "schedule_value": schedule.schedule_value,
        },
    )

    return json_success(
        "Schedule created successfully.",
        schedule=schedule.to_dict(),
    )


@main.route(
    "/schedules/<int:schedule_id>", methods=["PUT"]
)
@require_auth_and_db
def update_schedule(
    schedule_id, client=None, user=None
):
    """Update an existing schedule."""
    update_request, err = validate_json(
        ScheduleUpdateRequest
    )
    if err:
        return err

    update_fields = {
        k: v
        for k, v in update_request.model_dump().items()
        if v is not None
    }

    schedule = SchedulerService.update_schedule(
        schedule_id=schedule_id,
        user_id=user.id,
        **update_fields,
    )

    try:
        from shuffify.scheduler import (
            add_job_for_schedule,
            remove_job_for_schedule,
        )

        if schedule.is_enabled:
            add_job_for_schedule(
                schedule,
                current_app._get_current_object(),
            )
        else:
            remove_job_for_schedule(schedule_id)
    except Exception as e:
        logger.warning(
            "Could not update APScheduler job for "
            "schedule %d: %s [type=%s]",
            schedule_id,
            e,
            type(e).__name__,
        )

    logger.info(f"Updated schedule {schedule_id}")

    log_activity(
        user_id=user.id,
        activity_type=ActivityType.SCHEDULE_UPDATE,
        description=(
            f"Updated schedule {schedule_id}"
        ),
        playlist_id=schedule.target_playlist_id,
        playlist_name=schedule.target_playlist_name,
        metadata={
            "schedule_id": schedule_id,
            "updated_fields": list(
                update_fields.keys()
            ),
        },
    )

    return json_success(
        "Schedule updated successfully.",
        schedule=schedule.to_dict(),
    )


@main.route(
    "/schedules/<int:schedule_id>", methods=["DELETE"]
)
@require_auth_and_db
def delete_schedule(
    schedule_id, client=None, user=None
):
    """Delete a schedule."""
    from shuffify.scheduler import remove_job_for_schedule

    remove_job_for_schedule(schedule_id)

    SchedulerService.delete_schedule(
        schedule_id, user.id
    )

    logger.info(f"Deleted schedule {schedule_id}")

    log_activity(
        user_id=user.id,
        activity_type=ActivityType.SCHEDULE_DELETE,
        description=(
            f"Deleted schedule {schedule_id}"
        ),
        metadata={"schedule_id": schedule_id},
    )

    return json_success("Schedule deleted successfully.")


@main.route(
    "/schedules/<int:schedule_id>/toggle",
    methods=["POST"],
)
@require_auth_and_db
def toggle_schedule(
    schedule_id, client=None, user=None
):
    """Toggle a schedule's enabled/disabled state."""
    schedule = SchedulerService.toggle_schedule(
        schedule_id, user.id
    )

    try:
        from shuffify.scheduler import (
            add_job_for_schedule,
            remove_job_for_schedule,
        )

        if schedule.is_enabled:
            add_job_for_schedule(
                schedule,
                current_app._get_current_object(),
            )
        else:
            remove_job_for_schedule(schedule_id)
    except Exception as e:
        logger.warning(
            "Could not update APScheduler job for "
            "schedule %d: %s [type=%s]",
            schedule_id,
            e,
            type(e).__name__,
        )

    status_text = (
        "enabled" if schedule.is_enabled else "disabled"
    )

    log_activity(
        user_id=user.id,
        activity_type=ActivityType.SCHEDULE_TOGGLE,
        description=(
            f"Schedule {schedule_id} {status_text}"
        ),
        playlist_id=schedule.target_playlist_id,
        playlist_name=schedule.target_playlist_name,
        metadata={
            "schedule_id": schedule_id,
            "is_enabled": schedule.is_enabled,
        },
    )

    return json_success(
        f"Schedule {status_text}.",
        schedule=schedule.to_dict(),
    )


@main.route(
    "/schedules/<int:schedule_id>/run", methods=["POST"]
)
@require_auth_and_db
def run_schedule_now(
    schedule_id, client=None, user=None
):
    """Manually trigger a schedule execution."""
    try:
        result = JobExecutorService.execute_now(
            schedule_id, user.id
        )
    except Exception as e:
        logger.error(
            "Manual run of schedule %d failed: %s "
            "[type=%s]",
            schedule_id,
            e,
            type(e).__name__,
            exc_info=True,
        )
        return json_error(
            f"Execution failed: {e}", 500
        )

    tracks = result.get("tracks_total", 0)
    msg = (
        f"Executed successfully — {tracks} tracks "
        f"processed."
        if tracks
        else "Executed but no tracks were processed."
    )

    log_activity(
        user_id=user.id,
        activity_type=ActivityType.SCHEDULE_RUN,
        description=(
            f"Manually ran schedule {schedule_id}"
        ),
        metadata={
            "schedule_id": schedule_id,
            "result": result,
        },
    )

    return json_success(msg, result=result)


@main.route("/schedules/<int:schedule_id>/history")
@require_auth_and_db
def schedule_history(
    schedule_id, client=None, user=None
):
    """Get execution history for a schedule."""
    history = SchedulerService.get_execution_history(
        schedule_id, user.id, limit=10
    )

    return jsonify({"success": True, "history": history})


@main.route(
    "/playlist/<playlist_id>/rotation-status"
)
@require_auth_and_db
def rotation_status(
    playlist_id, client=None, user=None
):
    """Get rotation status for a playlist."""
    from shuffify.services.playlist_pair_service import (
        PlaylistPairService,
    )

    pair_info = None
    pair = PlaylistPairService.get_pair_for_playlist(
        user_id=user.id,
        production_playlist_id=playlist_id,
    )
    if pair:
        pair_info = pair.to_dict()

    rotate_schedule = None
    user_schedules = (
        SchedulerService.get_user_schedules(user.id)
    )
    for s in user_schedules:
        if (
            s.target_playlist_id == playlist_id
            and s.job_type == JobType.ROTATE
        ):
            rotate_schedule = s.to_dict()
            break

    return jsonify({
        "success": True,
        "has_pair": pair_info is not None,
        "pair": pair_info,
        "has_rotation_schedule": (
            rotate_schedule is not None
        ),
        "rotation_schedule": rotate_schedule,
    })
