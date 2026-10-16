"""Explicit-session Collect/Drip reads and writes; the caller owns receipt and commit."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from shuffify.enums import PendingRaidStatus
from shuffify.models.db import PendingRaidTrack, ScrapedPlaylistCache, UpstreamSource
from shuffify.services.operations.control import propagate_control
from shuffify.services.pending_raid_service import PendingRaidSelectionError

from .drip import PendingRow


def capture_pending_uris(storage, user_id, target_id):
    with storage.no_autoflush:
        return tuple(storage.scalars(select(PendingRaidTrack.track_uri).where(
            PendingRaidTrack.user_id == user_id, PendingRaidTrack.target_playlist_id == target_id,
        ).order_by(PendingRaidTrack.id)))


def capture_pending_rows(storage, user_id, target_id):
    with storage.no_autoflush:
        rows = storage.scalars(select(PendingRaidTrack).where(
            PendingRaidTrack.user_id == user_id, PendingRaidTrack.target_playlist_id == target_id,
            PendingRaidTrack.status == PendingRaidStatus.PENDING,
        ).order_by(PendingRaidTrack.id).execution_options(populate_existing=True))
        return tuple(PendingRow(row.id, row.track_uri, row.status) for row in rows)


def stage_pending(storage, user_id, target_id, tracks):
    """Deduplicate across all statuses and return identities from this same transaction."""
    values = {track['uri'] for track in tracks if track.get('uri')}
    if not values:
        return []
    existing = set(storage.scalars(select(PendingRaidTrack.track_uri).where(
        PendingRaidTrack.user_id == user_id, PendingRaidTrack.target_playlist_id == target_id,
        PendingRaidTrack.track_uri.in_(values),
    )))
    created = []
    for track in tracks:
        value = track.get('uri')
        if not value or value in existing:
            continue
        artists = track.get('artists', [])
        row = PendingRaidTrack(
            user_id=user_id, target_playlist_id=target_id, track_uri=value,
            track_name=track.get('name', 'Unknown'),
            track_artists=', '.join(artists) if isinstance(artists, list) else artists,
            track_album=track.get('album_name', ''), track_image_url=track.get('album_image_url', ''),
            track_duration_ms=track.get('duration_ms'), source_playlist_id=track.get('source_playlist_id'),
            source_name=track.get('source_name'), status=PendingRaidStatus.PENDING,
        )
        storage.add(row)
        created.append(row)
        existing.add(value)
    if created:
        storage.flush()
    return [row.id for row in created]


def promote_pending(storage, user_id, target_id, captured):
    """Promote only the frozen identities, never new rows with matching URIs."""
    captured = [asdict(row) if is_dataclass(row) else dict(row) for row in captured]
    if not captured:
        return []
    expected = {row['id']: row for row in captured}
    if len(expected) != len(captured) or any(row.get('status') != PendingRaidStatus.PENDING for row in captured):
        raise PendingRaidSelectionError('Invalid captured pending selection')
    rows = list(storage.scalars(select(PendingRaidTrack).where(
        PendingRaidTrack.id.in_(expected), PendingRaidTrack.user_id == user_id,
        PendingRaidTrack.target_playlist_id == target_id,
    ).execution_options(populate_existing=True)))
    if ({row.id for row in rows} != set(expected)
            or any(row.track_uri != expected[row.id]['track_uri']
                   or row.status not in {PendingRaidStatus.PENDING, PendingRaidStatus.PROMOTED} for row in rows)):
        raise PendingRaidSelectionError('The captured pending selection changed')
    changed, now = [], datetime.now(timezone.utc)
    for row in rows:
        if row.status == PendingRaidStatus.PENDING:
            row.status, row.resolved_at = PendingRaidStatus.PROMOTED, now
            changed.append(row.id)
    if changed:
        storage.flush()
    return sorted(changed)


def record_source_tracking(storage, user_id, target_id, facts):
    for fact in facts:
        row = storage.scalar(select(UpstreamSource).where(
            UpstreamSource.id == fact['source_id'], UpstreamSource.user_id == user_id,
            UpstreamSource.target_playlist_id == target_id,
        ).execution_options(populate_existing=True))
        if row is None or row.source_playlist_id != fact['source_playlist_id']:
            raise PendingRaidSelectionError('The captured discovery source changed')
        captured = fact.get('source', {})
        if any(getattr(row, key) != captured[key]
               for key in ('source_type', 'search_query', 'raid_count') if key in captured):
            raise PendingRaidSelectionError('The captured discovery source changed')
        row.last_resolved_at = datetime.fromisoformat(fact['resolved_at'])
        row.last_resolve_pathway, row.last_resolve_status = fact['pathway'], fact['status']
        row.last_track_count = fact['resolved_count']
    if facts:
        storage.flush()


def read_scraper_cache(storage, playlist_ids):
    if not playlist_ids:
        return {}
    with storage.no_autoflush:
        rows = storage.scalars(select(ScrapedPlaylistCache).where(
            ScrapedPlaylistCache.playlist_id.in_(playlist_ids),
            ScrapedPlaylistCache.expires_at > datetime.now(timezone.utc),
        ).execution_options(populate_existing=True))
        return {row.playlist_id: row.track_uris for row in rows}


def apply_scraper_cache(storage, updates):
    for update in updates:
        scraped_at = datetime.fromisoformat(update['scraped_at'])
        row = storage.scalar(select(ScrapedPlaylistCache).where(
            ScrapedPlaylistCache.playlist_id == update['playlist_id'],
        ).execution_options(populate_existing=True))
        if row is not None and row.scraped_at > scraped_at:
            continue
        if row is None:
            row = ScrapedPlaylistCache(playlist_id=update['playlist_id'])
            storage.add(row)
        row.track_uris = update['track_uris']
        row.track_count = len(update['track_uris'])
        row.scraped_at, row.scrape_pathway = scraped_at, update['pathway']
        row.expires_at = scraped_at + timedelta(seconds=update['ttl_seconds'])
    if updates:
        storage.flush()


def _optional(storage, callback, error_code):
    # A savepoint's implicit flush must not misclassify an unrelated mandatory failure as optional.
    storage.flush()
    try:
        with storage.begin_nested():
            return callback()
    except Exception as error:
        propagate_control(error)
        return {'status': 'failed', 'error_code': error_code}


def apply_raid_local(storage, user_id, target_id, actions):
    """Return exact receipts; repository.local_step owns the single atomic commit."""
    from shuffify.services.activity_log_service import ActivityLogService

    stage = actions['pending_stage']
    if stage['target_playlist_id'] != target_id:
        raise PendingRaidSelectionError('The staging action targets another playlist')
    ids = stage_pending(storage, user_id, target_id, stage['tracks'])
    tracking = actions.get('source_tracking', [])
    record_source_tracking(storage, user_id, target_id, tracking)
    receipt = {'staged_ids': ids,
               'pending_stage': {'status': 'complete' if ids else 'not_required', 'ids': ids,
                                  'affected_count': len(ids)},
               'source_tracking': {'status': 'complete' if tracking else 'not_required',
                                   'source_ids': [fact['source_id'] for fact in tracking]},
               'scraper_cache': [], 'activities': []}
    for update in actions.get('scraper_cache', []):
        def cache(update=update):
            apply_scraper_cache(storage, [update])
            return {'status': 'complete', 'playlist_id': update['playlist_id'],
                    'track_count': len(update['track_uris'])}

        receipt['scraper_cache'].append(_optional(storage, cache, 'source_cache_failed'))
    for action in actions.get('activities', []):
        def activity(action=action):
            row = ActivityLogService.log(user_id, action['activity_type'], action['description'], playlist_id=target_id,
                                         metadata=action.get('metadata'), session=storage, commit=False)
            return {'status': 'complete', 'id': row.id}

        receipt['activities'].append(_optional(storage, activity, 'activity_log_failed'))
    return receipt


def apply_drip_local(storage, user_id, target_id, actions):
    from shuffify.services.track_lock_service import TrackLockService

    promotion, locks = actions['pending_promotion'], actions.get('lock_reconcile')
    if promotion['target_playlist_id'] != target_id or locks and locks['playlist_id'] != target_id:
        raise PendingRaidSelectionError('The Drip action targets another playlist')
    updated = TrackLockService.update_positions_after_reorder(
        user_id, target_id, locks['track_uris'], session=storage, commit=False,
    ) if locks else None
    ids = promote_pending(storage, user_id, target_id, promotion['rows'])
    return {'promoted_ids': ids,
            'pending_promotion': {'status': 'complete' if ids else 'not_required', 'ids': ids,
                                  'affected_count': len(ids)},
            'lock_reconcile': {'status': 'complete' if locks else 'not_required', 'affected_count': updated or 0}}
