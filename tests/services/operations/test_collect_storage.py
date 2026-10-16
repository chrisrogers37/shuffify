"""Required Collect/Drip database changes stay inside the caller's explicit transaction."""

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from shuffify.enums import PendingRaidStatus
from shuffify.models.db import PendingRaidTrack, ScrapedPlaylistCache, UpstreamSource, User, db
from shuffify.services.pending_raid_service import PendingRaidSelectionError
from tests.services.operations.test_crash_recovery import uri


def helpers():
    return importlib.import_module('shuffify.services.operations.executors.collect_storage')


@pytest.fixture
def owner(db_app):
    user = User(spotify_id='collector')
    db.session.add(user)
    db.session.commit()
    return user.id


def pending(user_id, value, status=PendingRaidStatus.PENDING):
    return PendingRaidTrack(user_id=user_id, target_playlist_id='target', track_uri=value,
                            track_name='Track', status=status)


def test_captures_exclude_all_historical_statuses_but_only_freeze_pending_promotion_rows(owner):
    store = helpers()
    db.session.add_all([pending(owner, uri(1)), pending(owner, uri(2), PendingRaidStatus.DISMISSED),
                        pending(owner, uri(3), PendingRaidStatus.PROMOTED)])
    db.session.commit()
    with Session(db.engine) as storage:
        assert store.capture_pending_uris(storage, owner, 'target') == (uri(1), uri(2), uri(3))
        rows = store.capture_pending_rows(storage, owner, 'target')
        assert [row.track_uri for row in rows] == [uri(1)]
        assert rows[0].status == 'pending'


def test_stage_returns_new_exact_ids_without_commit_or_reintroducing_historical_rows(owner):
    store = helpers()
    db.session.add(pending(owner, uri(1), PendingRaidStatus.DISMISSED))
    db.session.commit()
    with Session(db.engine) as storage:
        ids = store.stage_pending(storage, owner, 'target', [
            {'uri': uri(1)}, {'uri': uri(2), 'name': 'New', 'artists': ['First', 'Second'],
                             'source_playlist_id': 'upstream', 'source_name': 'Source'}, {'uri': uri(2)},
        ])
        assert len(ids) == 1
        row = storage.get(PendingRaidTrack, ids[0])
        assert row.track_artists == 'First, Second'
        assert row.source_playlist_id == 'upstream'
        assert store.stage_pending(storage, owner, 'target', [{'uri': uri(2)}]) == []
        storage.rollback()
    assert PendingRaidTrack.query.count() == 1


def test_promote_exact_captured_ids_does_not_expand_to_later_rows_and_rolls_back(owner):
    store = helpers()
    db.session.add(pending(owner, uri(1)))
    db.session.commit()
    with Session(db.engine) as storage:
        captured = store.capture_pending_rows(storage, owner, 'target')
        later = pending(owner, uri(2))
        storage.add(later)
        storage.flush()
        changed = store.promote_pending(storage, owner, 'target', captured)
        assert changed == [captured[0].id]
        assert storage.get(PendingRaidTrack, changed[0]).status == PendingRaidStatus.PROMOTED
        assert later.status == PendingRaidStatus.PENDING
        storage.rollback()
    assert PendingRaidTrack.query.one().status == PendingRaidStatus.PENDING


def test_promote_rejects_changed_uri_or_dismissed_capture_before_any_updates(owner):
    store = helpers()
    db.session.add_all([pending(owner, uri(1)), pending(owner, uri(2))])
    db.session.commit()
    with Session(db.engine) as storage:
        captured = store.capture_pending_rows(storage, owner, 'target')
        storage.get(PendingRaidTrack, captured[1].id).status = PendingRaidStatus.DISMISSED
        storage.flush()
        with pytest.raises(PendingRaidSelectionError):
            store.promote_pending(storage, owner, 'target', captured)
        assert storage.get(PendingRaidTrack, captured[0].id).status == PendingRaidStatus.PENDING


def test_source_tracking_and_scraper_cache_only_persist_with_final_receipt_commit(owner):
    store = helpers()
    source = UpstreamSource(user_id=owner, target_playlist_id='target', source_type='own',
                            source_playlist_id='upstream', raid_count=5)
    db.session.add(source)
    db.session.commit()
    now = datetime.now(timezone.utc)
    facts = [{'source_id': source.id, 'source_playlist_id': 'upstream', 'pathway': 'public_scraper',
              'status': 'partial', 'resolved_count': 7, 'resolved_at': now.isoformat()}]
    cache = [{'playlist_id': 'upstream', 'track_uris': [uri(1)], 'pathway': 'embed',
              'scraped_at': now.isoformat(), 'ttl_seconds': 3600}]
    with Session(db.engine) as storage:
        store.record_source_tracking(storage, owner, 'target', facts)
        store.apply_scraper_cache(storage, cache)
        row = storage.scalar(select(ScrapedPlaylistCache))
        assert row.track_uris == [uri(1)]
        assert row.expires_at == now + timedelta(seconds=3600)
        assert storage.get(UpstreamSource, source.id).last_track_count == 7
        storage.rollback()
    db.session.expire_all()
    assert db.session.get(UpstreamSource, source.id).last_resolved_at is None
    assert ScrapedPlaylistCache.query.count() == 0


def test_local_raid_receipt_keeps_required_staging_when_optional_activity_fails(owner, monkeypatch):
    store = helpers()

    def broken(*args, **kwargs):
        raise ValueError('optional logging unavailable')

    monkeypatch.setattr('shuffify.services.activity_log_service.ActivityLogService.log', broken)
    actions = {'pending_stage': {'target_playlist_id': 'target', 'tracks': [{'uri': uri(1)}]},
               'source_tracking': [], 'scraper_cache': [],
               'activities': [{'activity_type': 'raid', 'description': 'Collected', 'metadata': {}}]}
    with Session(db.engine) as storage:
        receipt = store.apply_raid_local(storage, owner, 'target', actions)
        assert len(receipt['staged_ids']) == 1
        assert receipt['activities'] == [{'status': 'failed', 'error_code': 'activity_log_failed'}]
        storage.commit()
    assert PendingRaidTrack.query.one().id == receipt['staged_ids'][0]


def test_local_drip_receipt_returns_exact_promoted_ids_and_lock_reconciliation(owner):
    store = helpers()
    db.session.add(pending(owner, uri(1)))
    db.session.commit()
    with Session(db.engine) as storage:
        rows = store.capture_pending_rows(storage, owner, 'target')
        actions = {'pending_promotion': {'target_playlist_id': 'target', 'rows': list(rows)},
                   'lock_reconcile': {'playlist_id': 'target', 'track_uris': [uri(1)]}}
        receipt = store.apply_drip_local(storage, owner, 'target', actions)
        assert receipt['promoted_ids'] == [rows[0].id]
        assert receipt['lock_reconcile']['status'] == 'complete'
        storage.rollback()
    assert PendingRaidTrack.query.one().status == PendingRaidStatus.PENDING


def test_optional_receipt_never_swallows_durable_control(owner, monkeypatch):
    from shuffify.services.operations.repository import LeaseLost

    store = helpers()
    error = LeaseLost('lease was lost')

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr('shuffify.services.activity_log_service.ActivityLogService.log', broken)
    actions = {'pending_stage': {'target_playlist_id': 'target', 'tracks': []}, 'source_tracking': [],
               'scraper_cache': [], 'activities': [{'activity_type': 'raid', 'description': 'Collected'}]}
    with Session(db.engine) as storage, pytest.raises(LeaseLost) as caught:
        store.apply_raid_local(storage, owner, 'target', actions)
    assert caught.value is error
