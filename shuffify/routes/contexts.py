"""Read the complete contexts consumed by the application pages."""

import logging
from datetime import datetime

from flask import request, session
from flask_wtf.csrf import generate_csrf

from shuffify.routes import (
    api_bp,
    is_authenticated,
    json_error,
    json_success,
    load_schedule_context,
    require_auth_and_db,
)
from shuffify.services import (
    ActivityLogService,
    DashboardService,
    PlaylistError,
    PlaylistService,
    ShuffleService,
    UserSettingsService,
)
from shuffify.services.auth_notice import auth_notice
from shuffify.services.operations.history import HistoryStore, get_history_scope
from shuffify.services.page_context_service import (
    activity_row,
    dashboard_context,
    execution_row,
    playlist_row,
    recently_loaded,
    routine_row,
)
from shuffify.services.playlist_preference_service import PlaylistPreferenceService
from shuffify.services.playlist_service import PlaylistAccessError, PlaylistNotFoundError
from shuffify.services.routine_activation_service import schedule_payloads
from shuffify.services.scheduler_service import SchedulerService

logger = logging.getLogger(__name__)


def _playlist_sections(api, user, *, refresh=False):
    playlists = PlaylistService(api).get_user_playlists(skip_cache=refresh)
    try:
        preferences = PlaylistPreferenceService.get_user_preferences(user.id)
        favorites, visible, hidden = PlaylistPreferenceService.apply_preferences(playlists, preferences)
    except Exception:
        logger.warning("Could not load library ordering preferences", exc_info=True)
        favorites, visible, hidden, preferences = [], playlists, [], {}
    return favorites, visible, hidden, preferences


def _previous_login(user):
    stored = session.get("previous_login_at")
    if "previous_login_at" in session:
        return datetime.fromisoformat(stored) if stored else None
    return user.last_login_at


@api_bp.route("/session")
def session_context():
    """Read session identity and consume one authored notice, including while signed out."""
    notice = auth_notice(session.pop("auth_notice", None))
    profile = session.get("user_data")
    if not is_authenticated() or not isinstance(profile, dict) or not isinstance(profile.get("id"), str):
        return json_success("Session loaded.", signed_in=False, user=None, notice=notice)
    images = profile.get("images")
    image_url = images[0].get("url") if isinstance(images, list) and images and isinstance(images[0], dict) else None
    display_name = profile.get("display_name")
    return json_success(
        "Session loaded.", signed_in=True,
        user={"id": profile["id"], "display_name": display_name if isinstance(display_name, str) else None,
              "image_url": image_url if isinstance(image_url, str) else None},
        csrf_token=generate_csrf(), notice=notice,
    )


@api_bp.route("/activity")
@require_auth_and_db
def activity_context(api=None, user=None):
    """Read bounded activity and execution histories with their full stored detail."""
    return json_success(
        "Activity loaded.", stats=DashboardService.get_quick_stats(user.id),
        activities=[activity_row(row) for row in ActivityLogService.get_recent(user.id, limit=100)],
        executions=[execution_row(row) for row in DashboardService.get_recent_executions(user.id, limit=20)],
    )


@api_bp.route("/library")
@require_auth_and_db
def library_context(api=None, user=None):
    """Read ordered library rows; refresh bypasses Spotify's playlist-list cache."""
    try:
        favorites, visible, hidden, preferences = _playlist_sections(
            api, user, refresh=request.args.get("refresh") == "1"
        )
    except PlaylistError:
        logger.warning("Could not load the library playlists", exc_info=True)
        return json_error("Could not load your library. Please try again.", 500)
    dashboard = dashboard_context(DashboardService.get_dashboard_data(user.id, last_login_at=_previous_login(user)))
    profile = session.get("user_data") or {}
    history = HistoryStore().session_for(
        user.id, get_history_scope(session), [row["id"] for row in favorites + visible + hidden],
        legacy=session.get("playlist_states"),
    )
    return json_success(
        "Library loaded.",
        sections={name: [playlist_row(row, projection="library", preference=preferences.get(row["id"]),
                                      session_state=history) for row in rows]
                  for name, rows in (("favorites", favorites), ("playlists", visible), ("hidden", hidden))},
        algorithms=ShuffleService.list_algorithms(),
        shuffle_defaults=UserSettingsService.get_default_shuffle_settings(user.id),
        greeting={"is_new_user": bool(session.get("is_new_user")),
                  "is_returning_user": dashboard["is_returning_user"], "display_name": profile.get("display_name"),
                  "activity_since_last_login_count": dashboard["activity_since_last_login_count"]},
        dashboard=dashboard,
    )


@api_bp.route("/routines")
@require_auth_and_db
def routines_context(api=None, user=None):
    """Read full routine rows and the editable playlist names used by their forms."""
    return json_success(
        "Routines loaded.",
        routines=[routine_row(row) for row in schedule_payloads(SchedulerService.get_user_schedules(user.id))],
        playlists=[{"id": row["id"], "name": row["name"]} for row in PlaylistService(api).get_user_playlists()],
    )


@api_bp.route("/workshop/<playlist_id>")
@require_auth_and_db
def workshop_context(playlist_id, api=None, user=None):
    """Read an editable playlist, its tracks, navigation and loaded source history."""
    playlists = PlaylistService(api)
    try:
        playlists.validate_user_can_edit(playlist_id, user.spotify_id, lean=True)
        playlist = playlists.get_playlist(playlist_id, include_features=False)
    except (PlaylistNotFoundError, PlaylistAccessError):
        return json_error("That playlist doesn't exist or is not accessible.", 404)
    try:
        favorites, visible, _hidden, _preferences = _playlist_sections(api, user)
        ordered = favorites + visible
    except Exception:
        logger.warning("Could not load Workshop playlist navigation", exc_info=True)
        ordered = []
    ids = [row["id"] for row in ordered]
    index = ids.index(playlist_id) if playlist_id in ids else None
    previous = ordered[index - 1] if index is not None and index > 0 else None
    following = ordered[index + 1] if index is not None and index < len(ordered) - 1 else None
    sources, _pairs = load_schedule_context(user)
    data = playlist.to_dict()
    tracks = data.pop("tracks")
    return json_success(
        "Workshop loaded.", playlist=data, tracks=tracks, algorithms=ShuffleService.list_algorithms(),
        upstream_sources=sources,
        previous={"id": previous["id"], "name": previous["name"]} if previous else None,
        next={"id": following["id"], "name": following["name"]} if following else None,
        recently_loaded=recently_loaded(session.get("external_playlist_history")),
    )
