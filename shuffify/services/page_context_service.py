"""Read-only projections shared by page context endpoints."""

from shuffify.services.state_service import StateService
from shuffify.spotify.api import playlist_item_count

RETURN_TARGETS = frozenset({"playlist", "routines", "routine", "library"})


def playlist_row(playlist, *, projection="summary", preference=None, session_state=None):
    """Project one Spotify playlist into a picker summary or a library row."""
    images = playlist.get("images") or []
    external = playlist.get("external_urls") or {}
    row = {
        "id": playlist["id"],
        "name": playlist["name"],
        "image_url": images[0].get("url") if images else None,
        "track_count": playlist_item_count(playlist),
        "spotify_url": external.get("spotify") or f"https://open.spotify.com/playlist/{playlist['id']}",
    }
    if projection == "library":
        row.update(
            public=playlist.get("public"),
            pinned=bool(preference and preference.is_pinned),
            hidden=bool(preference and preference.is_hidden),
            can_undo=StateService.can_undo(session_state or {}, playlist["id"]),
        )
    elif projection != "summary":
        raise ValueError("Unknown playlist projection")
    return row


def triggered_by(metadata):
    """Report a recorded origin; absent or unknown origins stay unknown."""
    value = metadata.get("triggered_by") if isinstance(metadata, dict) else None
    return value if value in ("manual", "scheduler") else None


def activity_row(activity):
    """Keep the full activity metadata and surface its recorded origin."""
    row = dict(activity) if isinstance(activity, dict) else activity.to_dict()
    row["triggered_by"] = triggered_by(row.get("metadata"))
    return row


def execution_row(execution):
    """Keep the complete durable execution detail without inferring its origin."""
    row = dict(execution)
    origin = row.get("triggered_by")
    row["triggered_by"] = origin if origin in ("manual", "scheduler") else triggered_by(row.get("metadata"))
    return row


def dashboard_context(dashboard):
    """Use the same activity and execution projections inside the library dashboard."""
    return {
        **dashboard,
        "recent_activity": [activity_row(row) for row in dashboard["recent_activity"]],
        "activity_since_last_login": [activity_row(row) for row in dashboard["activity_since_last_login"]],
        "recent_job_executions": [execution_row(row) for row in dashboard["recent_job_executions"]],
    }


def routine_row(payload):
    """Add a machine state code while retaining the human-readable routine label."""
    label = payload["state"]
    return {
        **payload,
        "state": {"Active": "active", "Paused": "paused"}.get(label, "needs_attention"),
        "state_label": label,
    }


def recently_loaded(history):
    """Project the bounded external-playlist history stored by Workshop."""
    if not isinstance(history, list):
        return []
    return [
        {"id": row["id"], "name": row["name"], "owner_id": row.get("owner_id", ""),
         "track_count": row.get("track_count")}
        for row in history[:10]
        if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("name"), str)
    ]
