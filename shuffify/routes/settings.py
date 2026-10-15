"""
Settings routes: view and update user preferences.
"""

import logging

from flask import (
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from pydantic import ValidationError

from shuffify.error_handlers import (
    format_validation_error,
)
from shuffify.routes import (
    api_bp,
    clear_session_and_show_login,
    json_error,
    json_success,
    require_auth_and_db,
    require_auth_page,
    site_bp,
)
from shuffify.schemas import UserSettingsUpdateRequest
from shuffify.services import (
    AuthenticationError,
    ShuffleService,
    UserSettingsError,
    UserSettingsService,
)

logger = logging.getLogger(__name__)


@site_bp.route("/settings")
@require_auth_page
def settings(api=None, user=None, spotify_profile=None):
    """Render the user settings page."""
    try:
        user_settings = UserSettingsService.get_or_create(user.id)
        algorithms = ShuffleService.list_algorithms()
        algorithm_options = [
            {"value": "", "label": "No default (choose each time)"}
        ] + [
            {"value": a["class_name"], "label": a["name"]}
            for a in algorithms
        ]

        return render_template(
            "settings.html",
            user=spotify_profile,
            settings=user_settings.to_dict(),
            algorithms=algorithms,
            algorithm_options=algorithm_options,
        )

    except AuthenticationError as e:
        logger.error("Auth error loading settings page: %s", e)
        return clear_session_and_show_login(
            "Your session has expired. "
            "Please log in again."
        )
    except UserSettingsError as e:
        logger.error(
            "Settings service error loading settings page: %s",
            e,
        )
        flash(
            "Could not load your settings. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("site.index"))
    except Exception as e:
        logger.error(
            "Unexpected error loading settings page: %s "
            "[type=%s]",
            e,
            type(e).__name__,
            exc_info=True,
        )
        flash(
            "Something went wrong loading settings. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("site.index"))


@api_bp.route("/settings", methods=["GET"])
@require_auth_and_db
def get_settings(api=None, user=None):
    """Read saved settings and the algorithms available to the settings form."""
    return json_success("Settings loaded.", **_settings_context(user))


def _settings_context(user, settings=None):
    """The complete settings context returned after both reads and updates."""
    algorithms = ShuffleService.list_algorithms()
    return {
        "settings": settings if settings is not None else UserSettingsService.get_or_create(user.id).to_dict(),
        "algorithms": algorithms,
        "algorithm_options": [{"value": "", "label": "No default (choose each time)"}] + [
            {"value": algorithm["class_name"], "label": algorithm["name"]} for algorithm in algorithms
        ],
    }


@site_bp.route("/settings", methods=["POST"])
@require_auth_page
def update_settings_page(api=None, user=None, spotify_profile=None):
    """Save a native settings form and return to its HTML page with feedback."""
    _updated, error = _save_settings(user)
    flash(error or "Settings saved successfully.", "error" if error else "success")
    return redirect(url_for("site.settings"))


@api_bp.route("/settings", methods=["POST"])
@require_auth_and_db
def update_settings(api=None, user=None):
    """Update user settings and return the saved values."""
    updated, error = _save_settings(user)
    if error:
        return json_error(error, 400)
    return json_success("Settings saved successfully.", **_settings_context(user, updated))


def _save_settings(user):
    """Parse JSON or native form values and persist one validated settings update."""
    # Handle both JSON and form-encoded data
    if request.is_json:
        data = request.get_json()
        if not isinstance(data, dict):
            return None, "Request body must be a JSON object."
    else:
        data = request.form.to_dict()

    # Convert checkbox values from form data
    # HTML forms send "on" for checked, nothing for unchecked
    bool_fields = [
        "notifications_enabled",
        "auto_snapshot_enabled",
        "dashboard_show_recent_activity",
    ]
    truthy_strings = ("true", "on", "1", "yes")
    for field in bool_fields:
        if field in data:
            if request.is_json:
                val = data[field]
                if isinstance(val, str):
                    data[field] = val.lower() in truthy_strings
            else:
                # Form encoding: a field may be submitted multiple
                # times (e.g. a sibling hidden default + a checkbox
                # carrying the user's actual choice). `to_dict()`
                # keeps only one of them, so read every value via
                # `getlist` and let any truthy value win: the
                # checkbox's `value="true"` is always honored when
                # checked, regardless of DOM order.
                vals = request.form.getlist(field)
                data[field] = any(
                    str(v).lower() in truthy_strings for v in vals
                )
        else:
            # Unchecked checkboxes are absent from form data
            if not request.is_json:
                data[field] = False

    # Convert max_snapshots_per_playlist to int
    if "max_snapshots_per_playlist" in data:
        try:
            data["max_snapshots_per_playlist"] = int(
                data["max_snapshots_per_playlist"]
            )
        except (ValueError, TypeError):
            return None, "Invalid value for max snapshots."

    # Handle empty algorithm as None (no default)
    if data.get("default_algorithm") == "":
        data["default_algorithm"] = None

    try:
        update_request = UserSettingsUpdateRequest(**data)
    except ValidationError as e:
        return None, f"Validation error: {format_validation_error(e)}"

    # Leave omitted values unchanged, but an explicitly cleared algorithm
    # means "choose each time" and must clear the stored default.
    update_kwargs = {
        k: v
        for k, v in update_request.model_dump().items()
        if v is not None
    }
    if "default_algorithm" in update_request.model_fields_set:
        update_kwargs["default_algorithm"] = update_request.default_algorithm

    try:
        updated = UserSettingsService.update(
            user.id, **update_kwargs
        )
    except UserSettingsError as e:
        return None, str(e)

    return updated.to_dict(), None
