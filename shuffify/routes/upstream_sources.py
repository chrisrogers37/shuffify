"""
Upstream source routes: manage playlist upstream source configs.
"""

import logging

from flask import session, request, jsonify

from shuffify.routes import main, require_auth, json_error, json_success
from shuffify.services import (
    UpstreamSourceService,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
    ActivityLogService,
    UserService,
)
from shuffify.enums import ActivityType
from shuffify import is_db_available

logger = logging.getLogger(__name__)


@main.route(
    "/playlist/<playlist_id>/upstream-sources",
    methods=["GET"],
)
def list_upstream_sources(playlist_id):
    """List all upstream sources for a target playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable.", 503)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error(
            "User data not found in session.", 401
        )

    sources = UpstreamSourceService.list_sources(
        spotify_id, playlist_id
    )
    return jsonify({
        "success": True,
        "sources": [s.to_dict() for s in sources],
    })


@main.route(
    "/playlist/<playlist_id>/upstream-sources",
    methods=["POST"],
)
def add_upstream_source(playlist_id):
    """Add an upstream source to a target playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable.", 503)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    source_playlist_id = data.get("source_playlist_id")
    if not source_playlist_id:
        return json_error(
            "source_playlist_id is required.", 400
        )

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error(
            "User data not found in session.", 401
        )

    try:
        source = UpstreamSourceService.add_source(
            spotify_id=spotify_id,
            target_playlist_id=playlist_id,
            source_playlist_id=source_playlist_id,
            source_type=data.get(
                "source_type", "external"
            ),
            source_url=data.get("source_url"),
            source_name=data.get("source_name"),
        )

        # Log activity (non-blocking)
        try:
            db_user = UserService.get_by_spotify_id(
                spotify_id
            )
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=(
                        ActivityType.UPSTREAM_SOURCE_ADD
                    ),
                    description=(
                        f"Added upstream source "
                        f"'{data.get('source_name', source_playlist_id)}'"
                    ),
                    playlist_id=playlist_id,
                    metadata={
                        "source_playlist_id": (
                            source_playlist_id
                        ),
                        "source_type": data.get(
                            "source_type", "external"
                        ),
                    },
                )
        except Exception:
            pass

        return json_success(
            "Source added.",
            source=source.to_dict(),
        )
    except UpstreamSourceError as e:
        return json_error(str(e), 400)


@main.route(
    "/upstream-sources/<int:source_id>",
    methods=["DELETE"],
)
def delete_upstream_source(source_id):
    """Delete an upstream source configuration."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable.", 503)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error(
            "User data not found in session.", 401
        )

    try:
        UpstreamSourceService.delete_source(
            source_id, spotify_id
        )

        # Log activity (non-blocking)
        try:
            db_user = UserService.get_by_spotify_id(
                spotify_id
            )
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=(
                        ActivityType
                        .UPSTREAM_SOURCE_DELETE
                    ),
                    description=(
                        f"Removed upstream source "
                        f"{source_id}"
                    ),
                )
        except Exception:
            pass

        return json_success("Source removed.")
    except UpstreamSourceNotFoundError:
        return json_error("Source not found.", 404)
    except UpstreamSourceError as e:
        return json_error(str(e), 500)
