"""Exact-ID management behavior on the rendered Routines page."""

from tests.page_runtime import render_page, run_page
from tests.test_playlist_operations_runtime import node as node  # noqa: F401


def row(schedule_id, target, job_type, enabled=True):
    return {
        "schedule_id": schedule_id,
        "schedule": {
            "id": schedule_id, "target_playlist_id": target,
            "target_playlist_name": target.title(), "job_type": job_type,
            "is_enabled": enabled,
        },
        "schedule_revision": f"revision-{schedule_id}",
        "timing": {"text": "Every day", "effective_timezone": "UTC", "legacy": False},
        "registration": {"status": "registered" if enabled else "paused", "next_run_at": None},
        "state": "Active" if enabled else "Paused",
    }


def page(db_app):
    return render_page(
        db_app, "schedules.html", path="/schedules",
        routines=[row(4, "target", "rotate", False), row(5, "target", "shuffle"),
                  row(6, "other", "raid")],
        playlists=[{"id": "target", "name": "Target"}, {"id": "other", "name": "Other"}],
    )


def test_cards_use_exact_ids_and_navigation_does_not_mutate(node, db_app):
    run_page(node, page(db_app), r"""
const reply = data => ({ok: true, status: 200, json: async () => data});
const page = loadPage(TREE, {path: '/schedules?routine_id=5&view=delete', route(url) {
    if (url.startsWith('/api/schedules/latest-results')) return reply({success: true,
        results: {'4': null, '5': null, '6': null}});
    if (url.endsWith('/routines/context')) return reply({success: true, capabilities: {}});
}});
page.answer = false;
await settle();
const card = id => page.$('.schedule-card[data-schedule-id="' + id + '"]');
assert.equal(page.$$('.schedule-card').length, 3);
assert.equal(card(5).querySelector('.routine-edit').getAttribute('href'),
    '/playlist/target/routines/new?schedule_id=5&from=routines');
assert.equal(page.document.activeElement, card(5).querySelector('.routine-delete'));
assert.equal(page.asked.length, 1);
assert.equal(page.calls.filter(call => call.method !== 'GET').length, 0);
assert.equal(page.calls.filter(call => call.url === '/api/schedules/latest-results').length, 1);
assert.match(page.text(card(4)), /Paused/);
assert.match(page.text(card(6)), /Collect/);
""")


def test_state_put_targets_exact_row_and_delete_requires_confirmation(node, db_app):
    run_page(node, page(db_app), r"""
const reply = data => ({ok: true, status: 200, json: async () => data});
const page = loadPage(TREE, {path: '/schedules', route(url) {
    if (url.startsWith('/api/schedules/latest-results')) return reply({success: true, results: {}});
    if (url.endsWith('/routines/context')) return reply({success: true, capabilities: {}});
}});
await settle();
const card = id => page.$('.schedule-card[data-schedule-id="' + id + '"]');
page.replies.push(page.reply({success: true, schedule_id: 5, schedule_revision: 'revision-new',
    schedule: {id: 5, target_playlist_id: 'target', target_playlist_name: 'Target',
        job_type: 'shuffle', is_enabled: false}, timing: {text: 'Every day'},
    registration: {status: 'paused'}, state: 'Paused'}));
card(5).querySelector('.routine-toggle').click();
await settle();
const state = page.calls.find(call => call.url === '/api/routines/5/state');
assert.ok(state); assert.equal(state.method, 'PUT');
assert.deepEqual(JSON.parse(state.body), {desired_enabled: false, expected_revision: 'revision-5'});
assert.match(page.text(card(5)), /Paused/);
assert.match(page.text(card(4)), /Paused/);
page.answer = false;
card(6).querySelector('.routine-delete').click();
await settle();
assert.equal(page.calls.filter(call => call.url === '/api/schedules/6').length, 0);
assert.ok(card(6));
page.answer = true;
page.replies.push(page.reply({success: true}));
card(6).querySelector('.routine-delete').click();
await settle();
assert.equal(page.calls.filter(call => call.url === '/api/schedules/6').length, 1);
assert.equal(card(6), null);
""")


def test_history_deep_link_reads_only_named_routine_and_filters_loaded_rows(node, db_app):
    run_page(node, page(db_app), r"""
const reply = data => ({ok: true, status: 200, json: async () => data});
const page = loadPage(TREE, {path: '/schedules?routine_id=6&view=history', route(url) {
    if (url.startsWith('/api/schedules/latest-results')) return reply({success: true,
        results: {'4': null, '5': null, '6': null}});
    if (url.endsWith('/routines/context')) return reply({success: true, capabilities: {}});
    if (url === '/api/schedules/6/history') return reply({success: true, history: []});
}});
await settle();
assert.equal(page.calls.filter(call => call.url.endsWith('/history')).length, 1);
assert.equal(page.calls.find(call => call.url.endsWith('/history')).url, '/api/schedules/6/history');
assert.equal(page.byId('history-6').hidden, false);
assert.match(page.text(page.byId('history-6')), /No executions yet/);
const type = page.byId('routine-type-filter');
type.value = 'shuffle'; page.change(type);
assert.deepEqual(page.$$('.schedule-card').map(card => card.dataset.scheduleId), ['5']);
assert.equal(page.byId('routine-deep-link-error').hidden, true);
assert.equal(page.calls.filter(call => call.method !== 'GET').length, 0);
""")


def test_missing_prerequisite_is_separate_from_registration_state(node, db_app):
    run_page(node, page(db_app), r"""
const reply = data => ({ok: true, status: 200, json: async () => data});
const page = loadPage(TREE, {path: '/schedules', route(url) {
    if (url.startsWith('/api/schedules/latest-results')) return reply({success: true, results: {}});
    if (url === '/api/playlist/target/routines/context') return reply({success: true,
        capabilities: {shuffle: {available: false, reason: 'Choose a collection.'}}});
    if (url.endsWith('/routines/context')) return reply({success: true, capabilities: {}});
}});
await settle();
const card = page.$('.schedule-card[data-schedule-id="5"]');
assert.match(page.text(card.querySelector('.routine-status')), /Active/);
assert.match(page.text(card), /Missing prerequisite: Choose a collection/);
assert.equal(card.querySelector('.routine-toggle').disabled, false);
""")


def test_prerequisites_are_bounded_and_failures_are_named_per_playlist(node, db_app):
    many = render_page(
        db_app, "schedules.html", path="/schedules",
        routines=[row(index, f"target{index}", "shuffle") for index in range(1, 6)],
        playlists=[{"id": f"target{index}", "name": f"Target {index}"} for index in range(1, 6)],
    )
    run_page(node, many, r"""
const reply = data => ({ok: true, status: 200, json: async () => data});
const waiting = [];
let inFlight = 0, maximum = 0;
const page = loadPage(TREE, {path: '/schedules', route(url) {
    if (url.startsWith('/api/schedules/latest-results')) return reply({success: true, results: {}});
    if (url.endsWith('/routines/context')) return new Promise(resolve => {
        inFlight++; maximum = Math.max(maximum, inFlight);
        waiting.push({url, finish(data) { inFlight--; resolve(reply(data)); }});
    });
}});
await settle();
assert.equal(waiting.length, 2); assert.equal(inFlight, 2);
assert.match(page.text(page.$('.schedule-card[data-schedule-id="5"]')), /Checking prerequisites/);
waiting[0].finish({success: true, capabilities: {shuffle: {available: true}}});
await settle();
assert.equal(waiting.length, 3); assert.equal(inFlight, 2);
waiting[1].finish({success: false});
await settle();
assert.equal(waiting.length, 4); assert.equal(inFlight, 2);
assert.match(page.text(page.$('.schedule-card[data-schedule-id="2"]')),
    /Prerequisites could not be checked/);
assert.doesNotMatch(page.text(page.$('.schedule-card[data-schedule-id="1"]')),
    /Prerequisites could not be checked|Checking prerequisites/);
waiting[2].finish({success: true, capabilities: {}});
waiting[3].finish({success: true, capabilities: {}});
await settle();
assert.equal(waiting.length, 5); assert.equal(inFlight, 1);
waiting[4].finish({success: true, capabilities: {}});
await settle();
assert.equal(maximum, 2); assert.equal(inFlight, 0);
assert.doesNotMatch(page.text(page.$('.schedule-card[data-schedule-id="5"]')),
    /Checking prerequisites/);
""")


def test_run_deep_link_opens_exact_review_without_dispatching(node, db_app):
    run_page(node, page(db_app), r"""
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
const page = loadPage(TREE, {path: '/schedules?routine_id=5&view=run', route(url) {
    if (url.startsWith('/api/schedules/latest-results')) return reply({success: true, results: {}});
    if (url.endsWith('/routines/context')) return reply({success: true, capabilities: {}});
    if (url === '/api/schedules/5/run-context') return reply({success: true, context: reviewContext});
}});
await settle();
assert.equal(page.calls.filter(call => call.url.endsWith('/run-context')).length, 1);
assert.equal(page.calls.find(call => call.url.endsWith('/run-context')).url,
    '/api/schedules/5/run-context');
assert.match(page.text(page.byId('live-operation-review')), /Shuffle playlist/);
assert.equal(page.calls.filter(call => call.method !== 'GET').length, 0);
""")
