"""
Routine management page, exact deletion, execution and history endpoints.
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

from shuffify.enums import ActivityType
from shuffify.routes import (
    api_bp,
    clear_session_and_show_login,
    context_preflight,
    json_reply,
    json_success,
    log_activity,
    no_store,
    require_auth_and_db,
    require_auth_page,
    require_session_and_db,
    site_bp,
)
from shuffify.routes.routine_queue import admit_schedule
from shuffify.services import (
    AuthenticationError,
    PlaylistError,
    PlaylistService,
    ScheduleError,
)
from shuffify.services.api_response import busy_error
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
from shuffify.services.routine_activation_service import schedule_payloads
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
            routines=schedule_payloads(user_schedules),
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


@api_bp.route(
    "/schedules/<int:schedule_id>", methods=["DELETE"]
)
@require_auth_and_db
def delete_schedule(
    schedule_id, api=None, user=None
):
    """Delete a schedule."""
    # Worker refresh removes the memory registration after the DB delete commits,
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
@require_session_and_db(on_preflight_error=_run_preflight_error)
def run_schedule_now(schedule_id, api=None, user=None):
    """Accept one owned routine without authenticating or calling Spotify."""
    # An optional reviewed binding; a malformed one is refused before any read.
    try:
        binding = request_binding(request, "schedule")
    except ReviewBindingError:
        return _run_result_response(invalid_binding_result("schedule"))
    return admit_schedule(user, schedule_id, reviewed=binding, on_rejection=_run_preflight_error)


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
