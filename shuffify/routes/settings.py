"""
Settings routes: view and update user preferences.
"""

import logging

from flask import (
    session,
    redirect,
    url_for,
    request,
    flash,
    render_template,
)
from pydantic import ValidationError

from shuffify.routes import (
    main,
    is_authenticated,
    require_auth_and_db,
    get_db_user,
    clear_session_and_show_login,
    json_error,
    json_success,
)
from shuffify.services import (
    AuthService,
    ShuffleService,
    AuthenticationError,
    UserSettingsService,
    UserSettingsError,
)
from shuffify.schemas import UserSettingsUpdateRequest

logger = logging.getLogger(__name__)


@main.route("/settings")
def settings():
    """Render the user settings page."""
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
                "Please log in again to access settings.",
                "error",
            )
            return redirect(url_for("main.index"))

        user_settings = UserSettingsService.get_or_create(
            db_user.id
        )
        algorithms = ShuffleService.list_algorithms()
        algorithm_options = [
            {"value": "", "label": "No default (choose each time)"}
        ] + [
            {"value": a["class_name"], "label": a["name"]}
            for a in algorithms
        ]

        return render_template(
            "settings.html",
            user=user,
            settings=user_settings.to_dict(),
            algorithms=algorithms,
            algorithm_options=algorithm_options,
        )

    except AuthenticationError as e:
        logger.error("Error loading settings page: %s", e)
        return clear_session_and_show_login(
            "Your session has expired. "
            "Please log in again."
        )


@main.route("/settings", methods=["POST"])
@require_auth_and_db
def update_settings(client=None, user=None):
    """Update user settings from form submission."""
    # Handle both JSON and form-encoded data
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form.to_dict()

    # Convert checkbox values from form data
    # HTML forms send "on" for checked, nothing for unchecked
    bool_fields = [
        "notifications_enabled",
        "auto_snapshot_enabled",
        "dashboard_show_recent_activity",
    ]
    for field in bool_fields:
        if field in data:
            val = data[field]
            if isinstance(val, str):
                data[field] = val.lower() in (
                    "true",
                    "on",
                    "1",
                    "yes",
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
            return json_error(
                "Invalid value for max snapshots.", 400
            )

    # Handle empty algorithm as None (no default)
    if data.get("default_algorithm") == "":
        data["default_algorithm"] = None

    try:
        update_request = UserSettingsUpdateRequest(**data)
    except ValidationError as e:
        first_error = (
            e.errors()[0] if e.errors() else {}
        )
        msg = first_error.get("msg", "Invalid input")
        return json_error(
            f"Validation error: {msg}", 400
        )

    # Build kwargs from non-None fields only
    update_kwargs = {
        k: v
        for k, v in update_request.model_dump().items()
        if v is not None
    }

    try:
        updated = UserSettingsService.update(
            user.id, **update_kwargs
        )
    except UserSettingsError as e:
        return json_error(str(e), 400)

    # For AJAX requests, return JSON
    is_ajax = (
        request.headers.get("X-Requested-With")
        == "XMLHttpRequest"
    )
    if is_ajax or request.is_json:
        return json_success(
            "Settings saved successfully.",
            settings=updated.to_dict(),
        )

    # For regular form submission, redirect with flash
    flash("Settings saved successfully.", "success")
    return redirect(url_for("main.settings"))
