"""
Raid executor: pull new tracks from source playlists into the
raid playlist and stage them in PendingRaidTrack for provenance.

Tracks are added to the raid Spotify playlist (if linked) AND
recorded in the database. Per-source raid_count controls how
many tracks each source contributes.
"""

import logging
import random
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
from shuffify.services.raid_dedupe import (
    build_full_exclusion_set,
)

logger = logging.getLogger(__name__)


def execute_raid(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """
    Pull new tracks from source playlists, add to the raid
    playlist (if linked), and stage in PendingRaidTrack.
    """
    from shuffify.services.executors.base_executor import (
        JobExecutionError,
    )

    target_id = schedule.target_playlist_id
    source_ids = schedule.source_playlist_ids or []

    if not source_ids:
        logger.info(
            "Schedule %s: no source playlists "
            "configured, skipping raid",
            schedule.id,
        )
        target_tracks = api.get_playlist_tracks(target_id)
        return {
            "tracks_added": 0,
            "tracks_total": len(target_tracks),
        }

    try:
        exclusion_set, target_count = (
            build_full_exclusion_set(
                api, target_id, schedule.user_id
            )
        )

        _auto_snapshot_before_raid(
            schedule, api, target_id
        )

        new_uris = _fetch_raid_sources_with_limits(
            api, source_ids, exclusion_set,
            user_id=schedule.user_id,
        )

        if not new_uris:
            logger.info(
                "Schedule %s: no new tracks to add",
                schedule.id,
            )
            return {
                "tracks_added": 0,
                "tracks_total": target_count,
            }

        track_dicts = _build_track_dicts(api, new_uris)

        _add_to_raid_playlist(
            api, schedule.user_id, target_id, new_uris
        )

        staged = PendingRaidService.stage_tracks(
            user_id=schedule.user_id,
            target_playlist_id=target_id,
            tracks=track_dicts,
            source_name=schedule.target_playlist_name,
        )

        logger.info(
            "Schedule %s: staged %d tracks for "
            "review on %s",
            schedule.id,
            staged,
            schedule.target_playlist_name,
        )

        return {
            "tracks_added": staged,
            "tracks_total": target_count,
        }

    except SpotifyNotFoundError:
        raise JobExecutionError(
            "Target playlist {} not found. "
            "It may have been deleted.".format(
                target_id
            )
        )
    except SpotifyAPIError as e:
        raise JobExecutionError(
            "Spotify API error during raid: {}".format(e)
        )


def _auto_snapshot_before_raid(
    schedule: Schedule,
    api: SpotifyAPI,
    target_id: str,
) -> None:
    """Create auto-snapshots before a scheduled raid."""
    try:
        if not PlaylistSnapshotService.is_auto_snapshot_enabled(
            schedule.user_id
        ):
            return

        target_tracks = api.get_playlist_tracks(target_id)
        pre_raid_uris = [
            t.get("uri")
            for t in target_tracks
            if t.get("uri")
        ]
        if pre_raid_uris:
            PlaylistSnapshotService.create_snapshot(
                user_id=schedule.user_id,
                playlist_id=target_id,
                playlist_name=(
                    schedule.target_playlist_name
                    or target_id
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
            "raid failed: %s", snap_err
        )


def _add_to_raid_playlist(
    api, user_id, target_id, uris,
):
    """Add raided tracks to the raid Spotify playlist
    if a RaidPlaylistLink exists."""
    from shuffify.services.raid_link_service import (
        RaidLinkService,
    )

    link = RaidLinkService.get_link_for_playlist(
        user_id, target_id
    )
    if not link:
        return

    try:
        api.playlist_add_items(
            link.raid_playlist_id, uris
        )
        logger.info(
            "Added %d tracks to raid playlist %s",
            len(uris), link.raid_playlist_id,
        )
    except Exception as e:
        logger.warning(
            "Failed to add tracks to raid "
            "playlist: %s", e
        )


def _fetch_raid_sources_with_limits(
    api: SpotifyAPI,
    source_ids: list,
    exclusion_set: set,
    user_id: Optional[int] = None,
) -> List[str]:
    """
    Fetch new tracks from sources with per-source
    raid_count limits and chain-wide deduplication.
    """
    resolver = SourceResolver()

    # Load UpstreamSource records for raid_count
    sources = _load_sources(
        source_ids, user_id
    )

    results = resolver.resolve_all(
        sources, api, exclude_uris=exclusion_set
    )

    # Apply per-source raid_count limits
    all_new_uris = []

    for source, result in results.source_results:
        raid_count = source.raid_count or 5
        source_uris = (
            result.track_uris if result else []
        )

        if len(source_uris) > raid_count:
            source_uris = random.sample(
                source_uris, raid_count
            )

        all_new_uris.extend(source_uris)

    # Update tracking fields on resolved sources
    _update_source_tracking(results)

    # Deduplicate across sources
    seen = set()
    deduped = []
    for uri in all_new_uris:
        if uri not in seen and uri not in exclusion_set:
            seen.add(uri)
            deduped.append(uri)

    return deduped


def _load_sources(source_ids, user_id):
    """Load UpstreamSource records from DB or create
    ephemeral ones."""
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
                            raid_count=5,
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
                raid_count=5,
            )
            for sid in source_ids
        ]

    return sources


def _update_source_tracking(results):
    """Update tracking fields on resolved sources."""
    now = datetime.now(timezone.utc)
    for source, result in results.source_results:
        if source.id:
            source.last_resolved_at = now
            source.last_resolve_pathway = (
                result.pathway_name
            )
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
