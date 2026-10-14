"""Reviewed Library direct actions: prepare reads, final Run dispatches once, Check only observes."""

import re
from pathlib import Path

import pytest

from shuffify.schemas.requests import ShuffleRequest
from tests.node_runtime import run_scenario
from tests.test_app_panel_runtime import FIXTURE as PANEL_FIXTURE
from tests.test_playlist_operations_runtime import CLIENT, run_node
from tests.test_playlist_operations_runtime import node as node

# Shared by every reviewed-mode suite: navigation and panel stubs, results labels,
# review digests and one builder for the frozen context shape.
REVIEW_FIXTURE = """
const guards = [], unloadChecks = [];
let navigationPending = false, unloadApproved = false;
// Mirrors app-panels.js: programmatic navigation asks the guards, one request at a time, and
// the navigation they approve is not warned about by the page's one unload listener.
async function navigate(detail, proceed) {
    if (navigationPending) return false;
    navigationPending = true;
    try {
        if (!(await window.AppNavigation.request(detail))) return false;
        unloadApproved = true;
        proceed(); return true;
    } catch (_) { return false; } finally { navigationPending = false; }
}
window.addEventListener('beforeunload', event => {
    const approved = unloadApproved;
    unloadApproved = false;
    if (unloadChecks.map(check => check(approved)).some(Boolean)) { event.preventDefault(); event.returnValue = ''; }
});
window.AppNavigation = {addGuard: guard => {guards.push(guard); return () => {};},
    addUnloadCheck: check => {unloadChecks.push(check); return () => {};},
    request: async detail => {
        for (const guard of guards) {if (await guard(detail) === false) return false;}
        return true;
    },
    assign: (url, element) => navigate({kind: 'link', url, element}, () => window.location.assign(url)),
    reload: () => navigate({kind: 'reload', url: window.location.href}, () => window.location.reload())};
window.AppPanels.requestClose = async reason => {
    const last = panels[panels.length - 1];
    if (last && last.options.onClose) last.options.onClose(reason);
    return true;
};
element('live-operation-heading').dataset.resultsLabel = 'Live action results';
element('live-operation-close').dataset.resultsLabel = 'Back to Library';
const SCOPE = '1'.repeat(64), BEFORE = '2'.repeat(64), HISTORY = '3'.repeat(64);
const uri = n => 'spotify:track:' + String(n).padStart(22, '0');
function reviewContext(kind, overrides = {}, id = 'target') {
    const name = id === 'target' ? 'Target name' : id + ' playlist';
    return Object.assign({version: 1, kind, target: {id, name},
        label: {shuffle: 'Shuffle now', undo: 'Restore previous saved version now',
            privacy: 'Make playlist private now'}[kind],
        resources: [{resource: id, resource_name: name, role: 'target', domain: 'spotify',
            access: 'write', mutation_type: kind === 'privacy' ? 'metadata' : 'track_order',
            field: kind === 'privacy' ? 'public' : null}],
        required_bookkeeping: kind === 'privacy' ? [] : ['session_history'],
        review_binding: {version: 1, scope: SCOPE, before_order: kind === 'undo' ? BEFORE : null,
            history: kind === 'undo' ? HISTORY : null},
        current: kind === 'privacy' ? {public: true, loaded_at: '2026-10-11T20:00:00Z'}
            : kind === 'undo' ? {track_count: 2, track_uris: [uri(1), uri(2)], loaded_at: '2026-10-11T20:00:00Z'}
            : {track_count: 2, loaded_at: '2026-10-11T20:00:00Z'},
        action: kind === 'privacy' ? {public: false}
            : kind === 'undo' ? {current_index: 2, selected_index: 1, desired_track_uris: [uri(2), uri(2), uri(1)],
                summary: {added_count: 1, removed_count: 0, order_changed: true}}
            : {algorithm: {id: 'BasicShuffle', name: 'Basic'}, params: {keep_first: 2}}}, overrides);
}
const contextReply = (kind, overrides, id) => response({success: true, context: reviewContext(kind, overrides, id)});
const runReview = () => element('live-operation-run').click();
"""

REVIEWED = (
    CLIENT
    + REVIEW_FIXTURE
    + """
const observed = [], completed = [];
let revision = 0;
client.configure({getInputRevision: kind => kind === 'shuffle' ? revision : 0,
    afterComplete: record => {completed.push(record);},
    afterObservation: record => {observed.push(record);}});
const bodies = {shuffle: {csrf_token: 'test-csrf', algorithm: 'BasicShuffle', keep_first: '2'},
    undo: {csrf_token: 'test-csrf'}, privacy: {public: false}};
// The shuffle trigger sits in its options form, whose labels the review reuses.
const optionsForm = {elements: {namedItem: name => name === 'keep_first'
    ? {labels: [{textContent: 'Keep first tracks'}]} : null}};
const describe = (kind, targetId = 'target') => ({kind, targetId, body: Object.assign({}, bodies[kind]),
    trigger: Object.assign(element('trigger-' + kind + '-' + targetId), {form: optionsForm})});
const tracks = (status = 'complete', history = 'complete', extra = {}) => ({success: status === 'complete',
    message: 'Verified.', playlist_state: {current_index: 1}, operation: {kind: 'shuffle',
        aggregate_status: status === 'complete' && history === 'complete' ? 'complete' : 'unknown',
        resources: [resource('target', status,
            Object.assign({step: 'shuffle', desired_track_uris: [uri(2), uri(1)]}, extra))],
        bookkeeping: history ? [{kind: 'session_history', step: 'shuffle', status: history}] : []}});
const privacyResult = (status = 'complete') => ({success: status === 'complete',
    public: status === 'complete' ? false : undefined,
    operation: {kind: 'privacy', aggregate_status: status === 'complete' ? 'complete' : 'unknown',
        resources: [resource('target', status, {step: 'privacy', verification: 'metadata',
            desired_metadata: {public: false}})], bookkeeping: []}});
async function reviewed(kind, targetId = 'target') {
    const pending = client.prepare(describe(kind, targetId)); await tick(); await tick();
    runReview(); return pending;
}
"""
)


def test_prepare_reads_context_only_and_final_run_dispatches_once_with_bindings(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    replies.push(contextReply('shuffle'));
    const pending = client.prepare(describe('shuffle')); await tick(); await tick();
    assert.equal(calls.length, 1); assert.equal(calls[0].url, '/playlist/target/live-context');
    assert.deepEqual(JSON.parse(calls[0].options.body),
        {kind: 'shuffle', form: {algorithm: 'BasicShuffle', keep_first: '2'}});
    assert.equal(calls[0].options.body.includes('csrf'), false);
    assert.equal(element('live-operation-heading').textContent, 'Review: Shuffle now');
    assert.equal(element('live-operation-run').textContent, 'Shuffle now');
    assert.equal(element('live-operation-run').hidden, false);
    assert.match(rendered(element('live-operation-review')), /Method: Basic.*Keep first tracks: 2.*Current tracks: 2/);
    runReview(); const handle = await pending;
    assert.equal(Object.isFrozen(handle), true); assert.equal(calls.length, 1);
    replies.push(response(tracks()));
    const record = await client.run(handle);
    assert.equal(await client.run(handle), null); assert.equal(await client.run(copy(handle)), null);
    assert.equal(calls.length, 2); assert.equal(calls[1].url, '/shuffle/target');
    assert.deepEqual(Object.fromEntries(calls[1].options.body), {csrf_token: 'test-csrf', algorithm: 'BasicShuffle',
        keep_first: '2', reviewed_scope: SCOPE});
    assert.equal(record.complete, true); assert.equal(record.attention, false); assert.equal(completed.length, 1);
    assert.equal(JSON.stringify(client.getRecords()).includes('test-csrf'), false);
    assert.equal(record.operationId, 0); assert.equal(client.getRecords().length, 1);
    function copy(value) {return JSON.parse(JSON.stringify(value));}
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_undo_and_privacy_bind_reviewed_facts_without_client_selected_order(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    replies.push(contextReply('undo'));
    const undo = await reviewed('undo');
    assert.match(rendered(element('live-operation-review')), /previous version saved in this session.*adds 1 track/);
    replies.push(response(tracks()));
    await client.run(undo);
    assert.deepEqual(Object.fromEntries(calls[1].options.body), {csrf_token: 'test-csrf', reviewed_scope: SCOPE,
        reviewed_before_order: BEFORE, reviewed_history: HISTORY});
    replies.push(contextReply('privacy'));
    const privacy = await reviewed('privacy');
    replies.push(response(privacyResult()));
    const record = await client.run(privacy);
    assert.equal(calls[3].url, '/playlist/target/toggle-visibility');
    assert.deepEqual(JSON.parse(calls[3].options.body), {public: false, reviewed_scope: SCOPE});
    assert.equal(record.complete, true); assert.equal(record.intent.action.public, false);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize("moment", ["while_loading", "in_review", "after_accept"])
def test_cancel_or_option_change_invalidates_review_with_zero_mutation(node, moment):
    run_node(
        node,
        REVIEWED
        + f"""
(async () => {{
    const moment = '{moment}';
    let deliver; replies.push(() => new Promise(resolve => {{deliver = resolve;}}));
    const pending = client.prepare(describe('shuffle')); await tick(); await tick();
    if (moment === 'while_loading') {{
        await window.AppPanels.requestClose('escape');
        deliver(contextReply('shuffle')); assert.equal(await pending, null);
        assert.equal(element('live-operation-heading').textContent, 'Live action results');
    }} else if (moment === 'in_review') {{
        deliver(contextReply('shuffle')); await tick(); await tick();
        revision++; runReview(); assert.equal(await pending, null);
        assert.match(element('live-operation-review').dataset.notice, /options changed/);
    }} else {{
        deliver(contextReply('shuffle')); await tick(); await tick();
        runReview(); const handle = await pending; revision++;
        assert.equal(await client.run(handle), null);
    }}
    assert.equal(calls.length, 1); assert.equal(client.getRecords().length, 0);
    assert.equal(client.isPreparing(), false);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_newer_preparation_supersedes_older_and_late_reply_cannot_reopen_it(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    let first; replies.push(() => new Promise(resolve => {first = resolve;}), contextReply('privacy'));
    const older = client.prepare(describe('shuffle')); await tick(); await tick();
    const newer = client.prepare(describe('privacy')); await tick(); await tick();
    assert.equal(await older, null);
    first(contextReply('shuffle')); await tick(); await tick();
    assert.equal(element('live-operation-heading').textContent, 'Review: Make playlist private now');
    runReview(); const handle = await newer; assert.equal(handle.kind, 'privacy');
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize(
    "bad", ["target", "digest", "kind", "algorithm", "order", "binding"]
)
def test_malformed_context_blocks_run_and_load_again_rereads_only(node, bad):
    run_node(
        node,
        REVIEWED
        + f"""
(async () => {{
    const bad = '{bad}', kind = bad === 'order' || bad === 'binding' ? 'undo' : 'shuffle';
    const broken = {{target: {{target: {{id: 'other', name: 'Other'}}}},
        digest: {{review_binding: {{version: 1, scope: 'ABC', before_order: null, history: null}}}},
        kind: {{kind: 'privacy'}}, algorithm: {{action: {{algorithm: {{id: 'Other', name: 'Other'}}, params: {{}}}}}},
        order: {{action: {{current_index: 2, selected_index: 1, desired_track_uris: ['not-a-uri'],
            summary: {{added_count: 0, removed_count: 0, order_changed: true}}}}}},
        binding: {{review_binding: {{version: 1, scope: SCOPE, before_order: null, history: HISTORY}}}}}}[bad];
    replies.push(contextReply(kind, broken), contextReply(kind));
    const pending = client.prepare(describe(kind)); await tick(); await tick();
    assert.equal(element('live-operation-heading').textContent, 'Review unavailable');
    assert.equal(element('live-operation-run').textContent, 'Load review again');
    runReview(); await tick(); await tick();
    assert.equal(calls.length, 2); assert.equal(calls[1].url, '/playlist/target/live-context');
    assert.match(element('live-operation-heading').textContent, /^Review: /);
    runReview(); assert.notEqual(await pending, null); assert.equal(client.getRecords().length, 0);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_forged_raw_or_copied_handles_never_dispatch_in_reviewed_mode(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    assert.equal(await client.run(Object.freeze({kind: 'shuffle', targetId: 'target', generation: 1})), null);
    await assert.rejects(client.prepare({kind: 'rotate_now', targetId: 'target', body: {}}), /Unsupported/);
    await assert.rejects(client.prepare({kind: 'schedule', scheduleId: '5'}), /Unsupported/);
    await assert.rejects(client.prepare({kind: 'privacy', targetId: 'target', body: {public: 'false'}}),
        /true or false/);
    await assert.rejects(client.prepare({kind: 'shuffle', targetId: 'target',
        body: {algorithm: 'BasicShuffle', reviewed_scope: SCOPE}}), /server/);
    assert.equal(calls.length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_lost_track_reply_blocks_track_and_history_writes_across_cards_but_not_privacy(
    node,
):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    replies.push(contextReply('shuffle'));
    const handle = await reviewed('shuffle');
    replies.push(() => {throw new Error('lost response');});
    const lost = await client.run(handle);
    assert.equal(lost.aggregate, 'unknown'); assert.equal(lost.blocked, true); assert.equal(lost.attention, true);
    assert.deepEqual(plain(lost.claims), ['tracks:target', 'history:*']);
    const reads = calls.length;
    assert.equal(await client.prepare(describe('undo', 'other')), null);
    assert.equal(await client.prepare(describe('shuffle', 'target')), null);
    assert.equal(calls.length, reads);
    assert.equal(client.isBlocked('shuffle:other'), true); assert.equal(client.isBlocked('privacy:target'), false);
    replies.push(contextReply('privacy'));
    const privacy = await reviewed('privacy');
    replies.push(response(privacyResult())); assert.equal((await client.run(privacy)).complete, true);
    assert.equal(client.hasAttention(), true);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_known_unchanged_history_limits_track_block_to_that_playlist(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    replies.push(contextReply('shuffle'));
    const handle = await reviewed('shuffle');
    replies.push(response(tracks('unknown', 'failed'), 502));
    const record = await client.run(handle);
    assert.deepEqual(plain(record.claims), ['tracks:target']);
    assert.equal(client.isBlocked('shuffle:target'), true); assert.equal(client.isBlocked('undo:other'), false);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize(
    "code", ["review_changed", "history_changed", "resource_changed", "playlist_busy"]
)
def test_proven_no_effect_rejection_needs_a_fresh_review_not_a_permanent_block(
    node, code
):
    run_node(
        node,
        REVIEWED
        + f"""
(async () => {{
    replies.push(contextReply('undo'));
    const handle = await reviewed('undo');
    replies.push(response({{success: false, status: 'failed', message: 'Changed.', operation: {{kind: 'undo',
        aggregate_status: 'partial', resources: [], bookkeeping: [], error_code: '{code}',
        retryable: '{code}' === 'playlist_busy'}}}}, 409));
    const record = await client.run(handle);
    assert.equal(record.noEffect, true); assert.equal(record.blocked, false); assert.equal(record.attention, false);
    assert.equal(client.hasAttention(), false);
    if ('{code}' !== 'playlist_busy') assert.match(record.message, /Review it again/);
    replies.push(contextReply('undo'));
    const again = await reviewed('undo'); assert.notEqual(again, null);
    assert.equal(calls.filter(call => call.url === '/playlist/target/live-context').length, 2);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_complete_track_action_requires_its_saved_history_fact(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    replies.push(contextReply('shuffle'));
    const handle = await reviewed('shuffle');
    const vacuous = tracks(); vacuous.operation.bookkeeping = [];
    replies.push(response(vacuous));
    const record = await client.run(handle);
    assert.equal(record.complete, false); assert.equal(record.blocked, true);
    // The target's contents are verified; only the shared saved history stays unresolved,
    // which still blocks every action that writes history.
    assert.deepEqual(plain(record.claims), ['history:*']);
    assert.equal(client.isBlocked('shuffle:target'), true); assert.equal(client.isBlocked('undo:other'), true);
    // A missing required fact is unknown, so the copy still warns against repeating.
    assert.equal(record.aggregate, 'unknown'); assert.match(record.message, /Do not repeat this action/);
    assert.equal(completed.length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_privacy_check_clears_only_its_own_conflict_and_a_newer_failure_restores_it(
    node,
):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    replies.push(contextReply('privacy'));
    const handle = await reviewed('privacy');
    replies.push(response(privacyResult('unknown'), 502));
    const record = await client.run(handle);
    const original = JSON.stringify(client.getRecords()[0].response);
    assert.equal(client.isBlocked('privacy:target'), true); assert.equal(client.isBlocked('shuffle:target'), false);
    const match = {success: true, inspection_only: true, public: false, operation: {kind: 'privacy',
        aggregate_status: 'complete', retryable: false, error_code: null, bookkeeping: [],
        resources: [resource('target', 'not_required', {step: 'privacy', dispatched: false, affected_count: 0,
            verification: 'metadata', current_metadata: {public: false}, desired_metadata: {public: false}})]}};
    replies.push(response(match));
    const checked = await client.reconcile(record.operationId);
    assert.equal(calls[2].url, '/playlist/target/inspect-live');
    assert.deepEqual(JSON.parse(calls[2].options.body), {kind: 'privacy', desired_public: false});
    assert.equal(checked.blocked, false); assert.equal(checked.attention, false);
    assert.equal(observed.length, 1); assert.equal(observed[0].observations[0].status, 'match');
    assert.equal(JSON.stringify(client.getRecords()[0].response), original);
    assert.equal(client.getRecords()[0].aggregate, 'unknown');
    assert.match(rendered(element('live-operation-results')),
        /verifies the current state; it does not show which request caused it/);
    replies.push(() => {throw new Error('lost check');});
    const failed = await client.reconcile(record.operationId);
    assert.equal(failed.blocked, true); assert.equal(failed.observations.length, 2);
    assert.equal(failed.observations[0].status, 'match'); assert.equal(failed.observations[1].status, 'failed');
    assert.equal(calls.filter(call => call.options.method === 'POST' &&
        call.url === '/playlist/target/toggle-visibility').length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_track_check_uses_retained_order_and_never_clears_unknown_history(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    replies.push(contextReply('undo'));
    const undo = await reviewed('undo');
    replies.push(() => {throw new Error('lost response');});
    const record = await client.run(undo);
    const match = {success: true, inspection_only: true, operation: {kind: 'track_inspection',
        aggregate_status: 'complete', retryable: false, error_code: null, bookkeeping: [],
        resources: [resource('target', 'not_required', {step: 'track_inspection', dispatched: false,
            affected_count: 0, current_track_uris: [uri(2), uri(2), uri(1)]})]}};
    replies.push(response(match));
    const checked = await client.reconcile(record.operationId);
    assert.deepEqual(JSON.parse(calls[2].options.body), {kind: 'tracks', desired_track_uris: [uri(2), uri(2), uri(1)]});
    assert.equal(checked.observations[0].status, 'match'); assert.equal(checked.blocked, true);
    assert.equal(client.isBlocked('shuffle:other'), true);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_lost_random_shuffle_check_sends_no_desired_order_and_stays_unavailable(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    replies.push(contextReply('shuffle'));
    const handle = await reviewed('shuffle');
    replies.push(() => {throw new Error('lost response');});
    const record = await client.run(handle);
    replies.push(response({success: false, inspection_only: true, operation: {kind: 'track_inspection',
        aggregate_status: 'unknown', retryable: false, error_code: 'intent_unavailable', bookkeeping: [],
        resources: [resource('target', 'unknown', {step: 'track_inspection', dispatched: false,
            affected_count: null, current_track_uris: [uri(1)]})]}}, 502));
    const checked = await client.reconcile(record.operationId);
    assert.deepEqual(JSON.parse(calls[2].options.body), {kind: 'tracks'});
    assert.equal(checked.observations[0].status, 'unavailable'); assert.equal(checked.blocked, true);
    assert.equal(await client.reconcile(999), null);
    assert.equal(calls.filter(call => call.url.startsWith('/shuffle/')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_records_are_snapshots_and_display_refresh_failure_is_not_a_conflict(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    client.configure({getInputRevision: () => 0,
        afterComplete: () => {throw new Error('display unavailable');}});
    replies.push(contextReply('privacy'));
    const handle = await reviewed('privacy');
    replies.push(response(privacyResult()));
    const record = await client.run(handle);
    assert.equal(record.complete, true); assert.equal(record.displayAttention, 'complete');
    assert.equal(record.blocked, false); assert.equal(record.attention, true);
    assert.equal(client.isBlocked('privacy:target'), false);
    assert.equal(Object.isFrozen(record), true); assert.equal(Object.isFrozen(client.getRecords()[0].intent), true);
    assert.equal(client.getRecords()[0].attention, true);
    assert.match(rendered(element('live-operation-results')), /Update this view/);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize("choice", ["stay", "review", "leave"])
def test_navigation_ready_busy_and_attention_decisions(node, choice):
    run_node(
        node,
        REVIEWED
        + f"""
(async () => {{
    assert.equal(guards.length, 1);
    replies.push(contextReply('privacy'), response(privacyResult()));
    await client.run(await reviewed('privacy'));
    assert.equal(await window.AppNavigation.request({{kind: 'reload'}}), true);
    replies.push(contextReply('shuffle'));
    const handle = await reviewed('shuffle');
    let finish; replies.push(() => new Promise(resolve => {{finish = resolve;}}));
    const running = client.run(handle); await tick();
    assert.equal(await window.AppNavigation.request({{kind: 'reload'}}), false);
    finish(response(tracks('unknown', 'unknown'), 502)); await running;
    const opened = panels.length, assigned = [];
    window.location = {{href: '/library', assign: url => {{assigned.push(url);}}}};
    const decision = window.AppNavigation.assign('/settings'); await tick();
    assert.equal(panels[opened].id, 'live-operation-leave'); assert.equal(panels[opened].options.modal, true);
    assert.equal(await window.AppNavigation.request({{kind: 'reload'}}), false);
    const choice = '{choice}';
    element({{stay: 'live-operation-leave-stay', review: 'live-operation-leave-review',
        leave: 'live-operation-leave-confirm'}}[choice]).click();
    assert.equal(await decision, choice === 'leave');
    assert.deepEqual(assigned, choice === 'leave' ? ['/settings'] : []);
    // Only the navigation the guard approved leaves without the browser's warning, and only once.
    let warned = false; const unload = () => listeners.beforeunload[0]({{preventDefault() {{warned = true;}}}});
    unload(); assert.equal(warned, choice !== 'leave');
    warned = false; unload(); assert.equal(warned, true);
    if (choice === 'review') {{await tick(); assert.equal(panels[panels.length - 1].id, 'live-operation-panel');}}
    assert.equal(calls.filter(call => call.url.startsWith('/shuffle/')).length, 1);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_an_approval_not_yet_used_does_not_excuse_leaving_during_a_run(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    window.location = {href: '/library', assign() {}};
    // Nothing to lose: the guard approves at once, and that approval waits for the next unload.
    assert.equal(await window.AppNavigation.assign('/settings'), true);
    replies.push(contextReply('shuffle'));
    const handle = await reviewed('shuffle');
    let finish; replies.push(() => new Promise(resolve => {finish = resolve;}));
    const running = client.run(handle); await tick();
    assert.equal(client.isBusy(), true);
    // A run in flight warns anyway: leaving cannot stop it.
    let warned = false; listeners.beforeunload[0]({preventDefault() {warned = true;}});
    finish(response(tracks())); await running;
    assert.equal(warned, true);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_ready_navigation_cancels_an_open_review_quietly(node):
    run_node(
        node,
        REVIEWED
        + """
(async () => {
    replies.push(contextReply('shuffle'));
    const pending = client.prepare(describe('shuffle')); await tick(); await tick();
    assert.equal(await window.AppNavigation.request({kind: 'link', url: '/settings'}), true);
    assert.equal(await pending, null); assert.equal(calls.length, 1);
    assert.equal(panels.filter(panel => panel.id === 'live-operation-leave').length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


ROOT = Path(__file__).resolve().parent.parent


def test_review_form_allowlist_matches_the_server_shuffle_fields():
    """A field missing from the client allowlist would be dropped from the review read."""
    source = (ROOT / "shuffify/static/js/playlist-operations.js").read_text()
    block = re.search(r"var formFields = \[(.*?)\];", source, re.S).group(1)
    assert set(re.findall(r"'([a-z_]+)'", block)) == set(ShuffleRequest.model_fields) - {"locked_positions"}


def test_update_view_retries_the_failed_observation_display_without_resending(node):
    run_node(node, REVIEWED + """
(async () => {
    let failDisplay = true; const shown = [];
    client.configure({getInputRevision: () => 0,
        afterComplete: () => {throw new Error('Completion display must not be retried for a check');},
        afterObservation: record => {
            if (failDisplay) throw new Error('tile unavailable');
            shown.push(record.intent.action.public);
        }});
    replies.push(contextReply('privacy'));
    const handle = await reviewed('privacy');
    replies.push(() => {throw new Error('lost response');});
    const record = await client.run(handle);
    replies.push(response({success: true, inspection_only: true, public: false, operation: {kind: 'privacy',
        aggregate_status: 'complete', retryable: false, error_code: null, bookkeeping: [],
        resources: [resource('target', 'not_required', {step: 'privacy', dispatched: false, affected_count: 0,
            verification: 'metadata', current_metadata: {public: false}, desired_metadata: {public: false}})]}}));
    const checked = await client.reconcile(record.operationId);
    assert.equal(checked.blocked, false); assert.equal(checked.displayAttention, 'observation');
    assert.equal(client.hasAttention(), true);
    assert.match(rendered(element('live-operation-results')), /Update this view/);
    failDisplay = false;
    const button = {dataset: {liveRefresh: String(record.operationId)}};
    await element('live-operation-results').click({target: {closest: () => button}}); await tick();
    assert.deepEqual(shown, [false]); assert.equal(client.hasAttention(), false);
    assert.equal(client.getRecords()[0].displayAttention, false);
    assert.equal(calls.filter(call => call.url.endsWith('/toggle-visibility')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""")


def test_update_view_retries_a_failed_completion_display(node):
    run_node(node, REVIEWED + """
(async () => {
    let failDisplay = true;
    client.configure({getInputRevision: () => 0,
        afterComplete: () => {if (failDisplay) throw new Error('tile unavailable');}});
    replies.push(contextReply('privacy'));
    const handle = await reviewed('privacy');
    replies.push(response(privacyResult()));
    const record = await client.run(handle);
    assert.equal(record.displayAttention, 'complete'); assert.equal(client.hasAttention(), true);
    failDisplay = false;
    const button = {dataset: {liveRefresh: String(record.operationId)}};
    await element('live-operation-results').click({target: {closest: () => button}}); await tick();
    assert.equal(client.hasAttention(), false); assert.equal(calls.length, 2);
})().catch(e => {console.error(e); process.exitCode = 1;});
""")


def test_real_panels_reuse_open_results_for_review_and_escape_cancels_to_the_trigger(node):
    """The shipped AppPanels keeps the first open's hooks; the controller's stable hooks still cancel."""
    run_scenario(node, PANEL_FIXTURE + r"""
(async () => {
    const f = fixture();
    // Give fixture elements the few DOM members the controller renders with.
    const proto = Object.getPrototypeOf(f.trigger);
    Object.defineProperty(proto, 'dataset', {configurable: true,
        get() { return this._dataset || (this._dataset = {}); }});
    proto.appendChild = function (child) { this.append(child); return child; };
    proto.replaceChildren = function (...nodes) { this.children = []; this.append(...nodes); };
    proto.addEventListener = function (type, handler) { (this.handlers ||= {})[type] = handler; };
    proto.click = function () { return this.handlers.click({target: this}); };
    f.window.document = f.document; f.document.createElement = tag => f.element(tag);
    f.document.querySelectorAll = selector => f.document.documentElement.querySelectorAll(selector);
    const el = (tag, id, attrs = {}) => f.element(tag, Object.assign({id}, attrs));
    const panel = el('section', 'live-operation-panel', {'data-app-panel': '', hidden: '', inert: ''});
    const heading = el('h2', 'live-operation-heading', {'data-panel-heading': '', tabindex: '-1'});
    const body = el('div', 'live-operation-body'); body.classList.add('sh-panel-body');
    const review = el('div', 'live-operation-review', {hidden: ''}), results = el('ol', 'live-operation-results');
    const run = el('button', 'live-operation-run', {hidden: ''});
    const close = el('button', 'live-operation-close', {'data-panel-close': ''});
    heading.dataset.resultsLabel = 'Live action results'; close.dataset.resultsLabel = 'Back to Library';
    body.append(el('p', 'live-operation-status'), review, results, el('p', 'live-operation-note'));
    panel.append(heading, body, run, close); f.workspace.append(panel);
    const card = f.element('button'); f.primary.append(card);
    const calls = [], context = {version: 1, kind: 'privacy', target: {id: 'target', name: 'Target name'},
        label: 'Make playlist private now', resources: [{resource: 'target', resource_name: 'Target name',
            role: 'target', domain: 'spotify', access: 'write', mutation_type: 'metadata', field: 'public'}],
        required_bookkeeping: [], review_binding: {version: 1, scope: '1'.repeat(64), before_order: null,
            history: null}, current: {public: true, loaded_at: 'now'}, action: {public: false}};
    f.window.fetch = async url => {
        calls.push(url); return {ok: true, json: async () => ({success: true, context})};
    };
    vm.runInNewContext(fs.readFileSync('shuffify/static/js/playlist-operations.js', 'utf8'),
        {window: f.window, document: f.document, AbortController, FormData, setTimeout, clearTimeout, console});
    const client = f.window.PlaylistOperations;
    client.configure({getInputRevision: () => 0});
    // Results were opened from another control before this review starts in the same panel.
    assert.equal(await client.openResults(f.trigger), true); assert.equal(panel.hidden, false);
    const pending = client.prepare({kind: 'privacy', targetId: 'target', body: {public: false}, trigger: card});
    await tick(); await tick();
    assert.equal(heading.textContent, 'Review: Make playlist private now');
    assert.equal(f.document.activeElement, heading);
    assert.equal(run.hidden, false); assert.equal(close.textContent, 'Cancel');
    f.send('keydown', heading, {key: 'Escape'}); await tick(); await tick();
    assert.equal(await pending, null); assert.equal(panel.hidden, true);
    assert.equal(f.document.activeElement, card);
    assert.equal(heading.textContent, 'Live action results'); assert.equal(close.textContent, 'Back to Library');
    assert.deepEqual(calls, ['/playlist/target/live-context']);
})().catch(e => {console.error(e); process.exitCode = 1;});
""", ROOT)
