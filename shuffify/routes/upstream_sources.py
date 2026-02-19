"""
Upstream source routes: manage playlist upstream source configs.
"""

import logging

from flask import request, jsonify

from shuffify.routes import (
    main,
    require_auth_and_db,
    json_error,
    json_success,
    log_activity,
)
from shuffify.services import (
    UpstreamSourceService,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
)
from shuffify.enums import ActivityType

logger = logging.getLogger(__name__)


@main.route(
    "/playlist/<playlist_id>/upstream-sources",
    methods=["GET"],
)
@require_auth_and_db
def list_upstream_sources(
    playlist_id, client=None, user=None
):
    """List all upstream sources for a target playlist."""
    sources = UpstreamSourceService.list_sources(
        user.spotify_id, playlist_id
    )
    return jsonify({
        "success": True,
        "sources": [s.to_dict() for s in sources],
    })


@main.route(
    "/playlist/<playlist_id>/upstream-sources",
    methods=["POST"],
)
@require_auth_and_db
def add_upstream_source(
    playlist_id, client=None, user=None
):
    """Add an upstream source to a target playlist."""
    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    source_playlist_id = data.get("source_playlist_id")
    if not source_playlist_id:
        return json_error(
            "source_playlist_id is required.", 400
        )

    try:
        source = UpstreamSourceService.add_source(
            spotify_id=user.spotify_id,
            target_playlist_id=playlist_id,
            source_playlist_id=source_playlist_id,
            source_type=data.get(
                "source_type", "external"
            ),
            source_url=data.get("source_url"),
            source_name=data.get("source_name"),
        )

        log_activity(
            user_id=user.id,
            activity_type=ActivityType.UPSTREAM_SOURCE_ADD,
            description=(
                f"Added upstream source "
                f"'{data.get('source_name', source_playlist_id)}'"
            ),
            playlist_id=playlist_id,
            metadata={
                "source_playlist_id": source_playlist_id,
                "source_type": data.get(
                    "source_type", "external"
                ),
            },
        )

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
@require_auth_and_db
def delete_upstream_source(
    source_id, client=None, user=None
):
    """Delete an upstream source configuration."""
    try:
        UpstreamSourceService.delete_source(
            source_id, user.spotify_id
        )

        log_activity(
            user_id=user.id,
            activity_type=(
                ActivityType.UPSTREAM_SOURCE_DELETE
            ),
            description=(
                f"Removed upstream source {source_id}"
            ),
        )

        return json_success("Source removed.")
    except UpstreamSourceNotFoundError:
        return json_error("Source not found.", 404)
    except UpstreamSourceError as e:
        return json_error(str(e), 500)
