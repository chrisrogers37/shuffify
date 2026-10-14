"""Routines page: named review, guarded navigation, and exact state controls."""

from tests.page_runtime import render_page, run_page
from tests.test_playlist_operations_runtime import node as node  # noqa: F401
from tests.test_routine_management_runtime import row


def page(db_app):
    return render_page(
        db_app, "schedules.html", path="/schedules",
        routines=[row(4, "target", "rotate", False), row(5, "target", "shuffle")],
        playlists=[{"id": "target", "name": "Target"}],
    )


START = r"""
const reply = data => ({ok: true, status: 200, json: async () => data});
const SCOPE = '1'.repeat(64);
const reviewContext = {version: 1, kind: 'schedule', label: 'Run routine now',
    target: {id: 'target', name: 'Target'}, schedule: {id: 5, job_type: 'shuffle'}, snapshot: null,
    resources: [{resource: 'target', resource_name: 'Target', role: 'target', domain: 'spotify',
        access: 'write', mutation_type: 'track_order', field: null}],
    steps: ['shuffle'], required_bookkeeping: ['execution_record'],
    review_binding: {version: 1, scope: SCOPE, before_order: null, history: null},
    current: {}, action: {job_type: 'shuffle', algorithm: {id: 'BasicShuffle', name: 'Basic'},
        params: {}, rotation: null, drip: null}};
const page = loadPage(TREE, {path: '/schedules', route(url) {
    if (url.startsWith('/schedules/latest-results')) return reply({success: true, results: {}});
    if (url.endsWith('/routines/context')) return reply({success: true, capabilities: {}});
}});
await settle();
const card = id => page.$('.schedule-card[data-schedule-id="' + id + '"]');
"""


def test_run_reviews_exact_saved_routine_and_dispatches_once(node, db_app):
    run_page(node, page(db_app), START + r"""
page.replies.push(page.reply({success: true, context: reviewContext}));
card(5).querySelector('.routine-run').click();
await settle();
assert.equal(page.calls.find(call => call.url === '/schedules/5/run-context').method, 'GET');
assert.match(page.text(page.byId('live-operation-review')), /Shuffle playlist/);
assert.equal(page.calls.filter(call => call.method !== 'GET').length, 0);
page.replies.push(page.reply({success: true, result: {status: 'success', execution_id: 44,
    started_at: '2026-10-12T08:30:00Z', execution_state: 'recorded'},
    operation: {kind: 'shuffle', aggregate_status: 'complete', retryable: false,
        resources: [{resource: 'target', resource_name: 'Target', step: 'shuffle',
            mutation_type: 'track_order', status: 'complete', dispatched: true,
            affected_count: 2, retryable: false, verification: 'ordered'}],
        bookkeeping: [{kind: 'execution_record', step: 'run', status: 'complete'}]}}));
page.byId('live-operation-run').click();
await settle();
const writes = page.calls.filter(call => call.method !== 'GET');
assert.equal(writes.length, 1); assert.equal(writes[0].url, '/schedules/5/run');
assert.deepEqual(JSON.parse(writes[0].body), {reviewed_scope: SCOPE});
assert.equal(page.calls.filter(call => call.url === '/schedules/latest-results').length, 2);
assert.match(page.text(card(4)), /Paused/);
""")


def test_pause_is_available_while_another_routine_has_unresolved_attention(node, db_app):
    run_page(node, page(db_app), START + r"""
page.replies.push(page.reply({success: true, context: reviewContext}));
card(5).querySelector('.routine-run').click(); await settle();
page.replies.push(() => { throw new TypeError('lost reply'); });
page.byId('live-operation-run').click(); await settle();
assert.equal(page.window.PlaylistOperations.hasAttention(), true);
page.byId('live-operation-back').click(); await settle();
const reopen = page.byId('live-operation-open');
assert.equal(reopen.hidden, false);
assert.match(page.text(reopen), /View live action attention/);
reopen.click(); await settle();
assert.equal(page.byId('live-operation-panel').hidden, false);
page.byId('live-operation-back').click(); await settle();
assert.equal(page.document.activeElement, reopen);
page.replies.push(page.reply({success: true, schedule_id: 4, schedule_revision: 'new-four',
    schedule: {id: 4, target_playlist_id: 'target', target_playlist_name: 'Target',
        job_type: 'rotate', is_enabled: true}, timing: {text: 'Every day'},
    registration: {status: 'registered'}, state: 'Active'}));
card(4).querySelector('.routine-toggle').click(); await settle();
const state = page.calls.find(call => call.url === '/routines/4/state');
assert.ok(state); assert.deepEqual(JSON.parse(state.body),
    {desired_enabled: true, expected_revision: 'revision-4'});
assert.match(page.text(card(4).querySelector('.routine-status')), /Active/);
""")


def test_unknown_or_unowned_deep_link_never_falls_back_to_first_card(node, db_app):
    run_page(node, page(db_app), r"""
const reply = data => ({ok: true, status: 200, json: async () => data});
const page = loadPage(TREE, {path: '/schedules?routine_id=999&view=run', route(url) {
    if (url.startsWith('/schedules/latest-results')) return reply({success: true, results: {}});
    if (url.endsWith('/routines/context')) return reply({success: true, capabilities: {}});
}});
await settle();
assert.equal(page.byId('routine-deep-link-error').hidden, false);
assert.equal(page.calls.filter(call => call.method !== 'GET').length, 0);
assert.equal(page.$('.schedule-card[data-schedule-id="4"]').getAttribute('aria-busy'), null);
""")
