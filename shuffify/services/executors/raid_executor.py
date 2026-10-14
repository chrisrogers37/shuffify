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

from shuffify.enums import ActivityType, SnapshotType
from shuffify.models.db import Schedule, UpstreamSource, db
from shuffify.services.executors.base_executor import (
    JobExecutionError,
    verify_playlist_state,
)
from shuffify.services.live_operation_service import (
    book_not_required,
    current_operation,
    plan_tracks,
    read_tracks,
    write_tracks,
)
from shuffify.services.live_review_scope import require_reviewed
from shuffify.services.pending_raid_service import (
    PendingRaidService,
)
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)
from shuffify.services.raid_dedupe import (
    build_full_exclusion_set,
)
from shuffify.services.source_resolver import SourceResolver
from shuffify.shuffle_algorithms.utils import extract_uris
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
)

logger = logging.getLogger(__name__)


def execute_raid(schedule: Schedule, api: SpotifyAPI) -> dict:
    """
    Pull new tracks from source playlists, add to the raid
    playlist (if linked), and stage in PendingRaidTrack.
    """
    target_id = schedule.target_playlist_id
    source_ids = schedule.source_playlist_ids or []

    # Resolve the authoritative source list for this target at execution time
    # from UpstreamSource. Search-query sources live only there and are absent
    # from schedule.source_playlist_ids, so a schedule with an empty list can
    # still have real work to do. Scoping to the target prevents other
    # playlists' sources from leaking in; loading fresh avoids acting
    # on a stale denormalized list.
    sources = _load_sources(source_ids, schedule.user_id, target_id)

    if not sources:
        logger.info(
            "Schedule %s: no sources configured, " "skipping raid",
            schedule.id,
        )
        target_tracks = read_tracks(api, target_id)
        book_not_required("pending_stage", target_id)
        return {
            "tracks_added": 0,
            "tracks_total": len(target_tracks),
        }

    # --- Target-specific operations (can raise NotFound) ---
    try:
        exclusion_set, target_count = _raid_exclusion(api, target_id, schedule.user_id)

        _auto_snapshot_before_raid(schedule, api, target_id)
    except SpotifyNotFoundError:
        raise JobExecutionError("Target playlist {} not found. " "It may have been deleted.".format(target_id))
    except SpotifyAPIError as e:
        raise JobExecutionError("Spotify API error accessing target: " "{}".format(e))

    # Resolve each source independently. Healthy contributions still collect;
    # a failed source is reported after those effects have been retained.
    new_uris, provenance = _fetch_raid_sources_with_limits(
        api,
        sources,
        exclusion_set,
        user_id=schedule.user_id,
    )

    if not new_uris:
        logger.info(
            "Schedule %s: no new tracks to add",
            schedule.id,
        )
        book_not_required("pending_stage", target_id)
        return _raid_result(0, target_count)

    # --- Stage and write results ---
    try:
        track_dicts = _build_track_dicts(api, new_uris)

        # Attach per-track provenance so the pending-track inbox records the
        # actual upstream source, not the target playlist.
        for td in track_dicts:
            src = provenance.get(td.get("uri"))
            if src:
                td["source_name"], td["source_playlist_id"] = src

        _add_to_raid_playlist(
            api,
            schedule.user_id,
            target_id,
            new_uris,
            schedule.id,
        )

        operation = current_operation()
        try:
            before_ids = _pending_ids(schedule.user_id, target_id, new_uris) if operation else set()
        except Exception as error:
            db.session.rollback()
            operation.book(
                "pending_stage",
                "failed",
                resource=target_id,
                error_code="pending_stage_read_failed",
                affected_count=0,
            )
            operation.counts = {"tracks_added": 0, "tracks_total": target_count}
            operation.error_code = "pending_stage_read_failed"
            raise JobExecutionError("Collection results are retained; staging did not start") from error
        try:
            staged = PendingRaidService.stage_tracks(
                user_id=schedule.user_id,
                target_playlist_id=target_id,
                tracks=track_dicts,
            )
        except Exception:
            if operation:
                # Attempted: unknown until shown unchanged, even if the rollback itself fails.
                operation.book("pending_stage", "unknown", resource=target_id, error_code="pending_stage_failed")
                db.session.rollback()
                try:
                    unchanged = _pending_ids(schedule.user_id, target_id, new_uris) == before_ids
                except Exception:
                    unchanged = False
                operation.book(
                    "pending_stage",
                    "failed" if unchanged else "unknown",
                    resource=target_id,
                    error_code="pending_stage_failed",
                )
                operation.error_code = "pending_stage_failed"
            raise
        if operation:
            # stage_tracks returned after its commit. Preserve that known effect
            # independently of the read needed to identify compensatable rows.
            operation.book(
                "pending_stage", "complete" if staged else "not_required", resource=target_id, affected_count=staged
            )
            operation.counts = {"tracks_added": staged, "tracks_total": target_count}
            if staged:
                try:
                    staged_ids = _pending_ids(schedule.user_id, target_id, new_uris) - before_ids
                    if len(staged_ids) != staged:
                        raise JobExecutionError("The staged row identities could not be verified")
                except Exception as error:
                    operation.book(
                        "pending_stage_identity",
                        "unknown",
                        resource=target_id,
                        error_code="pending_stage_identity_unverified",
                    )
                    operation.error_code = "pending_stage_identity_unverified"
                    # Stop the composite. Do not compensate without exact IDs,
                    # and do not interpret a failed identity read as no staging.
                    raise JobExecutionError(
                        "Collected tracks were staged; their row identities need checking"
                    ) from error
                operation.staged_ids.extend(staged_ids)

        logger.info(
            "Schedule %s: staged %d tracks for " "review on %s",
            schedule.id,
            staged,
            schedule.target_playlist_name,
        )

        return _raid_result(staged, target_count)

    except SpotifyPartialBatchError:
        # Half-written: the job runner restores this job's snapshots.
        raise
    except SpotifyNotFoundError:
        raise JobExecutionError("Raid playlist not found when writing " "tracks. It may have been deleted.")
    except SpotifyAPIError as e:
        raise JobExecutionError("Spotify API error during raid: " "{}".format(e))


def _raid_result(tracks_added, tracks_total):
    """Stop dependent steps after preserving healthy collection and staging."""
    result = {"tracks_added": tracks_added, "tracks_total": tracks_total}
    operation = current_operation()
    if operation and any(
        item["kind"] == "source_resolution" and item["status"] == "failed"
        for item in operation.bookkeeping
    ):
        operation.counts = result
        operation.error_code = "source_resolution_failed"
        # Ordinary failure deliberately avoids compensating verified healthy
        # source effects. The runner retains them and marks later steps unstarted.
        raise JobExecutionError("Some discovery sources could not be read; collected tracks were retained")
    return result


def _auto_snapshot_before_raid(
    schedule: Schedule,
    api: SpotifyAPI,
    target_id: str,
) -> None:
    """Create auto-snapshots before a scheduled raid."""
    try:
        target_tracks = read_tracks(api, target_id)
    except Exception as snap_err:
        logger.warning("Auto-snapshot before scheduled " "raid failed: %s", snap_err)
        return

    PlaylistSnapshotService.auto_snapshot_if_enabled(
        user_id=schedule.user_id,
        playlist_id=target_id,
        playlist_name=(schedule.target_playlist_name or target_id),
        track_uris=extract_uris(target_tracks or []),
        snapshot_type=SnapshotType.AUTO_PRE_RAID,
        trigger_description="Before scheduled raid",
    )


def _auto_snapshot_raid_playlist(user_id, raid_playlist_id, uris):
    """Snapshot the raid playlist before writing to it, so rollback can
    restore it if post-write verification fails.
    """
    PlaylistSnapshotService.auto_snapshot_if_enabled(
        user_id=user_id,
        playlist_id=raid_playlist_id,
        playlist_name="Raid playlist",
        track_uris=uris,
        snapshot_type=SnapshotType.AUTO_PRE_RAID,
        trigger_description="Before scheduled raid (raid playlist)",
    )


def _add_to_raid_playlist(
    api,
    user_id,
    target_id,
    uris,
    schedule_id,
):
    """Add raided tracks to the raid Spotify playlist
    if a RaidPlaylistLink exists, and verify the resulting
    state. Any HTTP failure or post-write divergence
    propagates so the orchestrator can fail/rollback.
    """
    from shuffify.services.raid_link_service import (
        RaidLinkService,
    )

    link = RaidLinkService.get_link_for_playlist(user_id, target_id)
    # A reviewed run never writes to a collection other than the reviewed one.
    require_reviewed("link", link)
    if not link:
        return
    if current_operation() and link.raid_playlist_id == target_id:
        raise JobExecutionError("The collection must be distinct from the target")

    # The raid playlist is distinct from `target_id`, so its
    # contents aren't fetched anywhere upstream. Read here
    # to build the post-write expected URI set.
    prev_raid_tracks = read_tracks(api, link.raid_playlist_id)
    prev_raid_uris = extract_uris(prev_raid_tracks or [])

    # Snapshot the raid playlist before writing, so a post-write verification
    # failure can be rolled back — the raid playlist is distinct from the
    # target and not captured elsewhere, so it needs its own snapshot.
    # Mirrors the drip executor, which snapshots both.
    _auto_snapshot_raid_playlist(user_id, link.raid_playlist_id, prev_raid_uris)

    plan_tracks(api, link.raid_playlist_id, prev_raid_uris + list(uris), step="raid")
    write_tracks(api, "playlist_add_items", link.raid_playlist_id, uris, step="raid")
    logger.info(
        "Added %d tracks to raid playlist %s",
        len(uris),
        link.raid_playlist_id,
    )

    expected_raid = prev_raid_uris + list(uris)
    verify_playlist_state(
        api,
        link.raid_playlist_id,
        expected_raid,
        schedule_id,
        "raid pull",
    )


def _fetch_raid_sources_with_limits(
    api: SpotifyAPI,
    sources: list,
    exclusion_set: set,
    user_id: Optional[int] = None,
) -> List[str]:
    """
    Resolve the given UpstreamSource records and apply per-source
    raid_count limits with chain-wide deduplication.

    ``sources`` is the already-loaded list of UpstreamSource records for the
    target (see ``_load_sources``). The caller owns loading so the skip
    decision and the resolution operate on the same authoritative set.
    """
    resolver = SourceResolver()

    results = resolver.resolve_all(sources, api, exclude_uris=exclusion_set)

    # Apply per-source raid_count limits
    all_new_uris = []
    failed_sources = []
    # Map each fresh URI to the source it came from (first source wins), so
    # staged tracks record correct provenance -- the source, not the target.
    provenance = {}

    for source, result in results.source_results:
        if result is None:
            from shuffify.services.source_resolver.base import ResolveResult

            result = ResolveResult(
                track_uris=[], pathway_name="unavailable", success=False,
                error_message="Source resolution result unavailable",
            )
        if result and not result.success:
            # Surface failed resolves so "raid does nothing"
            # is debuggable. Happy path stays quiet — only
            # success=False triggers this log line.
            logger.warning(
                "Raid source resolution failed: "
                "source_id=%s source_playlist_id=%s "
                "source_name=%s pathway=%s partial=%s "
                "error=%s",
                getattr(source, "id", None),
                getattr(source, "source_playlist_id", None),
                getattr(source, "source_name", None),
                result.pathway_name,
                result.partial,
                result.error_message,
            )
            _log_failed_source_activity(
                user_id=user_id,
                source=source,
                result=result,
            )
            failed_sources.append({
                "source_id": getattr(source, "id", None),
                "source_playlist_id": getattr(source, "source_playlist_id", None),
                "status": "failed",
                "error_code": "source_resolution_incomplete" if result.partial else "source_resolution_failed",
            })

        raid_count = source.raid_count or 5
        source_uris = result.track_uris if result else []

        # Filter against exclusion BEFORE sampling. Sampling raid_count
        # from the raw source first collapses yield to near-zero when the
        # source heavily overlaps the user's catalog, as a slow-rotation
        # editorial source does once most of it has been raided. For
        # example, raid_count=10 from a 100-URI source with 94% overlap
        # samples 10 * 6/100 = 0.6 new tracks per run on average.
        # Filtering first makes raid_count mean "up to N *new* tracks per
        # run", matching user intent.
        fresh_uris = [uri for uri in source_uris if uri not in exclusion_set]

        if len(fresh_uris) > raid_count:
            fresh_uris = random.sample(fresh_uris, raid_count)

        all_new_uris.extend(fresh_uris)
        for uri in fresh_uris:
            provenance.setdefault(
                uri,
                (
                    getattr(source, "source_name", None),
                    getattr(source, "source_playlist_id", None),
                ),
            )

    # Update tracking fields on resolved sources
    _update_source_tracking(results)
    operation = current_operation()
    if operation and failed_sources:
        operation.book(
            "source_resolution", "failed", error_code="source_resolution_failed",
            failed_sources=failed_sources,
        )

    # Deduplicate across sources
    seen = set()
    deduped = []
    for uri in all_new_uris:
        if uri not in seen and uri not in exclusion_set:
            seen.add(uri)
            deduped.append(uri)

    return deduped, provenance


def _load_sources(source_ids, user_id, target_id):
    """Load the UpstreamSource records for a raid, scoped to the target.

    Returns every persisted source for (user_id, target_id) — both playlist
    and search_query types — plus ephemeral 'external' sources for any
    source_ids not yet persisted. Scoping by target_playlist_id keeps a
    user's search-query sources on one playlist from leaking into another
    target's raid, and surfaces search sources that the schedule's
    denormalized source_playlist_ids list omits.
    """
    sources = None
    if user_id and target_id:
        try:
            sources = UpstreamSource.query.filter(
                UpstreamSource.user_id == user_id,
                UpstreamSource.target_playlist_id == target_id,
            ).all()
            # Include any source_ids not already persisted for this target
            found_ids = {s.source_playlist_id for s in sources if s.source_playlist_id}
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
            if current_operation():
                raise JobExecutionError("Discovery sources are unavailable") from e
            logger.warning(
                "DB lookup for sources failed, " "using source_ids directly: %s",
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


def _log_failed_source_activity(user_id, source, result):
    """Write an ActivityLog entry for a failed source resolve.

    Non-blocking — ActivityLogService.log already swallows
    its own exceptions, but we wrap defensively so even an
    import error can't escape into the raid loop.
    """
    if not user_id:
        return
    try:
        from shuffify.services.activity_log_service import (
            ActivityLogService,
        )

        source_name = (
            getattr(source, "source_name", None) or getattr(source, "source_playlist_id", None) or "unknown source"
        )
        ActivityLogService.log(
            user_id=user_id,
            activity_type=(ActivityType.RAID_SOURCE_RESOLVE_FAILED),
            description=(f"Raid source '{source_name}' failed to " f"resolve via {result.pathway_name}"),
            metadata={
                "source_id": getattr(source, "id", None),
                "source_playlist_id": getattr(source, "source_playlist_id", None),
                "source_name": getattr(source, "source_name", None),
                "pathway": result.pathway_name,
                "partial": result.partial,
                "error_message": result.error_message,
            },
        )
    except Exception as e:
        logger.debug(
            "Failed to log raid source failure activity: %s",
            e,
        )


def _update_source_tracking(results):
    """Update tracking fields on resolved sources."""
    now = datetime.now(timezone.utc)
    for source, result in results.source_results:
        if source.id:
            source.last_resolved_at = now
            source.last_resolve_pathway = result.pathway_name if result else "unavailable"
            if result and result.success:
                source.last_resolve_status = "success"
            elif result and result.partial:
                source.last_resolve_status = "partial"
            else:
                source.last_resolve_status = "failed"
    try:
        db.session.commit()
    except Exception as e:
        logger.warning("Failed to update source tracking: %s", e)


# Track metadata is fetched one request per track (GET /tracks/{id}). A raid
# can stage hundreds of tracks (raid_count up to 100, per source), so beyond
# this many the rest are staged as bare URIs rather than hold a raid-now
# request open or spend the app's rate limit, which every user shares.
MAX_METADATA_LOOKUPS = 50


def _build_track_dicts(
    api: SpotifyAPI,
    uris: List[str],
) -> List[dict]:
    """
    Fetch metadata for track URIs and return dicts
    suitable for PendingRaidService.stage_tracks().

    Only the first MAX_METADATA_LOOKUPS are looked up; the rest,
    and any Spotify cannot return, are staged with the URI alone.
    """
    try:
        raw_tracks = api.get_tracks(uris[:MAX_METADATA_LOOKUPS])
    except Exception as e:
        logger.warning(
            "Could not fetch track metadata, " "staging with URIs only: %s",
            e,
        )
        raw_tracks = []

    # Index by URI for fast lookup
    meta_by_uri = {}
    for t in raw_tracks:
        uri = t.get("uri")
        if uri:
            artists = [a.get("name", "") for a in t.get("artists", [])]
            album = t.get("album", {})
            images = album.get("images", [])
            meta_by_uri[uri] = {
                "uri": uri,
                "name": t.get("name", "Unknown"),
                "artists": artists,
                "album_name": album.get("name", ""),
                "album_image_url": (images[0]["url"] if images else ""),
                "duration_ms": t.get("duration_ms"),
            }

    # In the original order; the URI alone where there is no metadata.
    return [meta_by_uri.get(uri, {"uri": uri}) for uri in uris]


def _pending_ids(user_id, target_id, candidate_uris):
    from shuffify.models.db import PendingRaidTrack

    return {
        row.id
        for row in PendingRaidTrack.query.filter(
            PendingRaidTrack.user_id == user_id,
            PendingRaidTrack.target_playlist_id == target_id,
            PendingRaidTrack.track_uri.in_(candidate_uris),
        ).all()
    }


def _raid_exclusion(api, target_id, user_id):
    """Live runs fail closed rather than silently losing an exclusion read."""
    if not current_operation():
        return build_full_exclusion_set(api, target_id, user_id)
    from shuffify.models.db import PendingRaidTrack
    from shuffify.services.playlist_pair_service import PlaylistPairService
    from shuffify.services.raid_link_service import RaidLinkService

    target = read_tracks(api, target_id)
    uris = set(extract_uris(target))
    link = RaidLinkService.get_link_for_playlist(user_id, target_id)
    pair = PlaylistPairService.get_pair_for_playlist(user_id, target_id)
    require_reviewed("link", link)
    require_reviewed("pair", pair)
    for source_id in (link.raid_playlist_id if link else None, pair.archive_playlist_id if pair else None):
        if source_id:
            uris.update(extract_uris(read_tracks(api, source_id)))
    uris.update(
        row.track_uri
        for row in PendingRaidTrack.query.filter_by(
            user_id=user_id,
            target_playlist_id=target_id,
        ).all()
    )
    return uris, len(target)
