"""Activity and exact History use the same shipped, safe result renderer."""

from datetime import datetime, timezone
from types import SimpleNamespace

from shuffify.services.execution_result_service import build_execution_detail, serialize_execution_detail
from tests.page_runtime import render_page, run_page
from tests.test_playlist_operations_runtime import node as node  # noqa: F401


def row(identity, detail, *, status="success", name="Target playlist"):
    return {
        "id": identity, "schedule_id": 4, "started_at": "2026-10-13T09:00:00+00:00",
        "completed_at": "2026-10-13T09:01:00+00:00", "status": status,
        "tracks_added": 99, "tracks_total": 120, "error_message": None,
        "job_type": "raid_and_drip", "schedule_name": name, "result_detail": detail,
    }


def detail(identity, *, operation="raid_and_drip", outcome="complete", steps=None, **extra):
    result = {
        "detail_status": "available", "version": 1, "operation": operation,
        "execution_id": identity, "outcome": outcome, "run_state": "finished",
        "steps": steps or [
            {"step": "raid", "status": "complete", "counts": {"tracks_collected": 12},
             "resources": [{"kind": "collection", "resource": "collection", "status": "complete",
                            "dispatched": True, "affected_count": 12}], "bookkeeping": []},
            {"step": "drip", "status": "complete", "counts": {"tracks_moved_to_target": 3,
                                                            "tracks_removed_from_collection": 3},
             "resources": [], "bookkeeping": []},
        ],
    }
    result.update(extra)
    return result


def page(db_app, executions, activities=None):
    return render_page(
        db_app, "activity.html", path="/activity",
        stats={"total_shuffles": 2, "active_schedule_count": 1,
               "total_scheduled_runs": 5, "total_snapshots": 4},
        executions=executions,
        activities=activities or [],
    )


def test_ordered_composite_and_unknown_recovery_have_factual_counts(node, db_app):
    failed = detail(8, outcome="partial", steps=[
        {"step": "raid", "status": "complete", "counts": {"tracks_collected": 12},
         "resources": [], "bookkeeping": []},
        {"step": "drip", "status": "unknown", "counts": {},
         "resources": [{"kind": "target", "resource": "target", "status": "unknown", "dispatched": True},
                       {"kind": "collection", "resource": "collection", "status": "restored",
                        "dispatched": True}], "bookkeeping": []},
    ], execution_record="unknown")
    run_page(node, page(db_app, [row(8, failed, status="failed")]), """
loadPage(TREE, {path: '/activity'});
const rendered = page.text(page.byId('activity-runs'));
assert.match(rendered, /Collect — Completed/);
assert.match(rendered, /12 collected for review/);
assert.match(rendered, /Add to playlist — Could not be verified/);
assert.match(rendered, /Target playlist: Changes could not be verified/);
assert.match(rendered, /Linked collection: Restoration verified/);
assert.match(rendered, /Run history could not be verified/);
assert.doesNotMatch(rendered, /99 tracks|99 added to target/);
assert.deepEqual(page.errors, []);
""")


def test_running_zero_legacy_and_mismatched_identity(node, db_app):
    running = detail(1, outcome="partial", run_state="running", steps=[
        {"step": "raid", "status": "complete", "counts": {"tracks_collected": 0},
         "resources": [], "bookkeeping": []},
        {"step": "drip", "status": "pending", "counts": {}, "resources": [], "bookkeeping": []},
    ])
    not_started = detail(4, outcome="partial", operation="rotate", steps=[
        {"step": "rotate", "status": "not_started", "counts": {"tracks_archived": 0},
         "resources": [], "bookkeeping": []},
    ])
    no_detail = {"detail_status": "unavailable", "version": None, "steps": None}
    run_page(node, page(db_app, [row(1, running, status="running"),
                                 row(2, no_detail, status="failed_rolled_back"),
                                 row(3, detail(99), status="success"),
                                 row(4, not_started, status="skipped")]), """
loadPage(TREE, {path: '/activity'});
const results = page.$$('.sh-activity-result').map(el => page.text(el));
assert.match(results[0], /In progress/);
assert.match(results[0], /No new tracks collected/);
assert.match(results[0], /Add to playlist — Not started yet/);
assert.doesNotMatch(results[0], /No tracks added to target/);
assert.match(results[1], /Run failed/);
assert.match(results[1], /Step details unavailable/);
assert.doesNotMatch(results[1], /Restoration verified|99/);
assert.match(results[2], /Step details unavailable/);
assert.doesNotMatch(results[2], /12 collected for review/);
assert.match(results[3], /Rotate — Did not start/);
assert.doesNotMatch(results[3], /No tracks moved to archive/);
assert.deepEqual(page.errors, []);
""")


def test_legacy_statuses_and_malformed_detail_keep_factual_labels(node, db_app):
    unavailable = {"detail_status": "unavailable", "version": None, "steps": None}
    malformed = {"detail_status": "available", "version": 1, "operation": "raid",
                 "execution_id": "wrong", "outcome": "complete", "steps": [
                     {"step": "raid", "status": "complete", "counts": {"tracks_collected": 99},
                      "resources": [], "bookkeeping": []},
                 ]}
    statuses = [
        ("success", "Completed"), ("failed", "Run failed"),
        ("running", "In progress"), ("skipped", "Skipped"),
        ("failed_rolled_back", "Run failed; rollback reported"),
        ("partial", "Partly completed"),
        ("unknown", "Needs review — result could not be verified"),
        ("unrecognized_status", "Run status unavailable"),
    ]
    executions = [row(index, malformed if status == "unknown" else unavailable, status=status)
                  for index, (status, _) in enumerate(statuses, start=20)]
    html = page(db_app, executions)
    for _, label in statuses:
        assert f"<p>{label}</p>" in html  # No-JavaScript fallback agrees with the renderer.
    run_page(node, html, """
loadPage(TREE, {path: '/activity'});
const labels = page.$$('.sh-activity-result').map(slot => slot.querySelector('.sh-result-heading').textContent);
assert.deepEqual(labels, ['Completed', 'Run failed', 'In progress', 'Skipped',
    'Run failed; rollback reported', 'Partly completed',
    'Needs review — result could not be verified', 'Run status unavailable']);
page.$$('.sh-activity-result').forEach(slot => {
    assert.match(page.text(slot), /Step details unavailable/);
    assert.doesNotMatch(page.text(slot), /Restoration verified|99 collected for review|99 added to target/);
});
assert.deepEqual(page.errors, []);
""")


def test_activity_filters_only_loaded_rows_and_keeps_manual_events(node, db_app):
    manual = SimpleNamespace(activity_type="schedule_run", description="Manual collection confirmed",
                             playlist_name="Target", metadata_json={"triggered_by": "manual", "schedule_id": None},
                             created_at=datetime(2026, 10, 13, 9, tzinfo=timezone.utc))
    scheduled = SimpleNamespace(activity_type="schedule_create", description="Activated routine",
                                playlist_name="Target", metadata_json={"schedule_id": 4},
                                created_at=datetime(2026, 10, 12, 9, tzinfo=timezone.utc))
    run_page(node, page(db_app, [row(1, detail(1)), row(2, {"detail_status": "unavailable"})],
                        [manual, scheduled]), """
loadPage(TREE, {path: '/activity'});
assert.match(page.text(page.$('.sh-activity-stats')), /Enabled routines/);
assert.match(page.text(page.$('.sh-activity-stats')), /Routine runs/);
assert.match(page.text(page.byId('activity-events')), /Manual action/);
assert.match(page.text(page.byId('activity-events')), /Oct 13, 09:00 UTC/);
assert.match(page.text(page.byId('activity-run-count')), /2 of 2 loaded runs/);
const runFilter = page.byId('activity-run-filter');
runFilter.value = 'unavailable'; runFilter.dispatchEvent(new Event('change', {bubbles: true}));
assert.match(page.text(page.byId('activity-run-count')), /1 of 2 loaded runs/);
assert.equal(page.$$('.sh-activity-run').filter(row => !row.hidden).length, 1);
runFilter.value = 'all'; runFilter.dispatchEvent(new Event('change', {bubbles: true}));
const operationFilter = page.byId('activity-operation-filter');
operationFilter.value = 'rotate'; operationFilter.dispatchEvent(new Event('change', {bubbles: true}));
assert.match(page.text(page.byId('activity-run-count')), /0 of 2 loaded runs/);
assert.equal(page.byId('activity-run-no-match').hidden, false);
operationFilter.value = 'raid_and_drip'; operationFilter.dispatchEvent(new Event('change', {bubbles: true}));
assert.match(page.text(page.byId('activity-run-count')), /2 of 2 loaded runs/);
const eventFilter = page.byId('activity-event-filter');
eventFilter.value = 'manual'; eventFilter.dispatchEvent(new Event('change', {bubbles: true}));
assert.match(page.text(page.byId('activity-event-count')), /1 of 2 loaded events/);
assert.equal(page.$$('.sh-activity-event').filter(row => !row.hidden).length, 1);
assert.match(page.text(page.byId('activity-filter-status')), /loaded events shown/);
assert.deepEqual(page.errors, []);
""")


def test_activity_uses_human_operation_and_manual_event_names(node, db_app):
    operation_names = [
        ("raid", "Collect for review"),
        ("shuffle", "Reorder existing tracks"),
        ("raid_and_shuffle", "Collect then reorder"),
        ("raid_and_drip", "Collect then add to playlist"),
        ("drip", "Add collected tracks"),
        ("rotate", "Rotate with archive"),
    ]
    executions = [dict(row(index, {"detail_status": "unavailable"}), job_type=operation)
                  for index, (operation, _) in enumerate(operation_names, start=1)]
    opaque_id = "0123456789abcdefghijAB"
    manual = SimpleNamespace(
        activity_type="schedule_run", description="Manual raid_and_drip: playlist restoration verified",
        playlist_id=opaque_id, playlist_name=opaque_id,
        metadata_json={"triggered_by": "manual"}, created_at=None,
    )
    unrelated = SimpleNamespace(
        activity_type="schedule_run", description="Manual raid_and_drip completed elsewhere",
        playlist_id="same-shape", playlist_name="same-shape-ish human name",
        metadata_json={"triggered_by": "manual"}, created_at=None,
    )
    hostile = SimpleNamespace(
        activity_type="schedule_run", description="Manual raid: <script>alert(1)</script>",
        playlist_id="another-id", playlist_name=None,
        metadata_json={"triggered_by": "manual"}, created_at=None,
    )
    html = page(db_app, executions, [manual, unrelated, hostile])
    assert '<script>alert(1)</script>' not in html
    run_page(node, html, r"""
loadPage(TREE, {path: '/activity'});
assert.deepEqual(page.$$('.sh-activity-run .sh-badge').map(el => page.text(el)),
    ['Collect for review', 'Reorder existing tracks', 'Collect then reorder',
     'Collect then add to playlist', 'Add collected tracks', 'Rotate with archive']);
const events = page.$$('.sh-activity-event');
assert.match(page.text(events[0]), /Manual Collect then add to playlist: playlist restoration verified/);
assert.match(page.text(events[0]), /Playlist name unavailable/);
assert.doesNotMatch(page.text(events[0]), /0123456789abcdefghijAB/);
assert.match(page.text(events[1]), /Manual raid_and_drip completed elsewhere/);
assert.match(page.text(events[1]), /same-shape-ish human name/);
assert.match(page.text(events[2]), /Manual Collect for review: <script>alert\(1\)<\/script>/);
assert.equal(events[2].querySelector('script'), null);
assert.equal(page.$$('select.sh-input').length, 3);
assert.deepEqual(page.errors, []);
""")


def test_serialized_bookkeeping_keeps_verified_collection_and_failed_staging_separate(node, db_app):
    stored = build_execution_detail(
        operation="raid_and_drip", execution_id=10, outcome="partial",
        steps=[
            {"step": "raid", "status": "partial", "counts": {},
             "resources": [{"kind": "collection", "resource": "collection-id",
                            "status": "complete", "dispatched": True}],
             "bookkeeping": [{"kind": "pending_stage", "status": "failed",
                              "resource": "target-id"}]},
            {"step": "drip", "status": "not_started", "counts": {},
             "resources": [], "bookkeeping": []},
        ],
    )
    public = serialize_execution_detail(stored)
    assert public["detail_status"] == "available"
    run_page(node, page(db_app, [row(10, public, status="failed")]), """
loadPage(TREE, {path: '/activity'});
const slot = page.$('.sh-activity-result');
const text = page.text(slot);
assert.match(text, /Collect — Partly completed/);
assert.match(text, /Linked collection: Verified/);
assert.match(text, /Review inbox staging: Failed; no change confirmed/);
assert.match(text, /Add to playlist — Did not start/);
assert.doesNotMatch(text, /No new tracks collected|99 added to target/);
page.window.RoutineResults.renderExecution(slot, JSON.parse(page.$('.sh-activity-run').dataset.execution),
    {density: 'compact'});
assert.match(page.text(slot), /Review inbox staging: Failed; no change confirmed/);
assert.deepEqual(page.errors, []);
""")


def test_serialized_promotion_restore_and_run_record_are_scoped(node, db_app):
    stored = build_execution_detail(
        operation="drip", execution_id=11, outcome="unknown", execution_record="unknown",
        steps=[{"step": "drip", "status": "unknown", "counts": {},
                "resources": [{"kind": "target", "resource": "target-id", "status": "restored",
                               "dispatched": True}],
                "bookkeeping": [
                    {"kind": "pending_promotion", "status": "unknown", "resource": "target-id"},
                    {"kind": "pending_restore", "status": "complete", "resource": "target-id"},
                    {"kind": "execution_record", "status": "unknown"},
                ]}],
    )
    public = serialize_execution_detail(stored)
    run_page(node, page(db_app, [row(11, public, status="failed")]), """
loadPage(TREE, {path: '/activity'});
const text = page.text(page.$('.sh-activity-result'));
assert.match(text, /Target playlist: Restoration verified/);
assert.match(text, /Review inbox promotion: Change could not be verified/);
assert.match(text, /Review inbox restoration: Restoration verified/);
assert.match(text, /Run history: Save could not be verified/);
assert.match(text, /Run history could not be verified/);
assert.deepEqual(page.errors, []);
""")


def test_shared_renderer_escapes_untrusted_names_and_is_read_only(node, db_app):
    hostile = '<img src=x onerror=alert(1)>'
    html = page(db_app, [row(7, detail(7), name=hostile)])
    assert '&lt;img' in html
    run_page(node, html, """
loadPage(TREE, {path: '/activity'});
const slot = page.$('.sh-activity-result');
const before = page.text(slot);
page.window.RoutineResults.renderExecution(slot, {
    id: 7, status: 'success', started_at: '2026-10-13T09:00:00Z',
    result_detail: {detail_status:'available', version:1, operation:'shuffle', execution_id:7,
        outcome:'complete', steps:[{step:'shuffle', status:'complete', counts:{tracks_reordered:0},
        resources:[{resource:'<svg onload=alert(1)>', kind:'target', status:'complete'}]}]}
}, {density:'compact'});
assert.match(page.text(slot), /Order unchanged/);
assert.match(page.text(slot), /Target playlist: Verified/);
assert.equal(slot.querySelector('svg'), null);
assert.equal(page.calls.length, 0);
assert.notEqual(page.text(slot), before);
assert.deepEqual(page.errors, []);
""")


def test_rotate_resource_phases_and_bounded_counts_remain_separate(node, db_app):
    rotated = detail(5, operation="rotate", steps=[
        {"step": "rotate", "status": "restored",
         "counts": {"archive_overlaps_removed": 2, "tracks_archived": 5,
                    "tracks_returned": 3, "tracks_removed_from_target": 5,
                    "tracks_removed_from_archive": 3},
         "resources": [
             {"kind": "archive", "phase": "archive_purge", "resource": "archive",
              "status": "complete", "dispatched": True},
             {"kind": "target", "resource": "target", "status": "restored", "dispatched": True},
         ], "bookkeeping": []},
    ])
    run_page(node, page(db_app, [row(5, rotated)]), """
loadPage(TREE, {path: '/activity'});
const rendered = page.text(page.$('.sh-activity-result'));
assert.match(rendered, /Rotate — Restoration verified/);
assert.match(rendered, /2 overlaps removed from archive/);
assert.match(rendered, /5 moved to archive/);
assert.match(rendered, /3 returned to target/);
assert.match(rendered, /5 removed from target/);
assert.match(rendered, /3 removed from archive/);
assert.match(rendered, /Archive cleanup: Verified/);
assert.match(rendered, /Target playlist: Restoration verified/);
assert.doesNotMatch(rendered, /net|12 tracks/);
assert.deepEqual(page.errors, []);
""")
