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
    require_auth,
    clear_session_and_show_login,
    json_error,
    json_success,
    get_db_user,
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
def create_schedule():
    """Create a new scheduled operation."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    if not db_user.encrypted_refresh_token:
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
        user_id=db_user.id,
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
        f"User {db_user.spotify_id} created schedule "
        f"{schedule.id}: {schedule.job_type} on "
        f"{schedule.target_playlist_name}"
    )

    return json_success(
        "Schedule created successfully.",
        schedule=schedule.to_dict(),
    )


@main.route(
    "/schedules/<int:schedule_id>", methods=["PUT"]
)
def update_schedule(schedule_id):
    """Update an existing schedule."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

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
        user_id=db_user.id,
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

    return json_success(
        "Schedule updated successfully.",
        schedule=schedule.to_dict(),
    )


@main.route(
    "/schedules/<int:schedule_id>", methods=["DELETE"]
)
def delete_schedule(schedule_id):
    """Delete a schedule."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    from shuffify.scheduler import remove_job_for_schedule

    remove_job_for_schedule(schedule_id)

    SchedulerService.delete_schedule(
        schedule_id, db_user.id
    )

    logger.info(f"Deleted schedule {schedule_id}")

    return json_success("Schedule deleted successfully.")


@main.route(
    "/schedules/<int:schedule_id>/toggle",
    methods=["POST"],
)
def toggle_schedule(schedule_id):
    """Toggle a schedule's enabled/disabled state."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    schedule = SchedulerService.toggle_schedule(
        schedule_id, db_user.id
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
    return json_success(
        f"Schedule {status_text}.",
        schedule=schedule.to_dict(),
    )


@main.route(
    "/schedules/<int:schedule_id>/run", methods=["POST"]
)
def run_schedule_now(schedule_id):
    """Manually trigger a schedule execution."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    result = JobExecutorService.execute_now(
        schedule_id, db_user.id
    )

    return json_success(
        "Schedule executed successfully.",
        result=result,
    )


@main.route("/schedules/<int:schedule_id>/history")
def schedule_history(schedule_id):
    """Get execution history for a schedule."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    history = SchedulerService.get_execution_history(
        schedule_id, db_user.id, limit=10
    )

    return jsonify({"success": True, "history": history})
