"""
Raid executor: pull new tracks from source playlists into target.

Tracks are staged in PendingRaidTrack for user review instead of
being added directly to Spotify.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from shuffify.models.db import Schedule, UpstreamSource, db
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.enums import SnapshotType
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)
from shuffify.services.pending_raid_service import (
    PendingRaidService,
)
from shuffify.services.source_resolver import SourceResolver

logger = logging.getLogger(__name__)


def execute_raid(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """
    Pull new tracks from source playlists and stage them
    for user review in the Track Inbox.
    """
    from shuffify.services.executors.base_executor import (
        JobExecutionError,
    )

    target_id = schedule.target_playlist_id
    source_ids = schedule.source_playlist_ids or []

    if not source_ids:
        logger.info(
            f"Schedule {schedule.id}: no source playlists "
            f"configured, skipping raid"
        )
        target_tracks = api.get_playlist_tracks(target_id)
        return {
            "tracks_added": 0,
            "tracks_total": len(target_tracks),
        }

    try:
        target_tracks = api.get_playlist_tracks(target_id)
        target_uris = {
            t.get("uri")
            for t in target_tracks
            if t.get("uri")
        }

        _auto_snapshot_before_raid(
            schedule, target_tracks
        )

        new_uris = _fetch_raid_sources(
            api, source_ids, target_uris,
            user_id=schedule.user_id,
        )

        if not new_uris:
            logger.info(
                f"Schedule {schedule.id}: "
                f"no new tracks to add"
            )
            return {
                "tracks_added": 0,
                "tracks_total": len(target_tracks),
            }

        # Fetch metadata and stage for review
        track_dicts = _build_track_dicts(api, new_uris)
        staged = PendingRaidService.stage_tracks(
            user_id=schedule.user_id,
            target_playlist_id=target_id,
            tracks=track_dicts,
            source_name=schedule.target_playlist_name,
        )

        logger.info(
            f"Schedule {schedule.id}: staged "
            f"{staged} tracks for review on "
            f"{schedule.target_playlist_name}"
        )

        return {
            "tracks_added": staged,
            "tracks_total": len(target_tracks),
        }

    except SpotifyNotFoundError:
        raise JobExecutionError(
            f"Target playlist {target_id} not found. "
            f"It may have been deleted."
        )
    except SpotifyAPIError as e:
        raise JobExecutionError(
            f"Spotify API error during raid: {e}"
        )


def _auto_snapshot_before_raid(
    schedule: Schedule,
    target_tracks: list,
) -> None:
    """Create an auto-snapshot before a scheduled raid
    if enabled."""
    try:
        pre_raid_uris = [
            t.get("uri")
            for t in target_tracks
            if t.get("uri")
        ]
        if (
            pre_raid_uris
            and PlaylistSnapshotService
            .is_auto_snapshot_enabled(
                schedule.user_id
            )
        ):
            PlaylistSnapshotService.create_snapshot(
                user_id=schedule.user_id,
                playlist_id=(
                    schedule.target_playlist_id
                ),
                playlist_name=(
                    schedule.target_playlist_name
                    or schedule.target_playlist_id
                ),
                track_uris=pre_raid_uris,
                snapshot_type=(
                    SnapshotType.AUTO_PRE_RAID
                ),
                trigger_description=(
                    "Before scheduled raid"
                ),
            )
    except Exception as snap_err:
        logger.warning(
            "Auto-snapshot before scheduled "
            f"raid failed: {snap_err}"
        )


def _fetch_raid_sources(
    api: SpotifyAPI,
    source_ids: list,
    target_uris: set,
    user_id: Optional[int] = None,
) -> List[str]:
    """
    Fetch new tracks from source playlists not already
    in target using multi-pathway resolution.

    Returns:
        List of new track URIs (deduplicated).
    """
    resolver = SourceResolver()

    # Load full UpstreamSource records if user_id available
    sources = None
    if user_id:
        try:
            sources = UpstreamSource.query.filter(
                UpstreamSource.user_id == user_id,
            ).filter(
                db.or_(
                    UpstreamSource.source_playlist_id.in_(
                        source_ids
                    ),
                    UpstreamSource.source_type
                    == "search_query",
                )
            ).all()
            # Include any source_ids not in DB
            found_ids = {
                s.source_playlist_id
                for s in sources
                if s.source_playlist_id
            }
            for sid in source_ids:
                if sid not in found_ids:
                    sources.append(
                        UpstreamSource(
                            source_playlist_id=sid,
                            source_type="external",
                        )
                    )
        except Exception as e:
            logger.warning(
                "DB lookup for sources failed, "
                "using source_ids directly: %s",
                e,
            )
            sources = None

    if sources is None:
        sources = [
            UpstreamSource(
                source_playlist_id=sid,
                source_type="external",
            )
            for sid in source_ids
        ]

    results = resolver.resolve_all(
        sources, api, exclude_uris=target_uris
    )

    # Update tracking fields on resolved sources
    now = datetime.now(timezone.utc)
    for source, result in results.source_results:
        if source.id:  # Only update persisted sources
            source.last_resolved_at = now
            source.last_resolve_pathway = result.pathway_name
            if result.success:
                source.last_resolve_status = "success"
            elif result.partial:
                source.last_resolve_status = "partial"
            else:
                source.last_resolve_status = "failed"
    try:
        db.session.commit()
    except Exception as e:
        logger.warning(
            "Failed to update source tracking: %s", e
        )

    return results.new_uris


def _build_track_dicts(
    api: SpotifyAPI,
    uris: List[str],
) -> List[dict]:
    """
    Fetch metadata for track URIs and return dicts
    suitable for PendingRaidService.stage_tracks().
    """
    try:
        raw_tracks = api.get_tracks(uris)
    except Exception as e:
        logger.warning(
            "Could not fetch track metadata, "
            "staging with URIs only: %s",
            e,
        )
        return [{"uri": uri} for uri in uris]

    # Index by URI for fast lookup
    meta_by_uri = {}
    for t in raw_tracks:
        uri = t.get("uri")
        if uri:
            artists = [
                a.get("name", "")
                for a in t.get("artists", [])
            ]
            album = t.get("album", {})
            images = album.get("images", [])
            meta_by_uri[uri] = {
                "uri": uri,
                "name": t.get("name", "Unknown"),
                "artists": artists,
                "album_name": album.get("name", ""),
                "album_image_url": (
                    images[0]["url"] if images else ""
                ),
                "duration_ms": t.get("duration_ms"),
            }

    # Return in original order, fallback for missing
    result = []
    for uri in uris:
        if uri in meta_by_uri:
            result.append(meta_by_uri[uri])
        else:
            result.append({"uri": uri})
    return result
