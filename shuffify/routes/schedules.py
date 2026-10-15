"""
Schedule routes: CRUD and execution for scheduled operations.
"""

import logging

from flask import (
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from shuffify.enums import ActivityType, JobType
from shuffify.models.db import Schedule
from shuffify.routes import (
    api_bp,
    clear_session_and_show_login,
    context_preflight,
    json_error,
    json_reply,
    json_success,
    log_activity,
    no_store,
    require_auth_and_db,
    require_auth_page,
    site_bp,
    validate_json,
)
from shuffify.schemas import (
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
)
from shuffify.services import (
    AuthenticationError,
    PlaylistError,
    PlaylistPairService,
    PlaylistService,
    ScheduleError,
    UpstreamSourceService,
)
from shuffify.services.api_response import busy_error
from shuffify.services.executors import (
    JobExecutorService,
)
from shuffify.services.live_executor_context_service import (
    LiveExecutorContextService,
)
from shuffify.services.live_operation_service import (
    error_status,
    normalized_operation,
    preflight_error_code,
    rejected_result,
)
from shuffify.services.live_review_bindings import (
    ReviewBindingError,
    invalid_binding_result,
    request_binding,
    review_message,
)
from shuffify.services.live_review_context import context_unavailable
from shuffify.services.live_review_scope import ReviewedRun
from shuffify.services.routine_activation_service import _schedule_payload
from shuffify.services.scheduler_service import (
    ScheduleBusyError,
    SchedulerService,
)

logger = logging.getLogger(__name__)


@api_bp.route("/schedules/latest-results", methods=["GET"])
@no_store
@require_auth_and_db
def latest_schedule_results(api=None, user=None):
    """Read each owned routine's latest execution in one bounded batch."""
    return json_success(
        "Latest routine results loaded",
        results=SchedulerService.get_latest_executions(user.id, request.args.get("playlist_id")),
    )


@api_bp.route(
    "/playlist/<playlist_id>/schedules",
    methods=["GET"],
)
@require_auth_and_db
def playlist_schedules(
    playlist_id, api=None, user=None
):
    """Get all schedules for a specific playlist."""
    schedules = (
        SchedulerService.get_schedules_for_playlist(
            user.id, playlist_id
        )
    )
    return json_success(
        "Schedules loaded",
        schedules=[s.to_dict() for s in schedules],
    )


@site_bp.route("/schedules")
@require_auth_page
def schedules(api=None, user=None, spotify_profile=None):
    """Render the Schedules management page."""
    try:
        db_user = user

        user_schedules = (
            SchedulerService.get_user_schedules(db_user.id)
        )

        playlist_service = PlaylistService(api)
        playlists = playlist_service.get_user_playlists()

        return render_template(
            "schedules.html",
            user=spotify_profile,
            routines=[_schedule_payload(s) for s in user_schedules],
            playlists=playlists,
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
        return redirect(url_for("site.index"))
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
        return redirect(url_for("site.index"))


@api_bp.route("/schedules/create", methods=["POST"])
@require_auth_and_db
def create_schedule(api=None, user=None):
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

    # Defense-in-depth: the user must be able to edit the target playlist
    # (owner or collaborative). Otherwise a schedule can be created against an
    # arbitrary playlist ID that fails forever at execution time.
    PlaylistService(api).validate_user_can_edit(
        create_request.target_playlist_id, user.spotify_id
    )

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

    try:
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
    except ScheduleBusyError as error:
        return json_reply(busy_error(str(error)))

    # APScheduler registration is handled inside create_schedule.

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


@api_bp.route(
    "/schedules/<int:schedule_id>", methods=["PUT"]
)
@require_auth_and_db
def update_schedule(
    schedule_id, api=None, user=None
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

    try:
        schedule = SchedulerService.update_schedule(
            schedule_id=schedule_id,
            user_id=user.id,
            **update_fields,
        )
    except ScheduleBusyError as error:
        return json_reply(busy_error(str(error)))

    # APScheduler sync is handled inside update_schedule.

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


@api_bp.route(
    "/schedules/<int:schedule_id>", methods=["DELETE"]
)
@require_auth_and_db
def delete_schedule(
    schedule_id, api=None, user=None
):
    """Delete a schedule."""
    # delete_schedule removes the APScheduler job after the DB delete commits,
    # so a failed delete can't orphan a live job.
    try:
        SchedulerService.delete_schedule(
            schedule_id, user.id
        )
    except ScheduleBusyError as error:
        return json_reply(busy_error(str(error)))

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


@api_bp.route(
    "/schedules/<int:schedule_id>/toggle",
    methods=["POST"],
)
@require_auth_and_db
def toggle_schedule(
    schedule_id, api=None, user=None
):
    """Toggle a schedule's enabled/disabled state."""
    try:
        schedule = SchedulerService.toggle_schedule(
            schedule_id, user.id
        )
    except ScheduleBusyError as error:
        return json_reply(busy_error(str(error)))

    # APScheduler sync is handled inside toggle_schedule.

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


def _run_result_response(result, *, message=None):
    """Keep the legacy result envelope and one common operation payload.

    An incomplete run keeps ``message``, else a reviewed rejection's own copy,
    else the generic copy for its outcome.
    """
    payload = {key: value for key, value in result.items()
               if key not in {"operation", "http_status", "message", "error"}}
    operation = normalized_operation(result, "schedule")
    if operation.get("aggregate_status") == "complete":
        tracks = payload.get("tracks_total")
        message = (
            f"Executed successfully — {tracks} tracks processed."
            if tracks else "Executed but no tracks were processed."
        )
        return json_success(message, result=payload, operation=operation)
    message = message or review_message(result) or (
        "This run could not be verified. Review the playlist results "
        "before running it again."
        if operation.get("aggregate_status") == "unknown"
        else "This run did not finish. Review the playlist results "
             "before running it again."
    )
    return jsonify({
        "success": False, "category": "error", "message": message,
        "error": message,
        "result": payload, "operation": operation,
    }), error_status(result)


def _run_preflight_error(message, status_code):
    return _run_result_response(
        rejected_result(
            "schedule", error_code=preflight_error_code(status_code),
            http_status=status_code,
        ),
        message=message,
    )


@api_bp.route(
    "/schedules/<int:schedule_id>/run", methods=["POST"]
)
@require_auth_and_db(on_preflight_error=_run_preflight_error)
def run_schedule_now(schedule_id, api=None, user=None):
    """Return only this authorized invocation, including incomplete outcomes."""
    # An optional reviewed binding; a malformed one is refused before any read.
    try:
        binding = request_binding(request, "schedule")
    except ReviewBindingError:
        return _run_result_response(invalid_binding_result("schedule"))
    review = ReviewedRun("schedule", binding["reviewed_scope"]) if binding else None
    try:
        result = JobExecutorService.execute_now(
            schedule_id, user.id, review=review,
        )
    except Exception:
        logger.exception("Manual schedule %d outcome unavailable", schedule_id)
        result = rejected_result(
            "schedule", unknown=True, error_code="outcome_unavailable",
            http_status=502,
        )
    # Confirmed mutations are logged by the executor, once, with their origin.
    return _run_result_response(result)


@api_bp.route(
    "/schedules/<int:schedule_id>/run-context", methods=["GET"]
)
@no_store
@require_auth_and_db(on_preflight_error=context_preflight("schedule"))
def run_schedule_context(schedule_id, api=None, user=None):
    """Read-only review of running one owned routine now; changes nothing."""
    try:
        reply = LiveExecutorContextService(api).schedule(schedule_id, user)
    except Exception:
        logger.exception("Routine review unavailable for %s", schedule_id)
        reply = context_unavailable("schedule")
    return json_reply(reply)


@api_bp.route("/schedules/<int:schedule_id>/history")
@require_auth_and_db
def schedule_history(
    schedule_id, api=None, user=None
):
    """Get execution history for a schedule."""
    history = SchedulerService.get_execution_history(
        schedule_id, user.id, limit=10
    )

    return jsonify({"success": True, "history": history})


@api_bp.route(
    "/playlist/<playlist_id>/rotation-status"
)
@require_auth_and_db
def rotation_status(
    playlist_id, api=None, user=None
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

    rotate_schedule_obj = (
        Schedule.query.filter_by(
            user_id=user.id,
            target_playlist_id=playlist_id,
            job_type=JobType.ROTATE,
        ).first()
    )
    rotate_schedule = (
        rotate_schedule_obj.to_dict()
        if rotate_schedule_obj
        else None
    )

    return jsonify({
        "success": True,
        "has_pair": pair_info is not None,
        "pair": pair_info,
        "has_rotation_schedule": (
            rotate_schedule is not None
        ),
        "rotation_schedule": rotate_schedule,
    })
