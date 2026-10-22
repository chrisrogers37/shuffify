"""
Settings API: read and update user preferences.
"""

import logging

from flask import (
    request,
)
from pydantic import ValidationError

from shuffify.error_handlers import (
    format_validation_error,
)
from shuffify.routes import (
    api_bp,
    json_error,
    json_success,
    require_auth_and_db,
)
from shuffify.schemas import UserSettingsUpdateRequest
from shuffify.services import (
    ShuffleService,
    UserSettingsError,
    UserSettingsService,
)
from shuffify.services.base import is_commit_failure

logger = logging.getLogger(__name__)


@api_bp.route("/settings", methods=["GET"])
@require_auth_and_db
def get_settings(api=None, user=None):
    """Read saved settings and available algorithms."""
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


@api_bp.route("/settings", methods=["POST"])
@require_auth_and_db
def update_settings(api=None, user=None):
    """Update user settings and return the saved values."""
    try:
        updated, error = _save_settings(user)
    except UserSettingsError as e:
        logger.error("Settings write failed: %s", e)
        return json_error("Failed to save settings.", 500)
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
        # A failed commit is the caller's 500; the rest are validation text.
        if is_commit_failure(e):
            raise
        return None, str(e)

    return updated.to_dict(), None
