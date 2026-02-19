"""
Schedule routes: CRUD and execution for scheduled operations.
"""

import logging

from flask import (
    session,
    redirect,
    url_for,
    request,
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
)
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    AuthenticationError,
    PlaylistError,
)
from shuffify.schemas import (
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
)
from shuffify.services.scheduler_service import (
    SchedulerService,
)
from shuffify.services.job_executor_service import (
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

        return render_template(
            "schedules.html",
            user=user,
            schedules=[
                s.to_dict() for s in user_schedules
            ],
            playlists=playlists,
            algorithms=algorithms,
            max_schedules=(
                SchedulerService.MAX_SCHEDULES_PER_USER
            ),
        )

    except (AuthenticationError, PlaylistError) as e:
        logger.error(
            f"Error loading schedules page: {e}"
        )
        return clear_session_and_show_login(
            "Your session has expired. "
            "Please log in again."
        )


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

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    create_request = ScheduleCreateRequest(**data)

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
    except RuntimeError as e:
        logger.warning(
            f"Could not register schedule with "
            f"APScheduler: {e}"
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
    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    update_request = ScheduleUpdateRequest(**data)
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
    except RuntimeError as e:
        logger.warning(
            f"Could not update APScheduler job: {e}"
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
    except RuntimeError as e:
        logger.warning(
            f"Could not update APScheduler job: {e}"
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
    result = JobExecutorService.execute_now(
        schedule_id, user.id
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

    return json_success(
        "Schedule executed successfully.",
        result=result,
    )


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
