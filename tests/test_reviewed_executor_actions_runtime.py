"""The review controller: executor and snapshot reviews, multi-resource claims, draft continuation."""

import pytest

from tests.test_playlist_operations_runtime import CLIENT, run_node
from tests.test_playlist_operations_runtime import node as node
from tests.test_reviewed_live_actions_runtime import REVIEW_FIXTURE

# Context and result builders for the executor and snapshot kinds.
EXECUTOR_CONTEXTS = """
const res = (id, role, access, mutation) => ({resource: id, resource_name: id + ' name', role,
    domain: role === 'inbox' ? 'inbox' : 'spotify', access, mutation_type: access === 'read' ? null : mutation,
    field: null});
function executorContext(kind, overrides = {}) {
    const resources = {
        raid: [res('target', 'target', 'read'), res('collection', 'collection', 'write', 'track_membership'),
            res('target', 'inbox', 'write', 'inbox_state')],
        drip: [res('target', 'target', 'write', 'track_membership'),
            res('collection', 'collection', 'write', 'track_membership'),
            res('target', 'inbox', 'write', 'inbox_state')],
    };
    resources.raid_and_drip = resources.drip;
    const steps = {raid: ['raid'], drip: ['drip'], raid_and_drip: ['raid', 'drip']}[kind];
    const bookkeeping = {raid: ['pending_stage', 'execution_record'], drip: ['pending_promotion', 'execution_record'],
        raid_and_drip: ['pending_stage', 'pending_promotion', 'execution_record']}[kind];
    return Object.assign({version: 1, kind, label: {raid: 'Collect for review', drip: 'Add collected tracks now',
        raid_and_drip: 'Collect and add tracks now'}[kind], target: {id: 'target', name: 'Target name'},
        schedule: null, snapshot: null, resources: resources[kind], steps, required_bookkeeping: bookkeeping,
        review_binding: {version: 1, scope: SCOPE, before_order: null, history: null},
        current: {source_count: 2}, action: {count: 3, sources: [{id: 7, label: 'Saved source'}], extras: ['extra']}},
        overrides);
}
function snapshotContext(overrides = {}) {
    return Object.assign({version: 1, kind: 'snapshot_restore', label: 'Restore saved snapshot now',
        target: {id: 'target', name: 'Target name'}, schedule: null, snapshot: {id: 17, created_at: 'Oct 9'},
        resources: [res('target', 'target', 'write', 'track_order')], steps: [],
        required_bookkeeping: ['session_history'],
        review_binding: {version: 1, scope: SCOPE, before_order: BEFORE, history: null},
        current: {track_count: 2, track_uris: [uri(1), uri(2)], loaded_at: 'now'},
        action: {snapshot_id: 17, desired_track_uris: [uri(2), uri(1)],
            summary: {added_count: 0, removed_count: 0, order_changed: true}}}, overrides);
}
function rotateContext(archive) {
    return {version: 1, kind: 'schedule', label: 'Run routine now', target: {id: 'target', name: 'Target name'},
        schedule: {id: 4, job_type: 'rotate'}, snapshot: null,
        resources: [res('target', 'target', 'write', 'track_membership'), res(archive, 'archive', 'write',
            'track_membership')], steps: ['rotate'], required_bookkeeping: ['execution_record'],
        review_binding: {version: 1, scope: SCOPE, before_order: null, history: null}, current: {},
        action: {job_type: 'rotate', rotation: {mode: 'swap', count: 5, cap: 40, protect: 0}}};
}
const replyWith = context => response({success: true, context});
const executorResult = (kind, statuses = {}, bookkeeping) => ({success: !Object.keys(statuses).length,
    execution_id: 9, started_at: 'then', execution_state: 'recorded',
    operation: {kind, aggregate_status: Object.values(statuses).includes('unknown') ? 'unknown' : 'complete',
        retryable: false,
        resources: Object.entries(Object.assign({target: 'complete', collection: 'complete'}, statuses))
            .map(([id, status]) => resource(id, status, {step: kind, desired_track_uris: [uri(3)]})),
        bookkeeping: bookkeeping || [{kind: 'pending_promotion', status: 'complete'},
            {kind: 'pending_stage', status: 'complete'}, {kind: 'execution_record', status: 'complete'}]}});
"""

EXECUTOR = (
    CLIENT
    + REVIEW_FIXTURE
    + EXECUTOR_CONTEXTS
    + """
let revision = 0, page = {busy: false, draft: [], attention: []}, nav = {draft: false, attention: false};
const resolutions = [], attentions = [], completed = [];
let resolver = null;
client.configure({getInputRevision: () => revision,
    getContext: () => page, navigationState: () => nav,
    openAttention: async () => {attentions.push(true);},
    resolveConflict: choice => {resolutions.push(choice); return new Promise(resolve => {resolver = resolve;});},
    afterComplete: record => {completed.push(record);}});
async function prepared(descriptor) {
    const pending = client.prepare(descriptor); await tick(); await tick(); return {done: pending};
}
async function reviewedRun(descriptor, context, result) {
    replies.push(replyWith(context));
    const review = await prepared(descriptor); runReview();
    const handle = await review.done; replies.push(result);
    return client.run(handle);
}
"""
)


@pytest.mark.parametrize(
    "kind,path",
    [("raid", "raid-now"), ("drip", "drip-now"), ("raid_and_drip", "raid-and-drip")],
)
def test_executor_kinds_review_then_run_once_with_only_the_binding(node, kind, path):
    run_node(
        node,
        EXECUTOR
        + f"""
(async () => {{
    const kind = '{kind}';
    replies.push(replyWith(executorContext(kind)));
    const review = await prepared({{kind, targetId: 'target', trigger: element('trigger')}});
    assert.deepEqual(JSON.parse(calls[0].options.body), {{kind}});
    assert.equal(calls[0].url, '/api/playlist/target/live-context');
    const text = rendered(element('live-operation-review'));
    if (kind === 'raid') {{
        assert.match(text, new RegExp('Collects new tracks from Saved source, 1 playlist chosen for this run ' +
            'into collection name and the review inbox.*adds nothing'));
    }}
    else assert.match(text, /Adds up to 3 collected tracks from collection name to the top of Target name/);
    // Only a collect-only run promises the playlist gains nothing.
    if (kind === 'raid_and_drip') assert.doesNotMatch(text, /adds nothing/);
    runReview(); const handle = await review.done;
    replies.push(response(executorResult(kind)));
    const record = await client.run(handle);
    assert.equal(calls[1].url, '/api/playlist/target/{path}');
    assert.deepEqual(JSON.parse(calls[1].options.body), {{reviewed_scope: SCOPE}});
    assert.equal(record.complete, true); assert.equal(await client.run(handle), null); assert.equal(calls.length, 2);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_snapshot_restore_reads_its_own_context_and_binds_scope_and_before_order(node):
    run_node(
        node,
        EXECUTOR
        + """
(async () => {
    const result = {success: true, operation: {kind: 'snapshot_restore', aggregate_status: 'complete',
        retryable: false, resources: [resource('target', 'complete', {step: 'snapshot_restore'})],
        bookkeeping: [{kind: 'session_history', status: 'complete'}]}};
    const record = await reviewedRun({kind: 'snapshot_restore', snapshotId: 17, targetId: 'target',
        trigger: element('trigger')}, snapshotContext(), response(result));
    assert.equal(calls[0].url, '/api/snapshots/17/restore-context'); assert.equal(calls[0].options.method, 'GET');
    assert.equal(calls[1].url, '/api/snapshots/17/restore');
    assert.deepEqual(JSON.parse(calls[1].options.body), {reviewed_scope: SCOPE, reviewed_before_order: BEFORE});
    assert.equal(record.complete, true); assert.equal(record.intent.key, 'snapshot_restore:17');
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize(
    "case", ["schedule_id", "snapshot_target", "no_inbox", "bad_role", "steps"]
)
def test_malformed_executor_contexts_never_offer_run(node, case):
    run_node(
        node,
        EXECUTOR
        + f"""
(async () => {{
    const broken = {{
        schedule_id: [{{kind: 'schedule', scheduleId: 4}}, Object.assign(rotateContext('archive'), {{schedule: {{id: 5,
            job_type: 'rotate'}}}})],
        snapshot_target: [{{kind: 'snapshot_restore', snapshotId: 17, targetId: 'other'}}, snapshotContext()],
        no_inbox: [{{kind: 'drip', targetId: 'target'}}, executorContext('drip', {{resources: [res('target', 'target',
            'write', 'track_membership')]}})],
        bad_role: [{{kind: 'drip', targetId: 'target'}}, executorContext('drip', {{resources: [res('target', 'owner',
            'write', 'track_membership'), res('target', 'inbox', 'write', 'inbox_state')]}})],
        steps: [{{kind: 'raid', targetId: 'target'}}, executorContext('raid', {{steps: ['raid', 'drip']}})],
    }}['{case}'];
    replies.push(replyWith(broken[1]));
    const review = await prepared(Object.assign({{trigger: element('trigger')}}, broken[0]));
    assert.equal(element('live-operation-heading').textContent, 'Review unavailable');
    assert.equal(element('live-operation-run').textContent, 'Load review again');
    window.AppPanels.requestClose('escape'); assert.equal(await review.done, null); assert.equal(calls.length, 1);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_claims_follow_each_written_resource_and_aliases_intersect(node):
    run_node(
        node,
        EXECUTOR
        + """
(async () => {
    // A Drip whose collection result is unknown claims that playlist and the inbox, not the verified target.
    const drip = await reviewedRun({kind: 'drip', targetId: 'target', trigger: element('trigger')},
        executorContext('drip'), response(executorResult('drip', {collection: 'unknown'},
            [{kind: 'pending_promotion', status: 'unknown'}, {kind: 'execution_record', status: 'complete'}]), 502));
    assert.deepEqual(plain(drip.claims), ['tracks:collection', 'inbox:target']);
    assert.equal(client.hasUnresolved('tracks:collection'), true);
    assert.equal(client.hasUnresolved('tracks:target'), false);
    // A rotation whose archive is that same playlist conflicts once its context names it.
    replies.push(replyWith(rotateContext('collection')));
    const rotate = await prepared({kind: 'schedule', scheduleId: 4, trigger: element('trigger')});
    assert.equal(await rotate.done, null);
    assert.match(element('live-operation-review').dataset.notice, /needs attention/);
    // A rotation over a different archive does not.
    replies.push(replyWith(rotateContext('archive')));
    const other = await prepared({kind: 'schedule', scheduleId: 4, trigger: element('trigger')});
    assert.equal(element('live-operation-heading').textContent, 'Review: Run routine now');
    window.AppPanels.requestClose('escape'); await other.done;
    // Collect writes only the inbox when no collection is linked; the unknown inbox still blocks it.
    assert.equal(client.isBlocked('raid:target'), true); assert.equal(client.isBlocked('privacy:target'), false);
    assert.equal(calls.filter(call => call.options.method === 'POST' && !call.url.endsWith('live-context')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_unknown_target_tracks_block_drip_but_not_inbox_only_collect(node):
    run_node(
        node,
        EXECUTOR
        + """
(async () => {
    replies.push(replyWith(reviewContext('shuffle')));
    const shuffle = await prepared({kind: 'shuffle', targetId: 'target',
        body: {csrf_token: 'x', algorithm: 'BasicShuffle',
        keep_first: '2'}, trigger: element('trigger')});
    runReview(); const handle = await shuffle.done; replies.push(() => {throw new Error('lost');});
    await client.run(handle);
    assert.equal(client.isBlocked('drip:target'), true); assert.equal(client.isBlocked('raid:target'), false);
    const inboxOnly = executorContext('raid', {resources: [res('target', 'target', 'read'),
        res('target', 'inbox', 'write', 'inbox_state')]});
    const collect = await reviewedRun({kind: 'raid', targetId: 'target', trigger: element('trigger')}, inboxOnly,
        response(executorResult('raid', {}, [{kind: 'pending_stage', status: 'not_required'},
            {kind: 'execution_record', status: 'complete'}])));
    assert.equal(collect.complete, true);
    assert.equal(await client.prepare({kind: 'drip', targetId: 'target', trigger: element('trigger')}), null);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize("choice", ["save", "discard"])
def test_draft_conflict_resolves_through_the_page_then_reviews_again_before_a_separate_run(
    node, choice
):
    run_node(
        node,
        EXECUTOR
        + f"""
(async () => {{
    page.draft = ['tracks:target'];
    replies.push(replyWith(executorContext('drip')));
    const review = await prepared({{kind: 'drip', targetId: 'target', trigger: element('trigger')}});
    assert.equal(element('live-operation-heading').textContent, 'Save or discard your draft first');
    assert.equal(element('live-operation-run').textContent, 'Review & save draft');
    assert.equal(element('live-operation-discard').hidden, false);
    assert.match(rendered(element('live-operation-review')), /touch target name/);
    if ('{choice}' === 'save') runReview(); else element('live-operation-discard').click();
    await tick();
    assert.deepEqual(plain(resolutions), [{{type: 'draft', choice: '{choice}'}}]);
    assert.equal(element('live-operation-heading').textContent, 'Finishing your draft…');
    // The page's own review replaces this view; that close is not a cancel.
    panels.filter(panel => panel.id === 'live-operation-panel').pop().options.onClose('replace');
    assert.equal(client.isPreparing(), true);
    page.draft = []; replies.push(replyWith(executorContext('drip')));
    resolver(true); await tick(); await tick(); await tick();
    assert.equal(element('live-operation-heading').textContent, 'Review: Add collected tracks now');
    assert.equal(calls.length, 2); assert.equal(calls.filter(call => call.url.endsWith('drip-now')).length, 0);
    runReview(); const handle = await review.done; replies.push(response(executorResult('drip')));
    assert.equal((await client.run(handle)).complete, true);
    assert.equal(calls.filter(call => call.url.endsWith('drip-now')).length, 1);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_unsaved_or_superseded_draft_resolution_sends_nothing(node):
    run_node(
        node,
        EXECUTOR
        + """
(async () => {
    const notes = [], unspoken = [];
    window.showNotification = (message, type, options) => {
        notes.push(message); unspoken.push(Boolean(options && options.spoken === false));
    };
    page.draft = ['tracks:target'];
    replies.push(replyWith(executorContext('drip')));
    const first = await prepared({kind: 'drip', targetId: 'target', trigger: element('trigger')});
    runReview(); await tick(); resolver(false);
    assert.equal(await first.done, null);
    assert.match(element('live-operation-review').dataset.notice, /draft is still open or needs attention/);
    // The live panel is closed by then, so the page shows it too, as a toast the announcer
    // has already read: it is not spoken twice.
    assert.match(notes[notes.length - 1], /draft is still open or needs attention/);
    assert.equal(unspoken[unspoken.length - 1], true);
    replies.push(replyWith(executorContext('drip')));
    const second = await prepared({kind: 'drip', targetId: 'target', trigger: element('trigger')});
    runReview(); await tick(); const stale = resolver;
    replies.push(replyWith(reviewContext('privacy')));
    const newer = await prepared({kind: 'privacy', targetId: 'target', body: {public: false}, trigger: element('t')});
    assert.equal(await second.done, null);
    stale(true); await tick(); await tick();
    assert.equal(element('live-operation-heading').textContent, 'Review: Make playlist private now');
    window.AppPanels.requestClose('escape'); await newer.done;
    assert.equal(calls.filter(call => !call.url.endsWith('live-context')).length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_refusals_at_run_name_their_own_reason(node):
    run_node(
        node,
        EXECUTOR
        + """
(async () => {
    replies.push(replyWith(executorContext('drip')));
    const busy = await prepared({kind: 'drip', targetId: 'target', trigger: element('trigger')});
    page.busy = true; runReview();
    assert.equal(await busy.done, null);
    assert.match(element('live-operation-review').dataset.notice, /still running/);
    page.busy = false;
    replies.push(replyWith(executorContext('drip')));
    const drafted = await prepared({kind: 'drip', targetId: 'target', trigger: element('trigger')});
    page.draft = ['tracks:target']; runReview();
    assert.equal(await drafted.done, null);
    assert.match(element('live-operation-review').dataset.notice, /draft changed after this review/);
    assert.equal(calls.filter(call => call.url.endsWith('/drip-now')).length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_a_step_that_never_started_says_so_and_claims_nothing(node):
    run_node(
        node,
        EXECUTOR
        + """
(async () => {
    const data = executorResult('raid_and_drip', {}, [{kind: 'pending_stage', status: 'complete'},
        {kind: 'pending_promotion', status: 'not_required'}, {kind: 'execution_record', status: 'complete'}]);
    data.success = false;
    data.operation.aggregate_status = 'partial'; data.operation.error_code = 'drip_disabled';
    data.operation.resources = [resource('collection', 'complete', {step: 'raid', desired_track_uris: [uri(3)]}),
        resource('target', 'not_required', {step: 'drip', dispatched: false, affected_count: 0,
            verification: null, started: false})];
    data.operation.steps = [{step: 'raid', status: 'complete'}, {step: 'drip', status: 'not_started'}];
    const record = await reviewedRun({kind: 'raid_and_drip', targetId: 'target', trigger: element('trigger')},
        executorContext('raid_and_drip'), response(data, 502));
    assert.deepEqual(plain(record.claims), []); assert.equal(record.blocked, false);
    assert.equal(record.attention, true);
    const text = rendered(element('live-operation-results'));
    assert.match(text, /target name · Add collected tracks Did not run — nothing was sent/);
    assert.doesNotMatch(text, /Affected tracks: 0/);
    assert.equal(client.isBlocked('drip:target'), false);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_page_attention_opens_recovery_instead_of_a_review(node):
    run_node(
        node,
        EXECUTOR
        + """
(async () => {
    page.attention = ['tracks:target'];
    assert.equal(await client.prepare({kind: 'drip', targetId: 'target', trigger: element('trigger')}), null);
    assert.equal(attentions.length, 1); assert.equal(calls.length, 0);
    page.attention = ['tracks:archive'];
    replies.push(replyWith(rotateContext('archive')));
    const review = await prepared({kind: 'schedule', scheduleId: 4, trigger: element('trigger')});
    assert.equal(await review.done, null); assert.equal(attentions.length, 2); assert.equal(calls.length, 1);
    page.busy = true;
    assert.equal(await client.prepare({kind: 'raid', targetId: 'target', trigger: element('trigger')}), null);
    assert.equal(calls.length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize("choice", ["discard", "save", "stay"])
def test_draft_navigation_offers_save_or_discard_and_leave(node, choice):
    run_node(
        node,
        EXECUTOR
        + f"""
(async () => {{
    nav.draft = true;
    let warned = false; const unload = () => listeners.beforeunload[0]({{preventDefault() {{warned = true;}}}});
    unload(); assert.equal(warned, true); warned = false;
    window.location = {{href: '/library', assign() {{}}}};
    const decision = window.AppNavigation.assign('/settings'); await tick();
    assert.equal(panels[panels.length - 1].id, 'live-operation-leave');
    assert.equal(element('live-operation-leave-save').hidden, false);
    assert.equal(element('live-operation-leave-discard').hidden, false);
    assert.equal(element('live-operation-leave-review').hidden, true);
    assert.equal(element('live-operation-leave-confirm').hidden, true);
    assert.match(element('live-operation-leave-text').textContent, /draft changes that are not saved/);
    const choice = '{choice}';
    element({{discard: 'live-operation-leave-discard', save: 'live-operation-leave-save',
        stay: 'live-operation-leave-stay'}}[choice]).click(); await tick();
    if (choice !== 'stay') assert.deepEqual(plain(resolutions), [{{type: 'draft', choice}}]);
    else assert.equal(resolutions.length, 0);
    if (choice === 'discard') resolver(true);
    assert.equal(await decision, choice === 'discard');
    unload(); assert.equal(warned, choice !== 'discard');
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_attention_with_a_draft_names_both_losses_and_requires_explicit_leave(node):
    run_node(
        node,
        EXECUTOR
        + """
(async () => {
    nav.draft = true; nav.attention = true;
    const decision = window.AppNavigation.request({kind: 'link', url: '/settings'}); await tick();
    assert.match(element('live-operation-leave-text').textContent, /still need attention.*Unsaved draft changes/);
    assert.equal(element('live-operation-leave-save').hidden, true);
    assert.equal(element('live-operation-leave-confirm').hidden, false);
    element('live-operation-leave-review').click(); assert.equal(await decision, false); await tick();
    assert.equal(attentions.length, 1);
    // Page writes in flight hold navigation; the controller says why instead of asking.
    nav.busy = true;
    assert.equal(await window.AppNavigation.request({kind: 'reload'}), false);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_check_reads_only_unresolved_playlists_and_a_match_resolves_only_that_playlist(node):
    run_node(
        node,
        EXECUTOR
        + """
(async () => {
    const match = id => response({success: true, inspection_only: true, operation: {kind: 'track_inspection',
        aggregate_status: 'complete', retryable: false, error_code: null, bookkeeping: [],
        resources: [resource(id, 'not_required', {step: 'track_inspection', dispatched: false, affected_count: 0,
            current_track_uris: [uri(3)]})]}});
    const mismatch = () => response({success: false, inspection_only: true, operation: {kind: 'track_inspection',
        aggregate_status: 'unknown', retryable: false, error_code: 'intent_not_observed', bookkeeping: [],
        resources: []}}, 409);
    const inspections = () => calls.filter(call => call.url.endsWith('inspect-live'));
    const drip = await reviewedRun({kind: 'drip', targetId: 'target', trigger: element('trigger')},
        executorContext('drip'), response(executorResult('drip', {target: 'unknown', collection: 'complete'},
            [{kind: 'pending_promotion', status: 'complete'}, {kind: 'execution_record', status: 'unknown'}]), 502));
    assert.deepEqual(plain(drip.claims), ['tracks:target']);
    replies.push(match('target'));
    const checked = await client.reconcile(drip.operationId);
    // Only the unresolved playlist is read again, against its own retained order.
    assert.deepEqual(inspections().map(call => call.url), ['/api/playlist/target/inspect-live']);
    assert.deepEqual(JSON.parse(inspections()[0].options.body), {kind: 'tracks', desired_track_uris: [uri(3)]});
    assert.deepEqual(plain(checked.observations[0].resources), [{id: 'target', status: 'match'}]);
    // The match resolves only that playlist; the unknown run-history receipt keeps Attention.
    assert.deepEqual(plain(checked.claims), []); assert.equal(checked.blocked, false);
    assert.equal(checked.attention, true); assert.equal(checked.settled, false);
    assert.equal(client.isBlocked('drip:target'), false);
    assert.match(rendered(element('live-operation-results')), /match the retained order\\. .*still needs attention/);
    assert.doesNotMatch(rendered(element('live-operation-results')), /Check current playlist/);
    const both = await reviewedRun({kind: 'drip', targetId: 'target', trigger: element('trigger')},
        executorContext('drip'), response(executorResult('drip', {target: 'unknown', collection: 'unknown'}), 502));
    assert.deepEqual(plain(both.claims), ['tracks:target', 'tracks:collection']);
    replies.push(match('target'), mismatch());
    const partly = await client.reconcile(both.operationId);
    assert.deepEqual(plain(partly.observations[0].resources), [{id: 'target', status: 'match'},
        {id: 'collection', status: 'mismatch'}]);
    assert.deepEqual(plain(partly.claims), ['tracks:collection']); assert.equal(partly.attention, true);
    // A check of several playlists names each one.
    assert.match(rendered(element('live-operation-results')),
        /target name: Current tracks match the retained order\\. collection name: Current tracks do not match/);
    replies.push(match('collection'));
    const settled = await client.reconcile(both.operationId);
    assert.equal(inspections()[inspections().length - 1].url, '/api/playlist/collection/inspect-live');
    assert.equal(inspections().length, 4);
    assert.equal(settled.settled, true); assert.equal(settled.attention, false);
    assert.deepEqual(plain(settled.claims), []);
    assert.match(rendered(element('live-operation-results')),
        /Checked — the earlier result is kept below.*it does not show which request caused it/);
    const lost = await reviewedRun({kind: 'raid', targetId: 'other', trigger: element('trigger')},
        executorContext('raid', {target: {id: 'other', name: 'Other'}, resources: [res('other', 'target', 'read'),
            res('collection2', 'collection', 'write', 'track_membership'), res('other', 'inbox', 'write',
                'inbox_state')]}), () => {throw new Error('lost');});
    replies.push(response({success: false, inspection_only: true, operation: {kind: 'track_inspection',
        aggregate_status: 'unknown', retryable: false, error_code: 'intent_unavailable', bookkeeping: [],
        resources: []}}, 502));
    const unavailable = await client.reconcile(lost.operationId);
    assert.deepEqual(JSON.parse(calls[calls.length - 1].options.body), {kind: 'tracks'});
    assert.equal(unavailable.observations[0].status, 'unavailable');
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )
