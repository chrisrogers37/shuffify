"""Workshop live actions through the reviewed controller: a server review first, one Run,
an in-place refresh, Save continuation for an overlapping draft, and guarded navigation.

The scenarios run the shipped controller, Workshop save client and the Workshop page's own
functions (sliced from the template) under Node, with small DOM and network stubs.
"""

import json
import re
from unittest.mock import Mock, patch

import pytest

from shuffify.enums import JobType
from shuffify.models.playlist import Playlist
from tests.node_runtime import run_scenario
from tests.page_runtime import PAGE_DOM, page_tree, render_page
from tests.paths import api_path
from tests.test_playlist_operations_runtime import CLIENT, ROOT, run_node
from tests.test_playlist_operations_runtime import node as node
from tests.test_reviewed_live_actions_runtime import REVIEW_FIXTURE
from tests.test_routine_management_runtime import row as routine_row

WORKSHOP = (
    CLIENT
    + REVIEW_FIXTURE
    + r"""
element('live-operation-close').dataset.resultsLabel = 'Back to Workshop';
// Page code focuses headings and disables surface controls; give every stub element those members.
const plainElement = document.getElementById;
document.getElementById = id => {
    const el = plainElement(id);
    el.focus ||= () => {el.focused = true;};
    el.querySelectorAll ||= () => [];
    return el;
};
const counts = {status: 0, pending: 0, archive: 0, render: 0, snapshots: 0};
const notices = [], assigned = [];
let reloads = 0;
window.location = {href: '/workshop/target', assign: url => {assigned.push(url);}, reload: () => {reloads++;}};
const state = {playlistId: 'target', playlistName: 'Target name', savedUris: [uri(1), uri(2)],
    workingUris: [uri(1), uri(2)], isDirty: false, isSaving: false, isFinalizing: false, isShuffling: false,
    liveRun: null, pendingMutations: 0, pendingWrites: 0, configRevision: 0, saveAttention: null,
    saveIntent: null,
    observedCurrent: null, sourceRecovery: [], saveResults: [], review: null, reviewMode: null, reviewMessage: '',
    isPreparing: false, loadedAt: null, locks: {}, archiveQueue: [],
    archivePair: {archive_playlist_id: 'archive', auto_archive_on_remove: true}, archiveTracks: [],
    stagedRestorations: []};
const pendingRaidsState = {tracks: [], stagedPromotions: [], dismissing: new Set()};
const raidPanel = {status: {raid_link: {raid_playlist_id: 'collection', drip_enabled: false},
    schedule: {id: 5, job_type: 'raid_and_drip'}, source_count: 1}, _scheduleId: 5, _historyLoaded: true,
    loadStatus() {counts.status++;}};
Object.assign(context, {workshopState: state, pendingRaidsState, raidPanel, PlaylistOperations: client,
    AppPanels: window.AppPanels, AppNavigation: window.AppNavigation, fetch: window.fetch,
    archiveState: {rotationSchedule: {id: 7}},
    snapshotState: {isLoaded: false, snapshots: [], pendingDeleteId: null, renderPending: false, views: {}},
    playlistSchedules: {loaded: false, isLoading: false, schedules: [], loadSchedules() {}},
    trackDataByUri: {}, sortableInstance: null, prevPlaylistId: 'previous', nextPlaylistId: 'next',
    RAID_TIME_CAPABLE: ['daily', 'every_3d', 'weekly'], showNotification: message => notices.push(message),
    loadPendingRaids: () => {counts.pending++;}, fetchArchiveTracks: async () => {counts.archive++;},
    loadSnapshots: () => {counts.snapshots++;}, fullRerenderTrackList: () => {counts.render++;},
    updateTrackCount() {}, renderPendingRaids() {}, updatePendingBadge() {},
    renderInboxArchiveTracks() {}, updateArchiveQueueBadge() {},
    markDirty() {
        state.isDirty = context.hasWorkshopDraft();
        element('save-btn').disabled = !context.canSaveWorkshop(); context.renderSaveStatus();
    }});
// AppPanels.focusNear as its contract says: the element when it shows, else its closest
// [data-panel-return] section itself. Every call is recorded, and so is every announcement.
const focusMoves = [], announcements = [];
window.AppPanels.announce = message => { announcements.push(message); };
const showing = el => Boolean(el && el.isConnected && el.getClientRects && el.getClientRects().length);
window.AppPanels.focusNear = el => {
    focusMoves.push(el);
    const target = showing(el) ? el : el && el.isConnected && el.closest ? el.closest('[data-panel-return]') : null;
    if (!showing(target)) return false;
    document.activeElement = target;
    return true;
};
// Grouped controls are found by their compatibility group, keyed ones by their live key.
// Each simple selector the page asks for: a held control, a control of one group, a keyed control.
const matchesSelector = (el, selector) => selector.split(',').map(part => part.trim()).some(part =>
    part === '[data-compat-disabled]' ? el.dataset.compatDisabled !== undefined
        : part === '[data-live-operation-key]' ? Boolean(el.dataset.liveOperationKey)
        : part.startsWith('[data-workshop-compat="') ? el.dataset.workshopCompat === part.slice(23, -2) : false);
document.querySelectorAll = selector => [...elements.values()].filter(el => matchesSelector(el, selector));
element('workshop-compat-reasons').replaceChildren = function (...nodes) { this.children = nodes; };
// The shared confirmation answers yes unless a scenario says otherwise, and records each ask.
const confirms = [];
let confirmAnswer = true;
window.AppConfirm = {ask: async options => {confirms.push(options); return confirmAnswer;}};
context.AppConfirm = window.AppConfirm;
// The template gives these controls static keys; dynamic controls get theirs when rendered.
[['workshop-visibility-btn', 'privacy'], ['raid-now-btn', 'raid'], ['drip-now-btn', 'drip'],
    ['raid-and-drip-btn', 'raid_and_drip']].forEach(([id, kind]) => {
    element(id).dataset.liveOperationKey = kind + ':target';
});
vm.runInContext(fs.readFileSync('shuffify/static/js/workshop-save.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('shuffify/static/js/playlist-workspace.js', 'utf8'), context);
context.WorkshopSave = window.WorkshopSave;
context.PlaylistWorkspace = window.PlaylistWorkspace;
const workshop = fs.readFileSync('shuffify/templates/workshop.html', 'utf8');
function slice(from, to) {
    const start = workshop.indexOf(from), end = workshop.indexOf(to, start);
    assert.ok(start >= 0 && end > start, 'template slice not found: ' + from);
    vm.runInContext(workshop.slice(start, end), context);
}
function loadWorkshopMethod(name) {
    const from = workshop.indexOf('    ' + name + ': function(');
    assert.ok(from >= 0, 'method not found: ' + name);
    const end = workshop.indexOf('\n    },', from) + '\n    }'.length;
    return vm.runInContext('({' + workshop.slice(from, end) + '})', context)[name];
}
slice('function workshopBusy()', '// Undo Preview');
slice('function undoPreview()', '// Track List Rendering');
slice('function navigatePlaylist(direction)', '// Playlist Dropdown Selector');
slice('function restoreSnapshot(', '// --- Delete ---');
for (const name of ['raidNow', 'dripNow', 'raidAndDrip']) raidPanel[name] = loadWorkshopMethod(name);
// The Routines page's exact-ID cards call this shared reviewed entry point. Card DOM/action
// wiring has separate rendered-page coverage; these cases retain the live guard matrix.
function startExactRoutine(id) {
    const trigger = element('routine-run-' + id);
    trigger.dataset.liveOperationKey = 'schedule:' + id;
    return context.reviewedRun({kind: 'schedule', scheduleId: id, targetId: 'target', trigger});
}
(listeners.DOMContentLoaded || []).forEach(listener => listener());

// Review contexts and verified results for the Workshop kinds.
const res = (id, role, access, mutation) => ({resource: id, resource_name: id === 'target' ? 'Target name'
    : id + ' name', role, domain: role === 'inbox' ? 'inbox' : 'spotify', access,
    mutation_type: access === 'read' ? null : mutation, field: null});
const collecting = (collection = 'collection') => (collection
    ? [res(collection, 'collection', 'write', 'track_membership')] : []).concat(
    [res('target', 'inbox', 'write', 'inbox_state')]);
const targetWrite = (mutation = 'track_membership') => res('target', 'target', 'write', mutation);
const binding = (before = null) => ({version: 1, scope: SCOPE, before_order: before, history: null});
const bookkeeping = steps => (steps.includes('raid') ? ['pending_stage'] : [])
    .concat(steps.includes('drip') ? ['pending_promotion'] : [], ['execution_record']);
function liveContext(kind, collection = 'collection') {
    const steps = {raid: ['raid'], drip: ['drip'], raid_and_drip: ['raid', 'drip']}[kind];
    return {version: 1, kind, label: {raid: 'Collect for review', drip: 'Add collected tracks now',
        raid_and_drip: 'Collect and add tracks now'}[kind], target: {id: 'target', name: 'Target name'},
        schedule: null, snapshot: null, resources: (kind === 'raid' ? [res('target', 'target', 'read')]
            : [targetWrite()]).concat(collecting(collection)),
        steps, required_bookkeeping: bookkeeping(steps), review_binding: binding(),
        current: {source_count: 1, extra_source_ids: [], collection: null},
        action: kind === 'raid' ? {sources: [], extras: []} : {count: 3}};
}
function routineContext(id, jobType) {
    context.playlistSchedules.schedules = context.playlistSchedules.schedules.filter(row => row.schedule_id !== id)
        .concat([{schedule_id: id, schedule: {job_type: jobType}}]);
    const steps = {raid: ['raid'], raid_and_drip: ['raid', 'drip'], rotate: ['rotate'], shuffle: ['shuffle']}[jobType];
    const resources = jobType === 'rotate' ? [targetWrite(), res('archive', 'archive', 'write', 'track_membership')]
        : jobType === 'shuffle' ? [targetWrite('track_order')]
        : (jobType === 'raid' ? [res('target', 'target', 'read')] : [targetWrite()]).concat(collecting());
    return {version: 1, kind: 'schedule', label: 'Run routine now', target: {id: 'target', name: 'Target name'},
        schedule: {id, job_type: jobType}, snapshot: null, resources, steps,
        required_bookkeeping: bookkeeping(steps), review_binding: binding(), current: {},
        action: {job_type: jobType, algorithm: jobType === 'shuffle' ? {id: 'BasicShuffle', name: 'Basic'} : null,
            params: {},
            rotation: jobType === 'rotate' ? {mode: 'swap', count: 5, cap: 40, protect: 0} : null,
            drip: jobType === 'raid_and_drip' ? {count: 3, gate: 'open'} : null}};
}
function snapshotContext(id = 17) {
    return {version: 1, kind: 'snapshot_restore', label: 'Restore saved snapshot now',
        target: {id: 'target', name: 'Target name'}, schedule: null, snapshot: {id, created_at: 'Oct 9'},
        resources: [targetWrite('track_order')], steps: [], required_bookkeeping: ['session_history'],
        review_binding: binding(BEFORE), current: {track_count: 2, track_uris: [uri(1), uri(2)], loaded_at: 'now'},
        action: {snapshot_id: id, desired_track_uris: [uri(2), uri(1)],
            summary: {added_count: 0, removed_count: 0, order_changed: true}}};
}
const replyWith = ctx => response({success: true, context: ctx});
// Every written playlist and every required bookkeeping fact verified.
function verified(ctx) {
    return {success: true, execution_id: 9, started_at: 'then', execution_state: 'recorded',
        operation: {kind: ctx.kind, aggregate_status: 'complete', retryable: false,
            resources: ctx.resources.filter(item => item.access === 'write' && item.domain === 'spotify')
                .map(item => resource(item.resource, 'complete', {step: ctx.kind, desired_track_uris: [uri(3)]})),
            bookkeeping: ctx.required_bookkeeping.map(kind => ({kind, status: 'complete'}))}};
}
const fresh = (uris = [uri(3), uri(2), uri(1)]) => response({success: true, representable: true,
    current_track_uris: uris, current_tracks: [{uri: uris[0], name: 'Fresh track'}], loaded_at: 'fresh'});
const sourceContext = (extra = {}) => response(Object.assign({success: true,
    target: {id: 'target', name: 'Target name'}}, extra));
const saved = uris => response({success: true, target_status: 'saved', saved_track_uris: uris});
const urls = () => calls.map(call => call.url.split('?')[0]);
const KINDS = {
    drip: {start: () => raidPanel.dripNow(), context: () => liveContext('drip'),
        contextUrl: '/api/playlist/target/live-context', runUrl: '/api/playlist/target/drip-now',
        heading: 'Review: Add collected tracks now', body: {reviewed_scope: SCOPE}, writesTarget: true},
    raid_and_drip: {start: () => raidPanel.raidAndDrip(), context: () => liveContext('raid_and_drip'),
        contextUrl: '/api/playlist/target/live-context', runUrl: '/api/playlist/target/raid-and-drip',
        heading: 'Review: Collect and add tracks now', body: {reviewed_scope: SCOPE}, writesTarget: true},
    routine: {start: () => startExactRoutine(5), context: () => routineContext(5, 'raid_and_drip'),
        contextUrl: '/api/schedules/5/run-context', runUrl: '/api/schedules/5/run', heading: 'Review: Run routine now',
        body: {reviewed_scope: SCOPE}, writesTarget: true},
    rotate: {start: () => startExactRoutine(7), context: () => routineContext(7, 'rotate'),
        contextUrl: '/api/schedules/7/run-context', runUrl: '/api/schedules/7/run', heading: 'Review: Run routine now',
        body: {reviewed_scope: SCOPE}, writesTarget: true},
    restore: {start: () => context.restoreSnapshot(17, element('snapshot-17')), context: () => snapshotContext(17),
        contextUrl: '/api/snapshots/17/restore-context', runUrl: '/api/snapshots/17/restore',
        heading: 'Review: Restore saved snapshot now',
        body: {reviewed_scope: SCOPE, reviewed_before_order: BEFORE}, writesTarget: true},
    collect: {start: () => raidPanel.raidNow(), context: () => liveContext('raid'),
        contextUrl: '/api/playlist/target/live-context', runUrl: '/api/playlist/target/raid-now',
        heading: 'Review: Collect for review', body: {reviewed_scope: SCOPE}, writesTarget: false},
    raid_routine: {start: () => {
            raidPanel.status.schedule = {id: 5, job_type: 'raid'}; return startExactRoutine(5);
        },
        context: () => routineContext(5, 'raid'), contextUrl: '/api/schedules/5/run-context',
        runUrl: '/api/schedules/5/run', heading: 'Review: Run routine now', body: {reviewed_scope: SCOPE},
        writesTarget: false},
};
// Open a kind's review (its context is the only request). The pending run is wrapped so
// awaiting this helper does not also wait for that run.
async function openReview(spec) {
    replies.push(replyWith(spec.context()));
    const running = spec.start(); await tick(); await tick();
    return {running};
}
"""
)

TARGET_KINDS = ["drip", "raid_and_drip", "routine", "rotate", "restore"]


def scenario(body, **values):
    """Fill __NAME__ placeholders, keeping JavaScript braces literal."""
    for name, value in values.items():
        body = body.replace(f"__{name.upper()}__", value)
    return WORKSHOP + body


def test_a_draft_resolution_that_does_not_save_returns_focus_only_when_no_panel_holds_it(node):
    run_node(
        node,
        scenario("""
(async () => {
    const trigger = element('drip-now-btn'); trigger.isConnected = true; trigger.focused = false;
    state.workingUris = [uri(2), uri(1)];
    // Backing out of the Save review leaves no panel open: focus returns to the action.
    const first = (await openReview(KINDS.drip)).running;
    replies.push(sourceContext()); runReview(); await tick(); await tick();
    assert.equal(panels[panels.length - 1].id, 'workshop-save-panel');
    await window.AppPanels.requestClose('escape'); await tick();
    assert.equal(await first, null); assert.equal(trigger.focused, true);
    // A newer Save review the user is still working in keeps focus.
    trigger.focused = false;
    const second = (await openReview(KINDS.drip)).running;
    replies.push(sourceContext()); runReview(); await tick(); await tick();
    document.querySelector = selector => (selector.includes('data-app-panel') ? element('workshop-save-panel') : null);
    replies.push(sourceContext()); await context.commitToSpotify(); await tick();
    assert.equal(await second, null); assert.equal(trigger.focused, false);
    document.querySelector = () => null;
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_review_and_save_from_the_leave_dialog_explains_a_request_in_flight(node):
    run_node(
        node,
        scenario("""
(async () => {
    // A search is in flight: it does not hold navigation, but Save cannot open its review yet.
    state.workingUris = [uri(2), uri(1)]; state.pendingMutations = 1;
    const navigation = context.navigatePlaylist('prev'); await tick(); await tick();
    assert.equal(panels[panels.length - 1].id, 'live-operation-leave');
    const sent = calls.length;
    element('live-operation-leave-save').click(); await tick(); await tick();
    assert.equal(await navigation, false); assert.deepEqual(assigned, []);
    assert.match(notices[notices.length - 1], /still running\\. Review & save your draft when it finishes/);
    assert.equal(calls.length, sent);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_reads_never_hold_navigation_but_writes_do(node):
    run_node(
        node,
        scenario("""
(async () => {
    window.showNotification = message => notices.push(message);
    // A POST read in flight (search, preview, external load) never holds navigation.
    state.pendingMutations = 1; state.pendingWrites = 0;
    assert.equal(await context.navigatePlaylist('next'), true); assert.deepEqual(assigned, ['/workshop/next']);
    // A write in flight does, and says why.
    state.pendingWrites = 1;
    assert.equal(await context.navigatePlaylist('next'), false); assert.equal(assigned.length, 1);
    assert.match(notices[notices.length - 1], /still running/);
    state.pendingMutations = 0; state.pendingWrites = 0;
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_workshop_registers_no_guard_or_unload_listener_of_its_own(node):
    run_node(
        node,
        scenario("""
(async () => {
    assert.equal(guards.length, 1);
    assert.equal(listeners.beforeunload.length, 1);
    // The controller's one unload warning consults this page's draft and saved-work attention.
    let warned = false; const unload = () => listeners.beforeunload[0]({preventDefault() {warned = true;}});
    unload(); assert.equal(warned, false);
    state.workingUris = [uri(2), uri(1)]; unload(); assert.equal(warned, true);
    state.workingUris = [uri(1), uri(2)]; warned = false;
    state.sourceRecovery = [{kind: 'archive', resource: 'archive', status: 'unknown', body: {track_uris: []}}];
    unload(); assert.equal(warned, true);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_page_claims_name_each_drafted_and_recovering_resource(node):
    run_node(
        node,
        scenario("""
(async () => {
    assert.deepEqual(plain(context.workshopDraftClaims()), []);
    state.workingUris = [uri(2), uri(1)];
    assert.deepEqual(plain(context.workshopDraftClaims()), ['tracks:target']);
    // A staged promotion of a track already in the playlist leaves the order alone.
    state.workingUris = [uri(1), uri(2)]; pendingRaidsState.stagedPromotions = [{id: 1, track_uri: uri(2)}];
    assert.deepEqual(plain(context.workshopDraftClaims()), ['tracks:collection', 'inbox:target']);
    pendingRaidsState.stagedPromotions = []; state.archiveQueue = [uri(9)];
    assert.deepEqual(plain(context.workshopDraftClaims()), ['tracks:archive', 'tracks:target']);
    state.archiveQueue = []; state.stagedRestorations = [{uri: uri(1)}];
    assert.deepEqual(plain(context.workshopDraftClaims()), ['tracks:archive', 'tracks:target']);
    // An open Save review's resolved playlists count too.
    state.review = {sources: [{kind: 'restorations', resource: 'older-archive'}]};
    assert.deepEqual(plain(context.workshopDraftClaims()), ['tracks:archive', 'tracks:older-archive', 'tracks:target']);
    state.review = null; state.stagedRestorations = [];
    assert.deepEqual(plain(context.workshopAttentionClaims()), []);
    state.sourceRecovery = [{kind: 'raids', resource: 'collection', status: 'failed', body: {track_ids: [1]}},
        {kind: 'archive', resource: 'archive', status: 'unknown', body: {track_uris: [uri(9)]}}];
    // Repeated claims are harmless: overlaps are only counted and named.
    assert.deepEqual([...new Set(plain(context.workshopAttentionClaims()))],
        ['tracks:collection', 'inbox:target', 'tracks:target', 'tracks:archive']);
    state.sourceRecovery = []; state.saveAttention = {retryable: false, message: 'Unknown'};
    assert.deepEqual(plain(context.workshopAttentionClaims()), ['tracks:target']);
    state.saveAttention = null;
    assert.equal(context.workshopPageBusy(), false);
    for (const flag of ['isSaving', 'isFinalizing', 'isPreparing', 'isShuffling']) {
        state[flag] = true; assert.equal(context.workshopPageBusy(), true); state[flag] = false;
    }
    state.pendingMutations = 1; assert.equal(context.workshopPageBusy(), true);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_collect_with_a_track_draft_is_reviewed_and_run_without_touching_the_draft(
    node,
):
    run_node(
        node,
        scenario("""
(async () => {
    state.workingUris = [uri(2), uri(1)];
    const running = (await openReview(KINDS.collect)).running;
    assert.equal(calls[0].url, '/api/playlist/target/live-context');
    assert.deepEqual(JSON.parse(calls[0].options.body), {kind: 'raid'});
    assert.equal(element('live-operation-heading').textContent, 'Review: Collect for review');
    replies.push(response(verified(liveContext('raid'))));
    runReview(); const record = await running;
    assert.equal(record.complete, true);
    assert.deepEqual(urls(), ['/api/playlist/target/live-context', '/api/playlist/target/raid-now']);
    assert.deepEqual(JSON.parse(calls[1].options.body), {reviewed_scope: SCOPE});
    assert.deepEqual(plain(state.workingUris), [uri(2), uri(1)]);
    assert.deepEqual(plain(state.savedUris), [uri(1), uri(2)]);
    // Collecting refreshes raid status and the inbox only.
    assert.deepEqual(counts, {status: 1, pending: 1, archive: 0, render: 0, snapshots: 0});
    assert.equal(state.liveRun, null);
    // Without a linked collection, Collect writes only the inbox and is still reviewed and run.
    replies.push(replyWith(liveContext('raid', null)));
    const inboxOnly = raidPanel.raidNow(); await tick(); await tick();
    assert.equal(element('live-operation-heading').textContent, 'Review: Collect for review');
    replies.push(response(verified(liveContext('raid', null))));
    runReview(); assert.equal((await inboxOnly).complete, true);
    assert.deepEqual(plain(state.workingUris), [uri(2), uri(1)]);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_source_staging_that_collect_would_touch_blocks_it_until_resolved(node):
    run_node(
        node,
        scenario("""
(async () => {
    // Staged inbox promotions save into the collection and the inbox, which Collect also writes.
    pendingRaidsState.stagedPromotions = [{id: 1, track_uri: uri(1)}];
    const blocked = (await openReview(KINDS.collect)).running;
    assert.equal(element('live-operation-heading').textContent, 'Save or discard your draft first');
    assert.match(rendered(element('live-operation-review')), /touch collection name and the review inbox/);
    await window.AppPanels.requestClose('escape');
    assert.equal(await blocked, null);
    assert.equal(pendingRaidsState.stagedPromotions.length, 1);
    // An archive queue conflicts only when the archive is that same collection.
    pendingRaidsState.stagedPromotions = []; state.archiveQueue = [uri(9)];
    const distinct = (await openReview(KINDS.collect)).running;
    assert.equal(element('live-operation-heading').textContent, 'Review: Collect for review');
    await window.AppPanels.requestClose('escape'); assert.equal(await distinct, null);
    state.archivePair = {archive_playlist_id: 'collection', auto_archive_on_remove: true};
    const aliased = (await openReview(KINDS.collect)).running;
    assert.equal(element('live-operation-heading').textContent, 'Save or discard your draft first');
    await window.AppPanels.requestClose('escape'); assert.equal(await aliased, null);
    assert.deepEqual(plain(state.archiveQueue), [uri(9)]);
    assert.equal(calls.filter(call => !call.url.endsWith('live-context')).length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


@pytest.mark.parametrize("kind", TARGET_KINDS)
def test_target_action_with_a_draft_continues_only_after_the_real_save_then_needs_its_own_run(
    node, kind
):
    run_node(
        node,
        scenario(
            """
(async () => {
    const spec = KINDS['__KIND__'];
    state.workingUris = [uri(2), uri(1)];
    const running = (await openReview(spec)).running;
    assert.equal(calls[0].url, spec.contextUrl);
    assert.equal(element('live-operation-heading').textContent, 'Save or discard your draft first');
    assert.match(rendered(element('live-operation-review')), /touch Target name/);
    // Review & save opens the common Save review; nothing is saved or run yet.
    replies.push(sourceContext());
    runReview(); await tick(); await tick();
    assert.equal(panels[panels.length - 1].id, 'workshop-save-panel');
    assert.equal(state.reviewMode, 'review');
    assert.equal(element('live-operation-heading').textContent, 'Finishing your draft…');
    assert.deepEqual(urls(), [spec.contextUrl, '/api/workshop/target/source-context']);
    // Only the real Save continues the action, and only to a fresh review.
    replies.push(saved([uri(2), uri(1)]), replyWith(spec.context()));
    await context.saveReviewedWorkshop(); await tick(); await tick();
    assert.equal(element('live-operation-heading').textContent, spec.heading);
    assert.deepEqual(urls(), [spec.contextUrl, '/api/workshop/target/source-context', '/api/workshop/target/commit',
        spec.contextUrl]);
    // Its Run is still a separate choice, sent once with the server's bindings only.
    const keyOf = track => context.draftOccurrences().find(item => item.uri === track).key;
    const kept = [keyOf(uri(2)), keyOf(uri(1))];
    replies.push(response(verified(spec.context())), fresh());
    runReview(); const record = await running;
    assert.equal(record.complete, true);
    assert.equal(calls[4].url, spec.runUrl);
    assert.deepEqual(JSON.parse(calls[4].options.body), spec.body);
    assert.equal(calls.filter(call => call.url === spec.runUrl).length, 1);
    assert.deepEqual(plain(state.savedUris), [uri(3), uri(2), uri(1)]);
    assert.deepEqual(plain(state.workingUris), [uri(3), uri(2), uri(1)]);
    // The verified order is adopted through remap: each track keeps its row's key.
    assert.deepEqual([keyOf(uri(2)), keyOf(uri(1))], kept);
    assert.equal(new Set(plain(state.occurrences).map(item => item.key)).size, 3);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
            kind=kind,
        ),
    )


@pytest.mark.parametrize("moment", ["conflict", "save_review", "live_review"])
def test_cancelling_at_any_review_sends_nothing(node, moment):
    run_node(
        node,
        scenario(
            """
(async () => {
    const moment = '__MOMENT__';
    state.workingUris = [uri(2), uri(1)];
    const running = (await openReview(KINDS.drip)).running;
    if (moment !== 'conflict') {
        replies.push(sourceContext()); runReview(); await tick(); await tick();
        assert.equal(panels[panels.length - 1].id, 'workshop-save-panel');
    }
    if (moment === 'live_review') {
        replies.push(saved([uri(2), uri(1)]), replyWith(liveContext('drip')));
        await context.saveReviewedWorkshop(); await tick(); await tick();
        assert.equal(element('live-operation-heading').textContent, 'Review: Add collected tracks now');
    }
    // Back, Cancel and Escape all close the view that is open now.
    await window.AppPanels.requestClose('escape'); await tick();
    assert.equal(await running, null);
    assert.equal(calls.filter(call => call.url.endsWith('/drip-now')).length, 0);
    assert.equal(calls.filter(call => call.url.endsWith('/commit')).length, moment === 'live_review' ? 1 : 0);
    if (moment === 'save_review') {
        assert.match(element('live-operation-review').dataset.notice, /draft is still open or needs attention/);
        assert.deepEqual(plain(state.workingUris), [uri(2), uri(1)]);
    }
    assert.equal(client.getRecords().length, 0); assert.equal(client.isPreparing(), false);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
            moment=moment,
        ),
    )


@pytest.mark.parametrize(
    "event,outcome",
    [
        ("save", "saved"),
        ("stale_target", "attention"),
        ("failed_source", "attention"),
        ("review_again", "changed"),
        ("close_review", "cancelled"),
        ("open_results", "cancelled"),
        ("discard", "cancelled"),
        ("use_current", "cancelled"),
    ],
)
def test_save_completion_signal_settles_for_exactly_the_reviewed_save(
    node, event, outcome
):
    run_node(
        node,
        scenario(
            """
(async () => {
    const event = '__EVENT__';
    state.workingUris = [uri(2), uri(1)];
    if (event === 'failed_source') pendingRaidsState.stagedPromotions = [{id: 1, track_uri: uri(2)}];
    replies.push(sourceContext(event === 'failed_source' ? {raid: {id: 'collection', name: 'collection name'}} : {}));
    const review = await context.commitToSpotify();
    assert.equal(review, state.review); assert.equal(state.reviewMode, 'review');
    const signal = context.awaitWorkshopSave(review);
    let settled = null; signal.then(value => {settled = value;});
    // Closing the review unsaved, or leaving it for its results, abandons the reviewed Save.
    if (event === 'close_review') await window.AppPanels.requestClose('close');
    if (event === 'open_results') {
        await context.openWorkshopResults(); await tick();
        assert.equal(settled, 'cancelled'); assert.equal(state.reviewMode, 'results');
    }
    if (event === 'save') {replies.push(saved([uri(2), uri(1)])); await context.saveReviewedWorkshop();}
    if (event === 'stale_target') {
        replies.push(response({success: false, target_status: 'not_written', error_code: 'stale_draft',
            retryable: false, current_track_uris: [uri(5)], message: 'Spotify changed.'}, 409));
        await context.saveReviewedWorkshop();
    }
    if (event === 'failed_source') {
        replies.push(saved([uri(2), uri(1)]), response({success: false, resource: 'collection', status: 'failed',
            retryable: true, error_code: 'write_rejected', message: 'Rejected.'}, 502));
        await context.saveReviewedWorkshop();
        assert.equal(state.sourceRecovery.length, 1);
    }
    if (event === 'review_again') {replies.push(sourceContext()); await context.commitToSpotify();}
    if (event === 'discard') context.undoPreview();
    if (event === 'use_current') {
        state.observedCurrent = {uris: [uri(4)], loadedAt: 'now', tracks: []}; context.useCurrentSpotify();
    }
    assert.equal(await signal, '__OUTCOME__');
    assert.equal(calls.filter(call => /drip-now|raid-now|schedules|snapshots/.test(call.url)).length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
            event=event,
            outcome=outcome,
        ),
    )


def test_resolve_conflict_save_is_true_only_for_a_saved_review_and_superseded_waits_end(
    node,
):
    run_node(
        node,
        scenario("""
(async () => {
    state.workingUris = [uri(2), uri(1)];
    replies.push(sourceContext());
    const first = context.resolveWorkshopConflict({type: 'draft', choice: 'save'}); await tick(); await tick();
    // A newer conflict's review supersedes the older wait.
    replies.push(sourceContext());
    const second = context.resolveWorkshopConflict({type: 'draft', choice: 'save'}); await tick(); await tick();
    assert.equal(await first, false);
    replies.push(saved([uri(2), uri(1)])); await context.saveReviewedWorkshop();
    assert.equal(await second, true);
    // A draft already gone needs no Save review: the action continues to a fresh review.
    const before = panels.length;
    assert.equal(await context.resolveWorkshopConflict({type: 'draft', choice: 'save'}), true);
    assert.equal(panels.length, before);
    // Saved work needing attention holds Save; its results open instead.
    state.workingUris = [uri(1), uri(2)];
    state.sourceRecovery = [{kind: 'archive', resource: 'archive', status: 'unknown', body: {track_uris: []}}];
    const opened = panels.length;
    assert.equal(await context.resolveWorkshopConflict({type: 'draft', choice: 'save'}), false);
    assert.equal(panels[opened].id, 'workshop-save-panel'); assert.equal(state.reviewMode, 'results');
    assert.equal(await context.resolveWorkshopConflict({type: 'attention'}), false);
    assert.equal(calls.filter(call => call.url.endsWith('/commit')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_discard_is_local_keeps_source_recovery_and_still_needs_a_fresh_review_and_run(
    node,
):
    run_node(
        node,
        scenario("""
(async () => {
    const recovery = {kind: 'archive', resource: 'archive', status: 'unknown', body: {track_uris: [uri(9)]}};
    state.sourceRecovery = [recovery];
    pendingRaidsState.stagedPromotions = [{id: 1, track_uri: uri(1)}];
    const running = (await openReview(KINDS.collect)).running;
    assert.equal(element('live-operation-heading').textContent, 'Save or discard your draft first');
    assert.equal(element('live-operation-discard').hidden, false);
    replies.push(replyWith(liveContext('raid')));
    element('live-operation-discard').click(); await tick(); await tick();
    assert.equal(pendingRaidsState.stagedPromotions.length, 0);
    assert.deepEqual(pendingRaidsState.tracks.map(track => track.id), [1]);
    assert.equal(state.sourceRecovery[0], recovery); assert.equal(state.sourceRecovery.length, 1);
    assert.equal(element('live-operation-heading').textContent, 'Review: Collect for review');
    assert.equal(calls.filter(call => call.url.endsWith('/raid-now')).length, 0);
    replies.push(response(verified(liveContext('raid'))));
    runReview(); assert.equal((await running).complete, true);
    assert.deepEqual(urls(), ['/api/playlist/target/live-context', '/api/playlist/target/live-context',
        '/api/playlist/target/raid-now']);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_saved_work_needing_attention_opens_its_results_instead_of_a_review(node):
    run_node(
        node,
        scenario("""
(async () => {
    state.sourceRecovery = [{kind: 'archive', resource: 'archive', status: 'unknown', body: {track_uris: [uri(9)]}}];
    assert.equal(await raidPanel.dripNow(), null);
    assert.equal(panels[panels.length - 1].id, 'workshop-save-panel'); assert.equal(state.reviewMode, 'results');
    assert.equal(calls.length, 0);
    // A rotation into the recovering archive is caught once its review names that archive.
    state.sourceRecovery = [{kind: 'archive', resource: 'archive', status: 'unknown', body: {track_uris: []}}];
    state.saveAttention = null;
    const rotate = (await openReview(KINDS.rotate)).running;
    assert.equal(await rotate, null); assert.equal(calls.length, 1);
    assert.equal(panels[panels.length - 1].id, 'workshop-save-panel');
    // Visibility is compatible with the recovery and the draft.
    state.workingUris = [uri(2), uri(1)]; element('workshop-visibility-btn').dataset.public = 'true';
    replies.push(contextReply('privacy'));
    const privacy = context.toggleWorkshopVisibility(); await tick(); await tick();
    assert.equal(element('live-operation-heading').textContent, 'Review: Make playlist private now');
    await window.AppPanels.requestClose('escape'); assert.equal(await privacy, null);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_privacy_keeps_draft_staging_and_recovery_and_patches_only_visibility(node):
    run_node(
        node,
        scenario("""
(async () => {
    state.workingUris = [uri(2), uri(1)]; state.archiveQueue = [uri(9)]; state.stagedRestorations = [{uri: uri(8)}];
    state.sourceRecovery = [{kind: 'archive', resource: 'archive', status: 'unknown', body: {track_uris: [uri(7)]}}];
    state.saveAttention = {retryable: false, message: 'Unknown save'};
    pendingRaidsState.stagedPromotions = [{id: 1, track_uri: uri(6)}];
    const retained = [state.workingUris, state.savedUris, state.archiveQueue, state.stagedRestorations,
        state.sourceRecovery, pendingRaidsState.stagedPromotions, state.saveAttention];
    const button = element('workshop-visibility-btn'); button.dataset.public = 'true';
    // A busy page sends nothing, not even a review read.
    state.pendingMutations = 1; assert.equal(await context.toggleWorkshopVisibility(), null);
    assert.equal(calls.length, 0); state.pendingMutations = 0;
    replies.push(contextReply('privacy'));
    const pending = context.toggleWorkshopVisibility(); await tick(); await tick();
    assert.deepEqual(JSON.parse(calls[0].options.body), {kind: 'privacy', public: false});
    replies.push(response({success: true, public: false, message: 'Done.', operation: {kind: 'privacy',
        aggregate_status: 'complete', retryable: false, bookkeeping: [], resources: [resource('target', 'complete',
            {step: 'privacy', mutation_type: 'metadata', verification: 'metadata', desired_metadata: {public: false},
                current_metadata: {public: false}})]}}));
    runReview(); const record = await pending;
    assert.equal(record.complete, true);
    // The controller announces the outcome and its results show it; the page adds no toast.
    assert.equal(notices.some(notice => notice.startsWith('Playlist set to')), false);
    assert.equal(calls[1].url, '/api/playlist/target/toggle-visibility');
    assert.deepEqual(JSON.parse(calls[1].options.body), {public: false, reviewed_scope: SCOPE});
    assert.equal(button.dataset.public, 'false'); assert.equal(button.textContent, 'Make public now');
    assert.equal(element('workshop-visibility-state').textContent, 'This playlist is private on Spotify.');
    assert.deepEqual(counts, {status: 0, pending: 0, archive: 0, render: 0, snapshots: 0});
    [state.workingUris, state.savedUris, state.archiveQueue, state.stagedRestorations, state.sourceRecovery,
        pendingRaidsState.stagedPromotions, state.saveAttention]
        .forEach((value, i) => assert.equal(value, retained[i]));
    // A lost visibility reply blocks only another visibility write, never Save.
    replies.push(replyWith(reviewContext('privacy', {label: 'Make playlist public now',
        current: {public: false, loaded_at: 'now'}, action: {public: true}})), () => {throw new Error('lost');});
    const lost = context.toggleWorkshopVisibility(); await tick(); await tick();
    assert.deepEqual(JSON.parse(calls[2].options.body), {kind: 'privacy', public: true});
    runReview(); assert.equal((await lost).aggregate, 'unknown');
    assert.equal(button.disabled, true);
    const reads = calls.length;
    assert.equal(await context.toggleWorkshopVisibility(), null); assert.equal(calls.length, reads);
    state.saveAttention = null; state.sourceRecovery = [];
    assert.equal(context.canSaveWorkshop(), true);
    // A matching check patches only the observed visibility.
    replies.push(response({success: true, inspection_only: true, public: true, operation: {kind: 'privacy',
        aggregate_status: 'complete', retryable: false, error_code: null, bookkeeping: [],
        resources: [resource('target', 'not_required', {step: 'privacy', dispatched: false, affected_count: 0,
            verification: 'metadata', current_metadata: {public: true}, desired_metadata: {public: true}})]}}));
    const checked = await client.reconcile(client.getRecords()[1].operationId);
    assert.equal(checked.blocked, false); assert.equal(button.dataset.public, 'true');
    assert.equal(calls.filter(call => call.url.endsWith('/toggle-visibility')).length, 2);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_lost_reply_blocks_replay_and_save_until_checked(node):
    run_node(
        node,
        scenario("""
(async () => {
    replies.push(replyWith(liveContext('drip')), () => {throw new Error('response lost');});
    const running = raidPanel.dripNow(); await tick(); await tick();
    runReview(); const record = await running;
    assert.equal(record.aggregate, 'unknown');
    assert.deepEqual(plain(record.claims), ['tracks:target', 'tracks:collection', 'inbox:target']);
    assert.equal(element('drip-now-btn').disabled, true);
    assert.equal(element('raid-now-btn').disabled, true);
    assert.equal(element('workshop-visibility-btn').disabled, false);
    const sent = calls.length;
    assert.equal(await raidPanel.dripNow(), null); assert.equal(await raidPanel.raidNow(), null);
    assert.equal(calls.length, sent);
    state.workingUris = [uri(2), uri(1)]; context.markDirty();
    assert.equal(context.canSaveWorkshop(), false);
    assert.equal(element('workshop-save-state').textContent, 'Attention');
    assert.match(element('workshop-save-message').textContent, /live action needs attention/);
    // Nothing refreshed or adopted after an unknown result.
    assert.deepEqual(counts, {status: 0, pending: 0, archive: 0, render: 0, snapshots: 0});
    assert.equal(calls.filter(call => call.url.endsWith('/drip-now')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_a_matching_check_after_a_lost_restore_releases_save_but_keeps_attention(node):
    run_node(
        node,
        scenario("""
(async () => {
    const running = (await openReview(KINDS.restore)).running;
    replies.push(() => {throw new Error('response lost');});
    runReview(); const record = await running;
    assert.deepEqual(plain(record.claims), ['tracks:target', 'history:*']);
    state.workingUris = [uri(1)]; context.markDirty();
    assert.equal(context.canSaveWorkshop(), false); assert.equal(element('save-btn').disabled, true);
    // The check reads the target against the restore's retained order.
    replies.push(response({success: true, inspection_only: true, operation: {kind: 'track_inspection',
        aggregate_status: 'complete', retryable: false, error_code: null, bookkeeping: [],
        resources: [resource('target', 'not_required', {step: 'track_inspection', dispatched: false,
            affected_count: 0, current_track_uris: [uri(2), uri(1)]})]}}));
    const checked = await client.reconcile(record.operationId);
    assert.deepEqual(JSON.parse(calls[calls.length - 1].options.body),
        {kind: 'tracks', desired_track_uris: [uri(2), uri(1)]});
    // The playlist is resolved, so Save may run; unknown saved history still needs attention.
    assert.deepEqual(plain(checked.claims), ['history:*']); assert.equal(checked.attention, true);
    // The page hears the change itself: Save is enabled without another edit.
    assert.equal(context.canSaveWorkshop(), true); assert.equal(element('save-btn').disabled, false);
    assert.equal(calls.filter(call => call.url.endsWith('/restore')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


@pytest.mark.parametrize("kind", TARGET_KINDS + ["collect", "raid_routine"])
def test_verified_actions_refresh_in_place_without_reload(node, kind):
    run_node(
        node,
        scenario(
            """
(async () => {
    const spec = KINDS['__KIND__'];
    context.snapshotState.isLoaded = true;
    const running = (await openReview(spec)).running;
    assert.equal(element('live-operation-heading').textContent, spec.heading);
    assert.equal(calls.length, 1);
    replies.push(response(verified(spec.context())));
    if (spec.writesTarget) replies.push(fresh());
    runReview(); const record = await running;
    assert.equal(record.complete, true); assert.equal(record.attention, false);
    assert.equal(calls[1].url, spec.runUrl);
    assert.equal(calls.filter(call => call.url.endsWith('/current-state')).length, spec.writesTarget ? 1 : 0);
    // Only a run that wrote the archive reads it again.
    assert.deepEqual(counts, {status: 1, pending: 1, archive: '__KIND__' === 'rotate' ? 1 : 0,
        render: spec.writesTarget ? 1 : 0, snapshots: '__KIND__' === 'restore' ? 1 : 0});
    if (spec.writesTarget) {
        assert.deepEqual(plain(state.savedUris), [uri(3), uri(2), uri(1)]);
        assert.deepEqual(plain(state.workingUris), [uri(3), uri(2), uri(1)]);
        assert.equal(context.trackDataByUri[uri(3)].name, 'Fresh track'); assert.equal(state.loadedAt, 'fresh');
    } else {
        assert.deepEqual(plain(state.savedUris), [uri(1), uri(2)]);
    }
    assert.equal(reloads, 0); assert.deepEqual(assigned, []); assert.equal(state.liveRun, null);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
            kind=kind,
        ),
    )


def test_refresh_never_overwrites_work_that_arrives_during_the_fresh_read(node):
    run_node(
        node,
        scenario("""
(async () => {
    const running = (await openReview(KINDS.drip)).running;
    replies.push(response(verified(liveContext('drip'))), () => {
        state.workingUris = [uri(9)]; state.sourceRecovery = [{kind: 'raids', resource: null, status: 'failed',
            body: {track_ids: [4]}}];
        return fresh([uri(5)]);
    });
    runReview(); const record = await running;
    assert.equal(record.complete, true); assert.equal(record.displayAttention, 'complete');
    assert.equal(record.attention, true);
    assert.deepEqual(plain(state.workingUris), [uri(9)]); assert.deepEqual(plain(state.savedUris), [uri(1), uri(2)]);
    assert.equal(state.sourceRecovery.length, 1); assert.equal(counts.render, 0);
    // Status and inbox still refresh; the verified result asks for a later update.
    assert.equal(counts.status, 1); assert.equal(counts.pending, 1); assert.equal(counts.archive, 0);
    assert.match(rendered(element('live-operation-results')), /Update this view/);
    assert.equal(reloads, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_unrelated_draft_keeps_the_view_until_update_this_view_adopts_the_verified_playlist(
    node,
):
    run_node(
        node,
        scenario("""
(async () => {
    // A staged promotion of a track already in the playlist claims only the collection and inbox,
    // so a restore (target only) runs; its verified result must not replace the draft view.
    pendingRaidsState.stagedPromotions = [{id: 1, track_uri: uri(1)}];
    const running = (await openReview(KINDS.restore)).running;
    assert.equal(element('live-operation-heading').textContent, 'Review: Restore saved snapshot now');
    replies.push(response(verified(snapshotContext(17))));
    runReview(); const record = await running;
    assert.equal(record.complete, true); assert.equal(record.displayAttention, 'complete');
    assert.equal(calls.filter(call => call.url.endsWith('/current-state')).length, 0);
    assert.equal(pendingRaidsState.stagedPromotions.length, 1); assert.equal(counts.render, 0);
    // Once the draft is gone, "Update this view" adopts the verified playlist without resending.
    context.undoPreview();
    replies.push(fresh([uri(2), uri(1)]));
    const update = {dataset: {liveRefresh: String(record.operationId)}};
    await element('live-operation-results').click({target: {closest: () => update}}); await tick(); await tick();
    assert.deepEqual(plain(state.savedUris), [uri(2), uri(1)]);
    assert.equal(client.getRecords()[0].displayAttention, false);
    assert.equal(calls.filter(call => call.url.endsWith('/restore')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_inbox_and_archive_refreshes_keep_staged_and_recovering_rows_out(node):
    run_node(
        node,
        scenario("""
(async () => {
    pendingRaidsState.stagedPromotions = [{id: 1, track_uri: uri(1)}];
    state.sourceRecovery = [{kind: 'raids', resource: 'collection', status: 'failed', body: {track_ids: [2]}}];
    context.workshopRequest = async () => response({tracks: [{id: 1}, {id: 2}, {id: 3}]});
    slice('function visiblePendingTracks(', 'function renderPendingRaids()');
    context.loadPendingRaids(); await tick();
    assert.deepEqual(plain(pendingRaidsState.tracks), [{id: 3}]);
    assert.equal(pendingRaidsState.stagedPromotions[0].id, 1);
    state.stagedRestorations = [{uri: uri(4)}];
    state.sourceRecovery.push({kind: 'restorations', resource: 'archive', status: 'unknown',
        body: {track_uris: [uri(5)]}});
    context.workshopRequest = async () => response({tracks: [{uri: uri(6)}, {uri: uri(5)}, {uri: uri(4)}], total: 3});
    Object.assign(context, {archiveInboxLoaded: false});
    slice('function fetchArchiveTracks(', 'function loadArchiveTracks()');
    await context.fetchArchiveTracks(true);
    assert.deepEqual(plain(state.archiveTracks), [{uri: uri(6)}]);
    assert.deepEqual(plain(state.stagedRestorations), [{uri: uri(4)}]);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_manual_drip_available_with_paused_automatic_drip_but_busy_and_unknown_block(
    node,
):
    run_node(
        node,
        scenario("""
(async () => {
    const render = loadWorkshopMethod('render');
    Object.assign(raidPanel, {renderRaidLink() {}, renderSources() {}, renderSchedule() {}});
    raidPanel.status = {raid_link: {raid_playlist_id: 'collection', drip_enabled: false}, source_count: 1};
    render.call(raidPanel);
    assert.equal(element('drip-now-btn').disabled, false); assert.equal(element('raid-now-btn').disabled, false);
    // While a run is Busy the controller holds every live control; a status render during it
    // never stores Busy as Drip's own state, so the control returns once the run ends.
    let finish;
    replies.push(replyWith(liveContext('drip')), () => new Promise(resolve => {finish = resolve;}), fresh());
    const running = raidPanel.dripNow(); await tick(); await tick(); runReview(); await tick();
    assert.equal(state.liveRun.kind, 'drip');
    render.call(raidPanel); assert.equal(element('drip-now-btn').disabled, true);
    finish(response(verified(liveContext('drip')))); assert.equal((await running).complete, true);
    assert.equal(state.liveRun, null);
    // Lifting the controller's block restores Drip's own state at once, before any re-render.
    assert.equal(element('drip-now-btn').disabled, false);
    render.call(raidPanel); assert.equal(element('drip-now-btn').disabled, false);
    replies.push(replyWith(liveContext('drip')), () => {throw new Error('response lost');});
    const lost = raidPanel.dripNow(); await tick(); await tick(); runReview(); await lost;
    // A later status render cannot re-enable a control the unknown result blocks.
    render.call(raidPanel);
    assert.equal(element('drip-now-btn').disabled, true); assert.equal(element('raid-now-btn').disabled, true);
    raidPanel.status.source_count = 0; render.call(raidPanel);
    assert.equal(element('raid-now-btn').disabled, true);
    assert.equal(raidPanel.status.raid_link.drip_enabled, false);
    assert.equal(calls.filter(call => call.url.endsWith('/drip-now')).length, 2);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_pausing_automatic_drip_counts_as_a_configuration_change_and_keeps_manual_state(
    node,
):
    run_node(
        node,
        scenario("""
(async () => {
    const settings = () => calls.filter(call => call.url.endsWith('/raid-link'));
    const saved = enabled => response({raid_link: {raid_playlist_id: 'collection', drip_enabled: enabled}});
    raidPanel.status = {raid_link: {raid_playlist_id: 'collection', drip_enabled: true}};
    raidPanel.renderRaidLink = () => {};
    const toggle = loadWorkshopMethod('toggleDripEnabled');
    replies.push(saved(false));
    toggle.call(raidPanel); await tick(); await tick(); await tick();
    assert.equal(raidPanel.status.raid_link.drip_enabled, false); assert.equal(state.configRevision, 1);
    assert.deepEqual(JSON.parse(settings()[0].options.body), {drip_enabled: false});
    assert.equal(settings()[0].options.method, 'PUT'); assert.equal(element('drip-now-btn').disabled, false);
    replies.push(replyWith(liveContext('drip')), () => {throw new Error('response lost');});
    const lost = raidPanel.dripNow(); await tick(); await tick(); runReview(); await lost;
    replies.push(saved(true));
    toggle.call(raidPanel); await tick(); await tick(); await tick();
    assert.equal(raidPanel.status.raid_link.drip_enabled, true); assert.equal(state.configRevision, 2);
    assert.equal(element('drip-now-btn').disabled, true);
    assert.equal(calls.filter(call => call.url.endsWith('/drip-now')).length, 1); assert.equal(settings().length, 2);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_saved_configuration_changes_invalidate_an_open_review_but_collect_ignores_track_edits(
    node,
):
    run_node(
        node,
        scenario("""
(async () => {
    const drip = (await openReview(KINDS.drip)).running;
    context.noteWorkshopConfigChange(); runReview();
    assert.equal(await drip, null);
    assert.match(element('live-operation-review').dataset.notice, /options changed/);
    // Collecting does not depend on the track draft, so editing tracks keeps its review.
    const collect = (await openReview(KINDS.collect)).running;
    state.workingUris = [uri(2), uri(1)];
    replies.push(response(verified(liveContext('raid'))));
    runReview(); assert.equal((await collect).complete, true);
    // Staging an inbox promotion during its review does invalidate it.
    const restaged = (await openReview(KINDS.collect)).running;
    pendingRaidsState.stagedPromotions = [{id: 4, track_uri: uri(2)}]; runReview();
    assert.equal(await restaged, null);
    assert.equal(calls.filter(call => call.url.endsWith('/drip-now')).length, 0);
    assert.equal(calls.filter(call => call.url.endsWith('/raid-now')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_routine_and_snapshot_controls_are_keyed_to_what_they_run(node):
    run_node(
        node,
        scenario("""
(async () => {
    // Exact card wiring is checked on the rendered Workshop below; these controls
    // exercise per-ID claims in the shared live guard independently of list rendering.
    for (const id of [5, 7]) element('routine-run-' + id).dataset.liveOperationKey = 'schedule:' + id;
    replies.push(replyWith(routineContext(7, 'rotate')), () => {throw new Error('lost');});
    const lost = startExactRoutine(7); await tick(); await tick(); runReview();
    assert.deepEqual(plain((await lost).claims), ['tracks:target', 'tracks:archive']);
    assert.equal(element('routine-run-7').disabled, true);
    assert.equal(element('routine-run-5').disabled, false);
    Object.assign(context, {escapeHtml: String, escapeAttr: String, formatRelativeTime: () => 'now',
        getDateGroup: () => 'Today', humanizeDescription: value => value || '', SNAPSHOT_TYPE_CONFIG: {}});
    // The manual snapshot form starts hidden, as in the template.
    element('snapshot-create-form').classList = {contains: name => name === 'hidden', add() {}, remove() {},
        toggle() {}};
    context.snapshotState.snapshots = [{id: 17, snapshot_type: 'manual', track_count: 2, created_at: 'now',
        trigger_description: ''}];
    slice('function renderSnapshotTimeline()', '// --- Manual Snapshot Creation ---');
    context.renderSnapshotTimeline();
    assert.match(element('snapshot-list').innerHTML,
        /data-action-click="wk-snapshot-restore" data-snap-id="17" data-live-operation-key="snapshot_restore:17"/);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


@pytest.mark.parametrize(
    "choice", ["ready", "stay", "discard", "save", "review", "busy"]
)
def test_playlist_switches_and_the_selector_await_the_shared_guard_before_assigning(
    node, choice
):
    run_node(
        node,
        scenario(
            """
(async () => {
    const choice = '__CHOICE__';
    let closed = 0;
    Object.assign(context, {playlistDropdownListEl: element('playlist-dropdown-list'),
        playlistDropdownToggleEl: element('playlist-dropdown-toggle'), currentPlaylistId: 'target',
        playlistPicker: {close: () => {closed++;}}});
    slice("playlistDropdownListEl.addEventListener('click'", 'playlistDropdownSearchEl.addEventListener');
    if (choice === 'ready') {
        assert.equal(await context.navigatePlaylist('next'), true);
        assert.deepEqual(assigned, ['/workshop/next']);
        const option = {disabled: false, dataset: {playlistId: 'other'}};
        element('playlist-dropdown-list').click({target: {closest: () => option}}); await tick(); await tick();
        assert.equal(closed, 1); assert.deepEqual(assigned, ['/workshop/next', '/workshop/other']);
        // The current playlist is not a navigation.
        element('playlist-dropdown-list').click({target: {closest: () => ({disabled: false,
            dataset: {playlistId: 'target'}})}}); await tick();
        assert.equal(assigned.length, 2);
        return;
    }
    if (choice === 'busy') {
        state.isSaving = true;
        assert.equal(await context.navigatePlaylist('prev'), false); assert.deepEqual(assigned, []);
        return;
    }
    if (choice === 'review') {
        state.sourceRecovery = [{kind: 'archive', resource: 'archive', status: 'unknown', body: {track_uris: []}}];
    } else state.workingUris = [uri(2), uri(1)];
    const navigation = context.navigatePlaylist('prev'); await tick(); await tick();
    assert.equal(panels[panels.length - 1].id, 'live-operation-leave');
    assert.deepEqual(assigned, []);
    const button = {stay: 'live-operation-leave-stay', discard: 'live-operation-leave-discard',
        save: 'live-operation-leave-save', review: 'live-operation-leave-review'}[choice];
    if (choice === 'save') replies.push(sourceContext());
    element(button).click(); await tick(); await tick();
    assert.equal(await navigation, choice === 'discard');
    assert.deepEqual(assigned, choice === 'discard' ? ['/workshop/previous'] : []);
    if (choice === 'discard') {
        assert.deepEqual(plain(state.workingUris), [uri(1), uri(2)]);
        assert.equal(calls.length, 0);
    }
    if (choice === 'save') {
        // Review & save opens the common Save review and stays; saving never navigates afterwards.
        assert.equal(panels[panels.length - 1].id, 'workshop-save-panel'); assert.equal(state.reviewMode, 'review');
        replies.push(saved([uri(2), uri(1)])); await context.saveReviewedWorkshop(); await tick();
        assert.deepEqual(assigned, []);
    }
    if (choice === 'review') {
        assert.equal(panels[panels.length - 1].id, 'workshop-save-panel'); assert.equal(state.reviewMode, 'results');
    }
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
            choice=choice,
        ),
    )


def _playlist():
    return Playlist(
        id="playlist123",
        name="Workshop Test Playlist",
        owner_id="user123",
        tracks=[
            {
                "id": f"track{i}",
                "name": f"Track {i}",
                "uri": f"spotify:track:track{i}",
                "duration_ms": 180000,
                "artists": [f"Artist {i}"],
                "album_name": f"Album {i}",
                "album_image_url": f"https://example.com/album{i}.jpg",
            }
            for i in range(1, 3)
        ],
    )


@patch("shuffify.routes.get_db_user")
@patch("shuffify.routes.AuthService", autospec=True)
@patch("shuffify.routes.workshop.PlaylistService")
@patch("shuffify.routes.workshop.ShuffleService")
def test_workshop_page_uses_the_shared_live_views_and_keys_every_static_live_control(
    mock_shuffle_svc,
    mock_playlist_svc,
    mock_auth_svc,
    mock_get_db_user,
    authenticated_client,
    sample_user,
):
    mock_auth_svc.validate_session_token.return_value = True
    mock_auth_svc.get_authenticated_api.return_value = Mock()
    mock_auth_svc.get_user_data.return_value = sample_user
    mock_get_db_user.return_value = Mock(id=1, spotify_id="u1")
    mock_playlist_svc.return_value = Mock(get_playlist=Mock(return_value=_playlist()))
    mock_shuffle_svc.list_algorithms.return_value = [
        {
            "name": "Basic",
            "class_name": "BasicShuffle",
            "description": "Random shuffle",
            "parameters": {},
        }
    ]

    response = authenticated_client.get("/workshop/playlist123")
    assert response.status_code == 200
    html = response.data.decode()
    for marker in (
        'id="live-operation-panel"',
        'id="live-operation-review"',
        'id="live-operation-run"',
        'id="live-operation-discard"',
        'id="live-operation-leave"',
        'id="live-operation-leave-save"',
        'id="live-operation-leave-discard"',
    ):
        assert html.count(marker) == 1, marker
    assert "snapshot-restore-modal" not in html
    for kind in ("privacy", "raid", "drip", "raid_and_drip"):
        assert f'data-live-operation-key="{kind}:playlist123"' in html
    # The review panel closes the workspace beside the page; the leave dialog follows it.
    template = (ROOT / "shuffify/templates/workshop.html").read_text()
    assert re.search(
        r"\{\{ live_operation_panel\('Back to Workshop', '[^']+'\) \}\}\n"
        r"</div>\{# workshop-review-workspace #\}\n\{\{ live_operation_leave\(\) \}\}\n",
        template,
    )
# ---------------------------------------------------------------------------------------
# The rendered workspace: the real page and its own scripts in the
# page harness. Tools open as task views beside the playlist, load once, keep their DOM and
# give focus back; deep links; the selector disclosures; no key switches playlists; the draft
# group is one element; per-row actions keep focus in their list.
# ---------------------------------------------------------------------------------------


SORTABLE = re.compile(
    r'<script src="https://cdn\.jsdelivr\.net/npm/sortablejs[^>]*></script>'
)
WORKSHOP_ALGORITHMS = [
    {
        "name": "Basic",
        "class_name": "BasicShuffle",
        "description": "Random order",
        "parameters": {
            "keep_first": {
                "type": "integer",
                "default": 0,
                "min": 0,
                "description": "Tracks to keep",
            }
        },
    },
    {
        "name": "Balanced",
        "class_name": "BalancedShuffle",
        "description": "Even spread",
        "parameters": {
            "section_count": {
                "type": "integer",
                "default": 4,
                "min": 2,
                "description": "Sections",
            }
        },
    },
]


def _track(number):
    return {
        "id": f"t{number}",
        "name": f"Track {number}",
        "uri": f"spotify:track:t{number}",
        "duration_ms": 180000,
        "artists": [f"Artist {number}"],
        "album_name": "Album",
        "album_image_url": "",
    }


def workspace_page(db_app, tracks=3, path="/workshop/target", **context):
    """The rendered Workshop for playlist 'target', its CDN Sortable replaced by a stand-in that
    keeps the options it was given (window.sortableOptions). ``tracks`` is a count, or the
    track numbers in order (repeats allowed)."""
    hub = context.pop("hub", False)
    if hub:
        html = render_page(
            db_app,
            "workshop.html",
            path="/workshop",
            playlist=None,
            algorithms=WORKSHOP_ALGORITHMS,
            prev_playlist_id=None,
            next_playlist_id=None,
            upstream_sources_json={},
        )
    else:
        numbers = range(1, tracks + 1) if isinstance(tracks, int) else tracks
        items = [_track(number) for number in numbers]
        html = render_page(
            db_app,
            "workshop.html",
            path=path,
            playlist={
                "id": "target",
                "name": "Morning Mix",
                "owner_id": "u1",
                "description": "",
                "total_tracks": len(items),
                "public": True,
                "tracks": items,
                "audio_features": {},
            },
            user={"id": "u1", "display_name": "Alice"},
            algorithms=WORKSHOP_ALGORITHMS,
            upstream_sources_json={},
            prev_playlist_id="previous",
            next_playlist_id="next",
            prev_playlist_name="Earlier Mix",
            next_playlist_name="Later Mix",
            **context,
        )
    # The hub loads no playlist scripts; a playlist page loads Sortable once.
    assert len(SORTABLE.findall(html)) == (0 if hub else 1)
    return SORTABLE.sub(
        "<script>window.Sortable = {create: (list, options) => "
        "(window.sortableOptions = options, {option() {}})};</script>",
        html,
    )


WORKSPACE_PAGE = r"""
const PLAYLISTS = [{id: 'previous', name: 'Earlier Mix', track_count: 3, image_url: ''},
    {id: 'target', name: 'Morning Mix', track_count: 3, image_url: ''},
    {id: 'next', name: 'Later Mix', track_count: 2, image_url: ''}];
const pending = count => Array.from({length: count}, (_, i) => ({id: i + 1, track_uri: 'spotify:track:p' + (i + 1),
    track_name: 'Pending ' + (i + 1), track_artists: 'Artist', source_name: 'Source'}));
const ROUTES = {
    '/api/workshop/target/locks': {success: true, locks: []},
    '/api/playlist/target/pending-raids': {tracks: pending(3)},
    '/api/user-playlists': {success: true, playlists: PLAYLISTS},
    '/api/playlist/target/raid-status': {raid_status: {
        raid_link: null, sources: [], schedule: null, drip_schedule: null,
        source_count: 0, max_sources: 10}},
    '/api/playlist/target/pair': {success: true, paired: false, pair: null},
    '/api/playlist/target/routines/context': {success: true, routines: [], capabilities: {}},
    '/api/schedules/latest-results': {success: true, results: {}},
    '/api/playlist/target/snapshots': {success: true, snapshots: []},
    '/api/workshop/target/sessions': {success: true, sessions: []},
};
const unrouted = [];
// Loads the page. Its own reads are answered from ROUTES, or a scenario's own table; a request
// nothing answers is recorded, so every scenario can say none was made.
async function openWorkshop(path = '/workshop/target', routes = {}) {
    const table = Object.assign({}, ROUTES, routes);
    loadPage(TREE, {path, route: url => {
        const data = table[url.split('?')[0]];
        if (data === undefined) { unrouted.push(url); return undefined; }
        return page.reply(JSON.parse(JSON.stringify(data)));
    }});
    await settle();
    return page;
}
const evaluate = code => vm.runInContext(code, page.window);
// The page's arrays come from its own realm: compare plain copies.
const plain = value => JSON.parse(JSON.stringify(value));
const reads = path => page.calls.filter(call => call.url.split('?')[0] === path).length;
const closeTask = id => page.$('#' + id + ' [data-panel-close]').click();
const focused = () => page.document.activeElement;
// What the page's one announcer says once the current task's messages are written.
const spoken = async () => { await heard(); return page.text(page.byId('live-operation-announcer')); };
function clean() { assert.deepEqual(page.errors, []); assert.deepEqual(unrouted, []); }
// The track rows, their occurrence keys and URIs, and one row's actions.
const rows = () => page.$$('#track-list > li.track-item');
const keysOf = () => rows().map(row => row.dataset.occurrence);
const urisOf = () => rows().map(row => row.dataset.uri);
const actionsButton = index => rows()[index].querySelector('.track-actions-btn');
function openActions(index) {
    const button = actionsButton(index);
    button.focus(); button.click();
    return button;
}
const rowAction = name => page.$('#track-actions [data-row-action="' + name + '"]');
// The rows, the occurrence list and workingUris always agree.
function agrees() {
    const state = plain(evaluate('({keys: workshopState.occurrences.map(item => item.key), ' +
        'uris: workshopState.workingUris})'));
    assert.deepEqual(state.keys, keysOf()); assert.deepEqual(state.uris, urisOf());
}
async function removeRow(index) { openActions(index); rowAction('remove').click(); await settle(); }
"""


def run_workspace(node, html, scenario):
    script = (
        PAGE_DOM
        + "\nconst TREE = "
        + json.dumps(page_tree(html))
        + ";\n"
        + WORKSPACE_PAGE
        + "\n(async () => {\n"
        + scenario
        + "\n})().catch(error => { console.error(error); process.exitCode = 1; });"
    )
    return run_scenario(node, script, ROOT)


def test_each_tool_opens_beside_the_playlist_loads_once_and_returns_focus(node, db_app):
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop();
    // The tool bar's Inbox shows, and says, the pending count read on load.
    assert.equal(page.text(page.byId('workshop-inbox-count')), '3');
    assert.equal(page.byId('workshop-inbox-count').hidden, false);
    assert.equal(page.byId('workshop-tool-inbox').getAttribute('aria-label'), 'Inbox, 3 pending');
    // Nothing a tool shows is read until the tool opens.
    for (const path of ['/api/playlist/target/raid-status', '/api/playlist/target/pair',
            '/api/playlist/target/routines/context',
            '/api/playlist/target/snapshots', '/api/workshop/target/sessions']) assert.equal(reads(path), 0, path);
    const tools = [['workshop-tool-inbox', 'workshop-inbox-panel', '/api/playlist/target/raid-status'],
        ['workshop-tool-archive', 'workshop-archive-panel', '/api/playlist/target/pair'],
        ['workshop-tool-routines', 'workshop-routines-panel', '/api/playlist/target/routines/context'],
        ['workshop-tool-history', 'workshop-history-panel', '/api/playlist/target/snapshots']];
    for (const [button, panel, path] of tools) {
        const trigger = page.byId(button);
        for (const round of [1, 2]) {
            trigger.focus(); trigger.click(); await settle();
            assert.equal(page.byId(panel).hidden, false, panel);
            // The playlist stays beside a task at the workspace width.
            assert.equal(page.byId('workshop-surface').hidden, false);
            assert.equal(focused(), page.byId(panel + '-heading'), panel);
            // The first opening reads what the tool shows; another keeps the view as it was.
            assert.equal(reads(path), 1, panel + ' opening ' + round);
            closeTask(panel); await settle();
            assert.equal(page.byId(panel).hidden, true, panel);
            assert.equal(focused(), trigger, panel);
        }
    }
    // History's first opening also read the saved drafts, once.
    assert.equal(reads('/api/workshop/target/sessions'), 1);
    // The badge read the inbox on load; the Inbox's first opening read its list once more.
    assert.equal(reads('/api/playlist/target/pending-raids'), 2);
    // With no archive linked, Archive says so and leads to its setup.
    assert.equal(page.text(page.byId('archive-pair-summary')),
        'No archive playlist is linked. Archive setup creates or links one; tracks you remove are then kept there.');
    assert.equal(page.byId('archive-tracks-section').classList.contains('hidden'), true);
    assert.equal(page.$$('#inbox-raids-list > div[data-track-id]').length, 3);
    // A tool's body scroll is kept for its next opening (the browser drops it once hidden).
    page.byId('workshop-tool-history').click(); await settle();
    const historyBody = page.$('#workshop-history-panel .sh-panel-body');
    historyBody.scrollTop = 120;
    closeTask('workshop-history-panel'); await settle();
    historyBody.scrollTop = 0;
    page.byId('workshop-tool-history').click(); await settle();
    assert.equal(historyBody.scrollTop, 120);
    closeTask('workshop-history-panel'); await settle();
    // Opening one tool replaces another; focus still returns to the trigger of the one shown.
    page.byId('workshop-tool-inbox').click(); await settle();
    const history = page.byId('workshop-tool-history');
    history.focus(); history.click(); await settle();
    assert.equal(page.byId('workshop-inbox-panel').hidden, true);
    assert.equal(page.byId('workshop-history-panel').hidden, false);
    page.key(focused(), 'Escape'); await settle();
    assert.equal(focused(), history);
    clean();
""",
    )


def test_a_tools_inputs_results_and_options_survive_closing_it(node, db_app):
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop();
    const add = page.byId('workshop-tool-add');
    add.click(); await settle();
    page.byId('search-input').value = 'night drive';
    const songs = (from, count) => Array.from({length: count}, (_, i) => ({uri: 'spotify:track:s' + (from + i),
        id: 's' + (from + i), name: 'Song ' + (from + i), artists: ['A'], duration_ms: 1000}));
    page.replies.push(page.reply({success: true, tracks: songs(0, 10)}));
    page.byId('search-btn').click(); await settle();
    closeTask('workshop-add-panel'); await settle();
    add.click(); await settle();
    assert.equal(page.byId('search-input').value, 'night drive');
    assert.equal(page.$$('#search-results .add-track-btn').length, 10);
    // Pagination continues from the kept results.
    page.replies.push(page.reply({success: true, tracks: songs(10, 1)}));
    page.byId('search-load-more').click(); await settle();
    assert.equal(page.$$('#search-results .add-track-btn').length, 11);
    assert.equal(JSON.parse(page.calls.at(-1).body).offset, 10);
    // Each result says Add to draft, naming its track for a screen reader; added, it says so.
    const first = page.$('#search-results .add-track-btn');
    assert.equal(page.text(first), 'Add to draft: Song 0');
    const shown = keysOf();
    first.focus(); first.click(); await settle();
    // The added track is a new occurrence at the end; every row already shown keeps its key.
    assert.deepEqual(keysOf().slice(0, 3), shown); assert.equal(new Set(keysOf()).size, 4);
    // Added, the result says so and keeps focus (aria-disabled, never disabled); another
    // activation adds nothing and says why.
    assert.equal(page.text(first), 'In playlist: Song 0');
    assert.equal(first.getAttribute('aria-disabled'), 'true'); assert.equal(first.disabled, false);
    assert.equal(focused(), first);
    assert.equal(evaluate('workshopState.workingUris').at(-1), 'spotify:track:s0');
    assert.deepEqual(page.notices.at(-1), {message: 'Added "Song 0" to draft.', type: 'success'});
    first.click(); await settle();
    assert.equal(evaluate('workshopState.workingUris').filter(uri => uri === 'spotify:track:s0').length, 1);
    assert.deepEqual(page.notices.at(-1), {message: '"Song 0" is already in this playlist.', type: 'info'});
    assert.equal(focused(), first); assert.equal(rows().length, 4);
    // Taken out of the draft again, it can be added again: the draft decides, not the label.
    const added = keysOf()[3];
    await removeRow(3);
    first.focus(); first.click(); await settle();
    assert.equal(rows().length, 4); assert.deepEqual(keysOf().slice(0, 3), shown);
    assert.notEqual(keysOf()[3], added); assert.equal(rows()[3].dataset.uri, 'spotify:track:s0');
    assert.deepEqual(page.notices.at(-1), {message: 'Added "Song 0" to draft.', type: 'success'});
    assert.equal(page.text(first), 'In playlist: Song 0'); assert.equal(focused(), first);
    agrees();
    // The options keep their method and values across a close.
    const options = page.byId('workshop-tool-options');
    options.click(); await settle();
    assert.equal(page.byId('workshop-param-keep_first').value, '0');
    // Each parameter's label is bound to its field.
    assert.equal(page.text(page.$('label[for="workshop-param-keep_first"]')), 'Tracks to keep');
    page.byId('workshop-param-keep_first').value = '2';
    closeTask('workshop-options-panel'); await settle();
    options.click(); await settle();
    assert.equal(page.byId('workshop-param-keep_first').value, '2');
    // The action bar names the method Preview shuffle uses.
    const select = page.byId('workshop-algorithm');
    select.value = 'BalancedShuffle'; page.change(select);
    assert.equal(page.text(page.byId('workshop-algorithm-name')), 'Balanced');
    assert.equal(page.text(page.$('label[for="workshop-param-section_count"]')), 'Sections');
    assert.equal(page.byId('workshop-param-keep_first'), null);
    clean();
""",
    )


@pytest.mark.parametrize("width", [1440, 375])
def test_preview_from_shuffle_options_shows_the_reordered_list(node, db_app, width):
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop();
    page.window.innerWidth = __WIDTH__; page.window.dispatchEvent(page.event('resize'));
    const options = page.byId('workshop-tool-options');
    // Shuffle options stays in the action bar at every width, and Playlist tools lists it too.
    assert.equal(options.parentElement, page.byId('workshop-actions'));
    assert.equal(/\bsh-workspace-(wide|narrow)-only\b/.test(options.className), false);
    assert.equal(page.$$('#workshop-tools-panel [data-workshop-tool="options"]').length, 1);
    options.focus(); options.click(); await settle();
    // Below the workspace width the task takes the content area; the playlist is hidden.
    assert.equal(page.byId('workshop-surface').hidden, __WIDTH__ < 1024);
    page.byId('workshop-param-keep_first').value = '1';
    page.replies.push(page.reply({success: true, algorithm_name: 'Basic',
        shuffled_uris: ['spotify:track:t3', 'spotify:track:t2', 'spotify:track:t1']}));
    page.$('#workshop-options-panel [data-action-click="wk-015"]').click(); await settle();
    // One activation previews with the current options.
    const previews = page.calls.filter(call => call.url === '/api/workshop/target/preview-shuffle');
    assert.equal(previews.length, 1); assert.equal(JSON.parse(previews[0].body).keep_first, '1');
    assert.deepEqual(page.$$('#track-list .track-item').map(row => row.dataset.uri),
        ['spotify:track:t3', 'spotify:track:t2', 'spotify:track:t1']);
    // Beside the playlist the task stays open; over it, it closes onto its trigger.
    assert.equal(page.byId('workshop-options-panel').hidden, __WIDTH__ < 1024);
    assert.equal(page.byId('workshop-surface').hidden, false);
    if (__WIDTH__ < 1024) assert.equal(focused(), options);
    // The direct action is one activation too.
    page.replies.push(page.reply({success: true, algorithm_name: 'Basic',
        shuffled_uris: ['spotify:track:t1', 'spotify:track:t3', 'spotify:track:t2']}));
    page.byId('preview-shuffle-btn').click(); await settle();
    assert.equal(page.calls.filter(call => call.url === '/api/workshop/target/preview-shuffle').length, 2);
    assert.equal(page.text(page.byId('workshop-save-state')), 'Draft');
    clean();
""".replace("__WIDTH__", str(width)),
    )


@pytest.mark.parametrize(
    "setup,task,trigger,read",
    [
        (
            "raids",
            "workshop-sources-panel",
            "workshop-tool-inbox",
            api_path("/playlist/target/raid-status"),
        ),
        (
            "archive",
            "workshop-archive-setup-panel",
            "workshop-tool-archive",
            api_path("/playlist/target/pair"),
        ),
        (
            "rotation",
            "workshop-archive-setup-panel",
            "workshop-tool-archive",
            api_path("/playlist/target/pair"),
        ),
        ("unknown", None, None, None),
    ],
)
def test_setup_deep_links_open_their_task_with_the_tool_bar_button_as_trigger(
    node, db_app, setup, task, trigger, read
):
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop('/workshop/target?setup=__SETUP__');
    const task = __TASK__, trigger = __TRIGGER__;
    const open = page.$$('[data-app-panel]').filter(panel => !panel.hidden).map(panel => panel.id);
    assert.deepEqual(open, task ? [task] : []);
    if (task) {
        // A setup task is full width: it takes the content area at every width.
        assert.equal(page.byId(task).hasAttribute('data-panel-full'), true);
        assert.equal(page.byId('workshop-surface').hidden, true);
        assert.equal(reads(__READ__), 1);
        closeTask(task); await settle();
        assert.equal(page.byId('workshop-surface').hidden, false);
        assert.equal(focused(), page.byId(trigger));
    }
    clean();
""".replace("__SETUP__", setup)
        .replace("__TASK__", repr(task) if task else "null")
        .replace("__TRIGGER__", repr(trigger) if trigger else "null")
        .replace("__READ__", repr(read) if read else "null"),
    )


def test_archive_setup_link_falls_back_to_playlist_tools_when_archive_is_not_shown(
    node, db_app
):
    # Below the workspace width the tool bar shows no Archive; the index takes its place.
    html = workspace_page(db_app, path="/workshop/target?setup=archive").replace(
        'id="workshop-tool-archive"', 'id="workshop-tool-archive" hidden', 1
    )
    run_workspace(
        node,
        html,
        r"""
    await openWorkshop('/workshop/target?setup=archive');
    assert.equal(page.byId('workshop-archive-setup-panel').hidden, false);
    closeTask('workshop-archive-setup-panel'); await settle();
    assert.equal(focused(), page.byId('workshop-tools-btn'));
    clean();
""",
    )


def test_setup_tasks_return_to_their_tool_and_the_index_to_its_button(node, db_app):
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop();
    // Each task the page opens, and how: nested in the active task, or inheriting its trigger.
    const opens = [], open = page.window.AppPanels.open;
    page.window.AppPanels.open = (id, options) => {
        opens.push([id, Boolean(options.nest), Boolean(options.inheritTrigger)]);
        return open(id, options);
    };
    // Every place focus lands.
    const stops = [];
    page.document.addEventListener('focusin', event => stops.push(event.target));
    const inboxButton = page.byId('workshop-tool-inbox'), setupButton = page.byId('workshop-sources-btn');
    inboxButton.focus(); inboxButton.click(); await settle();
    setupButton.focus(); setupButton.click(); await settle();
    assert.equal(page.byId('workshop-sources-panel').hidden, false);
    assert.equal(page.byId('workshop-inbox-panel').hidden, true);
    // Source setup is a step nested in the Inbox it was opened from.
    assert.deepEqual(opens, [['workshop-inbox-panel', false, false], ['workshop-sources-panel', true, false]]);
    // Closing Source setup, by its button or Escape, goes straight back to the Inbox on Source
    // setup: focus stops nowhere on the way (not on the list behind the tasks).
    for (const how of ['button', 'escape']) {
        stops.length = 0;
        if (how === 'button') closeTask('workshop-sources-panel');
        else page.key(page.byId('workshop-sources-panel-heading'), 'Escape');
        await settle();
        assert.equal(page.byId('workshop-sources-panel').hidden, true, how);
        assert.equal(page.byId('workshop-inbox-panel').hidden, false, how);
        assert.equal(focused(), setupButton, how);
        assert.deepEqual(stops, [setupButton], how);
        if (how === 'button') { setupButton.click(); await settle(); }
    }
    // The Inbox still returns to the tool bar's Inbox, its own trigger.
    closeTask('workshop-inbox-panel'); await settle();
    assert.equal(focused(), inboxButton);
    // Another tool opened over the setup task replaces it; the Inbox does not come back.
    inboxButton.click(); await settle(); setupButton.click(); await settle();
    page.byId('workshop-tool-routines').click(); await settle();
    assert.deepEqual(page.$$('[data-app-panel]').filter(panel => !panel.hidden).map(panel => panel.id),
        ['workshop-routines-panel']);
    closeTask('workshop-routines-panel'); await settle();
    // The Playlist tools index opens each destination, which returns to the index's button.
    const index = page.byId('workshop-tools-btn');
    const destinations = page.$$('#workshop-tools-panel li > button[data-workshop-tool]').map(button =>
        [button.dataset.workshopTool, page.text(button), button.dataset.workshopCompat, button.dataset.actionClick,
            button.className, button.getAttribute('type')]);
    assert.deepEqual(destinations, [['options', 'Shuffle options'], ['add', 'Add tracks'], ['inbox', 'Inbox'],
        ['archive', 'Archive'], ['routines', 'Routines'], ['history', 'History'], ['details', 'Playlist details']]
        .map(entry => entry.concat(['view', 'wk-tool', 'w-full sh-button-secondary', 'button'])));
    index.focus(); index.click(); await settle();
    opens.length = 0;
    page.$('#workshop-tools-panel [data-workshop-tool="details"]').click(); await settle();
    assert.equal(page.byId('workshop-tools-panel').hidden, true);
    assert.equal(page.byId('workshop-details-panel').hidden, false);
    // A destination replaces the index and inherits its trigger.
    assert.deepEqual(opens, [['workshop-details-panel', false, true]]);
    closeTask('workshop-details-panel'); await settle();
    assert.equal(focused(), index);
    // Archive setup nests in the Archive, which keeps the index's trigger through the step.
    index.click(); await settle();
    page.$('#workshop-tools-panel [data-workshop-tool="archive"]').click(); await settle();
    const archiveSetup = page.byId('workshop-archive-setup-btn');
    archiveSetup.focus(); archiveSetup.click(); await settle();
    assert.deepEqual(opens.slice(-2), [['workshop-archive-panel', false, true],
        ['workshop-archive-setup-panel', true, false]]);
    closeTask('workshop-archive-setup-panel'); await settle();
    assert.equal(page.byId('workshop-archive-panel').hidden, false); assert.equal(focused(), archiveSetup);
    closeTask('workshop-archive-panel'); await settle();
    assert.equal(focused(), index);
    clean();
""",
    )


def test_the_selector_and_hub_picker_are_hidden_disclosures(node, db_app):
    # One helper owns every disclosure: the selector, the hub picker and the row actions.
    template = (ROOT / "shuffify/templates/workshop.html").read_text()
    for popup in (
        "playlistDropdownEl",
        "document.getElementById('hub-playlist-dropdown')",
        "document.getElementById('track-actions')",
    ):
        assert f"createDisclosure({popup}" in template, popup
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop();
    const toggle = page.byId('playlist-dropdown-toggle'), list = page.byId('playlist-dropdown');
    const search = page.byId('playlist-dropdown-search');
    // A disclosure: expanded or not, and what it controls; it opens no ARIA popup.
    assert.equal(toggle.hasAttribute('aria-haspopup'), false);
    assert.equal(toggle.getAttribute('aria-controls'), 'playlist-dropdown');
    assert.equal(page.text(toggle), 'Switch playlist');
    // The name is the page's heading, outside the selector.
    assert.equal(toggle.querySelector('h1'), null); assert.equal(page.text(page.$('h1')), 'Morning Mix');
    // Closed, the list is hidden: its search and options are out of the keyboard order.
    assert.equal(list.hidden, true); assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    search.focus(); assert.notEqual(focused(), search);
    toggle.focus(); toggle.click(); await settle();
    assert.equal(list.hidden, false); assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(focused(), search);
    assert.deepEqual(page.$$('#playlist-dropdown-list [data-playlist-id]').map(option => option.dataset.playlistId),
        ['previous', 'target', 'next']);
    // The selector reads the user's playlists when it first opens.
    assert.equal(reads('/api/user-playlists'), 1);
    page.key(search, 'Escape'); await settle();
    assert.equal(list.hidden, true); assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(focused(), toggle);
    // Focus moving out closes it; so does a click elsewhere.
    toggle.click(); await settle();
    page.byId('workshop-tool-add').focus(); assert.equal(list.hidden, true);
    toggle.click(); await settle();
    page.byId('workshop-tracks-heading').click(); assert.equal(list.hidden, true);
    // Choosing a playlist closes onto the toggle and asks the shared guard before it leaves.
    toggle.click(); await settle();
    page.$('#playlist-dropdown-list [data-playlist-id="next"]').click(); await settle();
    assert.equal(list.hidden, true); assert.deepEqual(page.navigations, ['/workshop/next']);
    clean();
""",
    )
    run_workspace(
        node,
        workspace_page(db_app, hub=True),
        r"""
    await openWorkshop('/workshop');
    const toggle = page.byId('hub-playlist-dropdown-toggle'), list = page.byId('hub-playlist-dropdown');
    assert.equal(list.hidden, true); assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(toggle.hasAttribute('aria-haspopup'), false);
    assert.equal(toggle.getAttribute('aria-controls'), 'hub-playlist-dropdown');
    toggle.focus(); toggle.click(); await settle();
    assert.equal(list.hidden, false); assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(focused(), page.byId('hub-dropdown-search'));
    assert.deepEqual(page.$$('#hub-dropdown-list a').map(link => link.getAttribute('href')),
        ['/workshop/previous', '/workshop/target', '/workshop/next']);
    page.key(page.byId('hub-dropdown-search'), 'Escape'); await settle();
    assert.equal(list.hidden, true); assert.equal(focused(), toggle);
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    // Focus moving out closes it (tabbing past its last playlist); so does a click elsewhere.
    // The toggle closes it onto itself. It reads the playlists once.
    const outside = page.$$('a[href], button').find(control => control !== toggle && !list.contains(control));
    toggle.click(); await settle();
    outside.focus(); await settle();
    assert.equal(list.hidden, true); assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    toggle.click(); await settle();
    page.$('h2').click(); await settle();
    assert.equal(list.hidden, true);
    toggle.click(); await settle(); toggle.click(); await settle();
    assert.equal(list.hidden, true); assert.equal(focused(), toggle);
    assert.equal(reads('/api/user-playlists'), 1);
    clean();
""",
    )


def test_no_key_switches_playlists_and_the_neighbours_are_named(node, db_app):
    template = (ROOT / "shuffify/templates/workshop.html").read_text()
    assert "ArrowLeft" not in template and "ArrowRight" not in template
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop();
    for (const target of [page.document.body, page.byId('workshop-tool-add'), actionsButton(0),
            page.byId('workshop-tracks-heading')]) {
        for (const key of ['ArrowLeft', 'ArrowRight']) {
            assert.equal(page.key(target, key).defaultPrevented, false, key);
        }
    }
    await settle();
    assert.deepEqual(page.navigations, []);
    // The links name their playlists, from the route: the page reads no playlists to say so.
    assert.equal(page.byId('workshop-prev-playlist').getAttribute('aria-label'), 'Previous playlist: Earlier Mix');
    assert.equal(page.byId('workshop-next-playlist').getAttribute('aria-label'), 'Next playlist: Later Mix');
    assert.deepEqual(page.$$('#workshop-details-panel a[href="/workshop/previous"], ' +
        '#workshop-details-panel a[href="/workshop/next"]').map(link => page.text(link)),
        ['Previous playlist: Earlier Mix', 'Next playlist: Later Mix']);
    assert.equal(reads('/api/user-playlists'), 0);
    // A programmatic switch still asks the shared guard.
    assert.equal(await page.window.navigatePlaylist('prev'), true);
    assert.deepEqual(page.navigations, ['/workshop/previous']);
    clean();
""",
    )


def test_the_draft_controls_are_one_group_that_a_narrow_task_hides(node, db_app):
    html = workspace_page(db_app)
    for control in (
        "workshop-draft-footer",
        "workshop-save-status",
        "save-btn",
        "undo-preview-btn",
    ):
        assert html.count(f'id="{control}"') == 1, control
    run_workspace(
        node,
        html,
        r"""
    await openWorkshop();
    const primary = page.byId('workshop-surface'), footer = page.byId('workshop-draft-footer');
    assert.equal(footer.closest('[data-panel-primary]'), primary);
    // The footer's measured space is kept on the document for scroll padding: it is the shared
    // sticky action footer (components/action-footer.css).
    assert.match(page.document.documentElement.style.getPropertyValue('--app-footer-space'), /^calc\(\d+px \+ /);
    assert.equal(footer.classList.contains('sh-action-footer'), true);
    assert.equal(footer.hasAttribute('data-action-footer'), true);
    for (const id of ['workshop-save-status', 'save-btn', 'undo-preview-btn']) {
        assert.equal(page.byId(id).closest('#workshop-draft-footer'), footer, id);
    }
    // In the document the group follows the identity header (CSS order moves it below 1024px).
    const order = primary.children.map(child => child.id || child.localName);
    assert.ok(order.indexOf('workshop-draft-footer') === order.indexOf('workshop-identity') + 1, order);
    assert.ok(order.indexOf('workshop-draft-footer') < order.indexOf('workshop-actions'), order);
    assert.equal(page.$('#workshop-tools').getAttribute('aria-label'), 'Playlist tools');
    // A draft shows its status and Discard; below the workspace width an open task hides the
    // footer with the rest of the playlist, so footers never stack.
    await removeRow(0);
    assert.equal(page.text(page.byId('workshop-save-state')), 'Draft');
    assert.equal(page.byId('undo-preview-btn').classList.contains('hidden'), false);
    page.window.innerWidth = 375; page.window.dispatchEvent(page.event('resize'));
    page.byId('workshop-tool-inbox').click(); await settle();
    assert.equal(primary.hidden, true);
    closeTask('workshop-inbox-panel'); await settle();
    assert.equal(primary.hidden, false);
    clean();
""",
    )


def test_per_row_actions_keep_focus_on_the_same_control_in_their_list(node, db_app):
    archive = {
        api_path("/playlist/target/pair"): {
            "success": True,
            "paired": True,
            "pair": {
                "archive_playlist_id": "archive",
                "archive_playlist_name": "Morning Archive",
                "auto_archive_on_remove": True,
            },
        },
        api_path("/playlist/target/pair/archive-tracks"): {
            "success": True,
            "total": 3,
            "tracks": [
                {
                    "uri": f"spotify:track:a{n}",
                    "name": f"Archived {n}",
                    "artists": ["A"],
                }
                for n in (3, 2, 1)
            ],
        },
        api_path("/playlist/target/rotation-status"): {
            "success": True,
            "has_pair": True,
            "pair": {"archive_playlist_id": "archive"},
            "has_rotation_schedule": False,
        },
    }
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop('/workshop/target', __ARCHIVE__);
    const rowsOf = list => page.$$('#' + list + ' > div').map(row => row.dataset.trackId || row.dataset.uri);
    const inRow = (list, key, control) => page.$$('#' + list + ' > div')
        .find(row => (row.dataset.trackId || row.dataset.uri) === key).querySelector(control);
    // Inbox: Add to draft moves focus to the next row's Add, Dismiss now to the previous row's
    // Dismiss when it was last, and the last row's to the inbox section.
    page.byId('workshop-tool-inbox').click(); await settle();
    assert.deepEqual(rowsOf('inbox-raids-list'), ['1', '2', '3']);
    assert.equal(page.text(inRow('inbox-raids-list', '1', '.inbox-dismiss-btn')), 'Dismiss now: Pending 1');
    let control = inRow('inbox-raids-list', '1', '.inbox-add-btn'); control.focus(); control.click(); await settle();
    assert.deepEqual(rowsOf('inbox-raids-list'), ['2', '3']);
    assert.equal(focused(), inRow('inbox-raids-list', '2', '.inbox-add-btn'));
    page.replies.push(page.reply({success: true, database_status: "complete", dismissed_count: 1, dismissed_ids: [3]}));
    control = inRow('inbox-raids-list', '3', '.inbox-dismiss-btn'); control.focus(); control.click(); await settle();
    assert.deepEqual(rowsOf('inbox-raids-list'), ['2']);
    assert.equal(focused(), inRow('inbox-raids-list', '2', '.inbox-dismiss-btn'));
    page.replies.push(page.reply({success: true, database_status: "complete", dismissed_count: 1, dismissed_ids: [2]}));
    control = inRow('inbox-raids-list', '2', '.inbox-dismiss-btn'); control.click(); await settle();
    assert.equal(focused(), page.$('[aria-labelledby="inbox-raids-heading"]'));
    assert.equal(page.text(page.byId('workshop-inbox-count')), '0');
    assert.equal(page.byId('workshop-inbox-count').hidden, true);
    // Archive: Add to draft on a row moves focus to the next row's.
    page.byId('workshop-tool-archive').click(); await settle();
    assert.equal(page.text(page.byId('archive-pair-summary')),
        'Archive playlist: Morning Archive. Tracks you remove are archived when you save.');
    assert.deepEqual(rowsOf('inbox-archive-list'), ['spotify:track:a1', 'spotify:track:a2', 'spotify:track:a3']);
    control = inRow('inbox-archive-list', 'spotify:track:a1', '.archive-add-btn');
    control.focus(); control.click(); await settle();
    assert.equal(focused(), inRow('inbox-archive-list', 'spotify:track:a2', '.archive-add-btn'));
    closeTask('workshop-archive-panel'); await settle();
    // The track list (with the tracks added above): Remove from draft moves focus to the next
    // row's actions button, else the previous row's, else the list heading.
    const before = urisOf();
    await removeRow(0);
    assert.deepEqual(urisOf(), before.slice(1)); assert.equal(focused(), actionsButton(0));
    await removeRow(rows().length - 1);
    assert.deepEqual(urisOf(), before.slice(1, -1)); assert.equal(focused(), actionsButton(rows().length - 1));
    while (rows().length > 1) await removeRow(0);
    await removeRow(0);
    assert.equal(rows().length, 0); assert.equal(focused(), page.byId('workshop-tracks-heading'));
    // An empty draft says so, with its way back to the Library.
    assert.equal(page.byId('workshop-tracks-empty').hidden, false);
    clean();
""".replace("__ARCHIVE__", json.dumps(archive)),
    )


def test_details_report_locks_visibility_and_a_refused_unlock(node, db_app):
    locks = {
        api_path("/workshop/target/locks"): {
            "success": True,
            "locks": [
                {
                    "track_uri": "spotify:track:t1",
                    "lock_tier": "super",
                    "position": 0,
                    "expires_at": None,
                }
            ],
        }
    }
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop('/workshop/target', __LOCKS__);
    const details = page.byId('workshop-details-btn');
    details.click(); await settle();
    assert.equal(page.text(page.byId('workshop-visibility-btn')), 'Make private now');
    assert.equal(page.byId('workshop-visibility-btn').dataset.liveOperationKey, 'privacy:target');
    assert.equal(page.text(page.byId('workshop-visibility-state')), 'This playlist is public on Spotify.');
    assert.equal(page.text(page.byId('workshop-lock-summary')), '1 track is locked (1 super-locked).');
    const unlock = page.byId('unlock-all-btn');
    assert.equal(unlock.classList.contains('hidden'), false);
    // A refused unlock says so and changes nothing.
    page.replies.push(page.reply({success: false, message: 'Locks are busy.'}));
    unlock.focus(); unlock.click(); await settle();
    assert.deepEqual(page.notices.at(-1), {message: 'Locks are busy.', type: 'error'});
    assert.equal(page.text(page.byId('workshop-lock-summary')), '1 track is locked (1 super-locked).');
    assert.equal(unlock.classList.contains('hidden'), false);
    page.replies.push(page.reply({success: true}));
    unlock.click(); await settle();
    assert.equal(page.text(page.byId('workshop-lock-summary')), 'No tracks are locked.');
    // Unlock all is gone with the last lock; focus stays in its section.
    assert.equal(unlock.classList.contains('hidden'), true);
    assert.equal(focused(), page.$('[aria-labelledby="workshop-locks-heading"]'));
    clean();
""".replace("__LOCKS__", json.dumps(locks)),
    )


def test_a_restricted_playlist_suggestion_searches_the_catalog(node, db_app):
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop();
    page.byId('workshop-tool-add').click(); await settle();
    page.byId('external-playlist-input').value = 'Locked list';
    page.replies.push(page.reply({success: true, mode: 'restricted', playlist: {name: 'Locked list', track_count: null},
        message: 'Spotify keeps this playlist private.', suggested_search: 'Locked list'}));
    page.$('#workshop-add-panel [data-action-click="wk-022"]').click(); await settle();
    const suggestion = page.$('[data-action-click="wk-suggest-search"]');
    page.replies.push(page.reply({success: true,
        tracks: [{uri: 'spotify:track:x', id: 'x', name: 'Found', artists: []}]}));
    suggestion.click(); await settle();
    const search = page.calls.filter(call => call.url === '/api/workshop/search');
    assert.equal(search.length, 1); assert.equal(JSON.parse(search[0].body).query, 'Locked list');
    assert.equal(page.byId('search-input').value, 'Locked list'); assert.equal(focused(), page.byId('search-input'));
    assert.equal(page.$$('#search-results .add-track-btn').length, 1);
    clean();
""",
    )


def test_snapshot_copy_and_routine_cards_name_their_effects(node, db_app):
    routes = {
        api_path("/playlist/target/snapshots"): {
            "success": True,
            "snapshots": [
                {
                    "id": 17,
                    "snapshot_type": "manual",
                    "track_count": 3,
                    "created_at": "2026-10-12T08:00:00Z",
                    "trigger_description": "Before cleanup",
                }
            ],
        },
        # One exact normalized routine of every kind, the shuffle first.
        api_path("/playlist/target/routines/context"): {
            "success": True,
            "capabilities": {},
            "routines": [
                routine_row(5 + number, "target", kind.value)
                for number, kind in enumerate(sorted(JobType, key=lambda kind: kind != JobType.SHUFFLE))
            ],
        },
    }
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop('/workshop/target', __ROUTES__);
    page.byId('workshop-tool-history').click(); await settle();
    const restore = page.$('[data-live-operation-key="snapshot_restore:17"]');
    assert.match(page.text(restore), /^Restore Spotify playlist now: Manual, /);
    assert.equal(restore.hasAttribute('data-workshop-compat'), false);
    // A manual snapshot says whether it keeps the draft or the saved order.
    page.byId('snapshot-take-btn').click(); await settle();
    assert.equal(page.text(page.byId('snapshot-save-btn')), 'Save snapshot');
    page.$('[data-action-click="wk-063"]').click(); await settle();
    await removeRow(0);
    assert.equal(page.text(page.byId('snapshot-save-btn')), 'Save draft snapshot');
    page.byId('workshop-tool-routines').click(); await settle();
    // Every exact card offers reviewed Run, History, and an explicit dedicated edit.
    const cards = page.$$('#schedules-list .schedule-card');
    assert.equal(cards.length, __KINDS__);
    cards.forEach(card => {
        assert.deepEqual([card.querySelector('.routine-run'), card.querySelector('.routine-history')]
            .map(control => page.text(control)), ['Run now', 'History']);
        assert.match(page.text(card), new RegExp('Routine #' + card.dataset.scheduleId));
    });
    const card = page.$('.schedule-card[data-schedule-id="5"]'), run = card.querySelector('.routine-run');
    assert.match(page.text(card), /Shuffle/);
    assert.equal(run.dataset.liveOperationKey, 'schedule:5');
    assert.equal(run.dataset.workshopCompat, 'view');
    const edit = card.querySelector('.routine-edit');
    assert.equal(edit.dataset.workshopCompat, 'routine');
    assert.equal(edit.getAttribute('href'), '/playlist/target/routines/new?schedule_id=5&from=playlist');
    assert.equal(card.querySelector('.routine-history').dataset.workshopCompat, 'view');
    page.replies.push(page.reply({success: false, error_code: 'schedule_unavailable', message: 'Unavailable.'}, 503));
    run.click(); await settle();
    assert.equal(page.calls.at(-1).url, '/api/schedules/5/run-context');
    clean();
""".replace("__ROUTES__", json.dumps(routes)).replace("__KINDS__", str(len(JobType))),
    )


def test_an_empty_playlist_offers_its_way_back_to_the_library(node, db_app):
    html = workspace_page(db_app, tracks=0)
    assert ">Back to Library</a>" in html and "Back to Dashboard" not in html
    run_workspace(
        node,
        html,
        r"""
    await openWorkshop();
    assert.equal(page.byId('workshop-tracks-empty').hidden, false);
    assert.equal(page.$('#workshop-tracks-empty a').getAttribute('href'), '/');
    const previews = [page.byId('preview-shuffle-btn'), page.byId('workshop-options-preview')];
    assert.deepEqual(previews.map(button => button.disabled), [true, true]);
    assert.equal(page.$$('#track-list .track-item').length, 0);
    // A track added to the draft takes the empty playlist's place.
    page.byId('workshop-tool-add').click(); await settle();
    page.byId('search-input').value = 'one';
    page.replies.push(page.reply({success: true,
        tracks: [{uri: 'spotify:track:n1', id: 'n1', name: 'New', artists: []}]}));
    page.byId('search-btn').click(); await settle();
    page.$('#search-results .add-track-btn').click(); await settle();
    assert.equal(page.$$('#track-list .track-item').length, 1);
    assert.equal(page.byId('workshop-tracks-empty').hidden, true);
    // Both Preview shuffle buttons follow the count; a block of their group keeps it aside.
    assert.deepEqual(previews.map(button => button.disabled), [false, false]);
    evaluate('workshopState.isFinalizing = true; markDirty(); updateTrackCount()');
    assert.deepEqual(previews.map(button => button.disabled), [true, true]);
    evaluate('workshopState.isFinalizing = false; markDirty()');
    assert.deepEqual(previews.map(button => button.disabled), [false, false]);
    closeTask('workshop-add-panel'); await settle();
    await removeRow(0);
    assert.deepEqual(previews.map(button => button.disabled), [true, true]);
    clean();
""",
    )


def test_a_live_review_from_a_tool_hands_focus_back_to_the_list(node, db_app):
    """A reviewed live action replaces the tool it was started from; once its review closes,
    its trigger is gone with that tool, so focus lands on the track list's heading."""
    status = {api_path("/playlist/target/raid-status"): {"raid_status": {
        "raid_link": None, "sources": [{"id": 1, "source_name": "One", "raid_count": 5}],
        "schedule": None, "drip_schedule": None, "source_count": 1, "max_sources": 10}}}
    run_workspace(node, workspace_page(db_app), r"""
    await openWorkshop('/workshop/target', __STATUS__);
    page.byId('workshop-tool-inbox').click(); await settle();
    page.replies.push(page.reply({success: false, error_code: 'context_unavailable', retryable: true,
        message: 'The review could not be loaded. Nothing was changed.'}, 503));
    const collect = page.byId('raid-now-btn');
    collect.focus(); collect.click(); await settle();
    assert.equal(page.calls.at(-1).url, '/api/playlist/target/live-context');
    assert.equal(page.byId('live-operation-panel').hidden, false);
    assert.equal(page.byId('workshop-inbox-panel').hidden, true);
    page.key(page.byId('live-operation-heading'), 'Escape'); await settle();
    assert.equal(page.byId('live-operation-panel').hidden, true);
    assert.equal(focused(), page.byId('workshop-tracks-heading'));
    clean();
""".replace("__STATUS__", json.dumps(status)))


# ---------------------------------------------------------------------------------------
# Track rows and row actions, on the occurrence list.
# ---------------------------------------------------------------------------------------


def test_rows_are_the_drafts_occurrences_and_say_their_lock(node, db_app):
    locks = {api_path("/workshop/target/locks"): {"success": True, "locks": [
        {"track_uri": "spotify:track:t2", "lock_tier": "standard", "position": 1,
         "expires_at": "2026-11-11T10:00:00+00:00"},
        {"track_uri": "spotify:track:t3", "lock_tier": "super", "position": 3, "expires_at": None}]}}
    run_workspace(node, workspace_page(db_app, tracks=[1, 2, 1, 3]), r"""
    await openWorkshop('/workshop/target', __LOCKS__);
    // On Repeat: a song twice is two rows, each its own occurrence of the draft.
    assert.deepEqual(urisOf(), ['spotify:track:t1', 'spotify:track:t2', 'spotify:track:t1', 'spotify:track:t3']);
    assert.equal(new Set(keysOf()).size, 4);
    assert.deepEqual(plain(evaluate('workshopState.occurrences')).map(occurrence => occurrence.key), keysOf());
    rows().forEach((row, index) => {
        const name = 'Track ' + row.dataset.uri.slice(-1);
        assert.equal(row.dataset.trackId, 't' + row.dataset.uri.slice(-1));
        assert.equal(page.text(row.querySelector('.track-number')), String(index + 1));
        assert.equal(page.text(row.querySelector('.track-title')), name);
        const button = row.querySelector('.track-actions-btn');
        assert.equal(button.getAttribute('aria-label'), 'Actions for ' + name + ', position ' + (index + 1));
        assert.equal(button.getAttribute('aria-expanded'), 'false');
        assert.equal(button.getAttribute('aria-controls'), 'track-actions');
        assert.equal(button.hasAttribute('aria-haspopup'), false);
        assert.equal(button.dataset.workshopCompat, 'view');
        // The drag handle is for pointers only.
        assert.equal(row.querySelector('.drag-handle').getAttribute('aria-hidden'), 'true');
    });
    // The lock in words: a standard lock with its date, a super lock without one.
    const lockText = index => rows()[index].querySelector('.track-lock-state');
    assert.equal(lockText(0).hidden, true);
    assert.match(page.text(lockText(1)), /^Locked until \S/); assert.equal(lockText(1).hidden, false);
    assert.equal(page.text(lockText(3)), 'Super-locked'); assert.equal(lockText(3).hidden, false);
    assert.equal(rows()[1].classList.contains('track-locked'), true);
    assert.equal(rows()[0].classList.contains('track-locked'), false);
    agrees(); clean();
""".replace("__LOCKS__", json.dumps(locks)))


def test_row_actions_move_one_occurrence_and_keep_focus_on_it(node, db_app):
    run_workspace(node, workspace_page(db_app, tracks=[1, 2, 1]), r"""
    await openWorkshop();
    const [first, second, repeat] = keysOf();
    const actions = page.byId('track-actions');
    // Closed, the actions wait under the list, hidden.
    assert.equal(actions.hidden, true); assert.equal(actions.parentElement, page.byId('workshop-track-section'));
    // Opened, they show inside that row, name the occurrence and its place, and take focus.
    const button = openActions(2);
    assert.equal(actions.hidden, false); assert.equal(actions.parentElement, rows()[2]);
    assert.equal(button.getAttribute('aria-expanded'), 'true');
    assert.equal(page.text(page.byId('track-actions-title')), 'Actions for Track 1, position 3 of 3');
    assert.equal(page.byId('track-actions-position').value, '3');
    assert.equal(focused(), rowAction('up'));
    // Move up: the second copy of Track 1 moves with its key; the actions and focus go with it.
    rowAction('up').click(); await settle();
    assert.deepEqual(keysOf(), [first, repeat, second]);
    assert.deepEqual(plain(evaluate('workshopState.workingUris')),
        ['spotify:track:t1', 'spotify:track:t1', 'spotify:track:t2']);
    assert.equal(actions.parentElement, rows()[1]); assert.equal(focused(), rowAction('up'));
    assert.equal(page.text(rows()[1].querySelector('.track-number')), '2');
    assert.equal(actionsButton(1).getAttribute('aria-label'), 'Actions for Track 1, position 2');
    assert.equal(page.text(page.byId('track-actions-title')), 'Actions for Track 1, position 2 of 3');
    await heard();
    assert.match(page.text(page.byId('live-operation-announcer')), /^Moved Track 1 to position 2\./);
    assert.equal(page.text(page.byId('workshop-save-state')), 'Draft');
    // At the top Move up moves nothing and says where the track already is: spoken, not a toast.
    const toasts = page.notices.length;
    rowAction('up').click(); await settle();
    assert.deepEqual(keysOf(), [repeat, first, second]);
    assert.match(await spoken(), /^Moved Track 1 to position 1\./);
    rowAction('up').click(); await settle();
    assert.deepEqual(keysOf(), [repeat, first, second]);
    assert.equal(await spoken(), 'Track 1 is already at position 1.');
    assert.equal(focused(), rowAction('up'));
    // Down to the end, and no further.
    rowAction('down').click(); await settle(); rowAction('down').click(); await settle();
    assert.deepEqual(keysOf(), [first, second, repeat]); assert.equal(focused(), rowAction('down'));
    await spoken();
    rowAction('down').click(); await settle();
    assert.equal(await spoken(), 'Track 1 is already at position 3.');
    assert.equal(page.notices.length, toasts);
    // Escape and Close return focus to the row's actions button; the actions go back under the list.
    page.key(rowAction('down'), 'Escape'); await settle();
    assert.equal(actions.hidden, true); assert.equal(actions.parentElement, page.byId('workshop-track-section'));
    assert.equal(focused(), actionsButton(2)); assert.equal(actionsButton(2).getAttribute('aria-expanded'), 'false');
    openActions(0); rowAction('close').click(); await settle();
    assert.equal(actions.hidden, true); assert.equal(focused(), actionsButton(0));
    // The actions button toggles them; a click inside their row keeps them, and a click or
    // focus elsewhere closes them.
    openActions(0); actionsButton(0).click(); await settle();
    assert.equal(actions.hidden, true); assert.equal(focused(), actionsButton(0));
    openActions(1); rows()[1].querySelector('.track-title').click(); await settle();
    assert.equal(actions.hidden, false);
    page.byId('workshop-tracks-heading').click(); await settle();
    assert.equal(actions.hidden, true);
    openActions(1); page.byId('workshop-tool-add').focus(); await settle();
    assert.equal(actions.hidden, true);
    // Another row's button moves them to that row.
    openActions(0); openActions(2); await settle();
    assert.equal(actions.parentElement, rows()[2]);
    assert.equal(actionsButton(0).getAttribute('aria-expanded'), 'false');
    assert.equal(actionsButton(2).getAttribute('aria-expanded'), 'true');
    // A rebuilt list (a verified result, a discard) closes them; focus that was in them goes to
    // the same occurrence's actions button.
    const shown = keysOf();
    rowAction('down').focus(); evaluate('fullRerenderTrackList()'); await settle();
    assert.equal(actions.hidden, true); assert.deepEqual(keysOf(), shown);
    assert.equal(focused(), actionsButton(2)); assert.equal(actionsButton(2).getAttribute('aria-expanded'), 'false');
    agrees(); clean();
""")


def test_move_to_position_takes_a_whole_number_clamped_to_the_list(node, db_app):
    run_workspace(node, workspace_page(db_app, tracks=[1, 2, 3, 4]), r"""
    await openWorkshop();
    const [k1, k2, k3, k4] = keysOf();
    const field = page.byId('track-actions-position'), toasts = page.notices.length;
    // The shared occurrence module clamps the place asked for.
    const asked = [], moveKey = page.window.PlaylistWorkspace.moveKey;
    page.window.PlaylistWorkspace.moveKey = (list, key, position) => {
        asked.push(position); return moveKey(list, key, position);
    };
    openActions(0);
    assert.equal(field.getAttribute('max'), '4');
    // Past the end is the last position.
    field.value = '99'; rowAction('move').click(); await settle();
    assert.deepEqual(keysOf(), [k2, k3, k4, k1]); assert.equal(focused(), rowAction('move'));
    assert.equal(field.value, '4'); assert.deepEqual(asked, [99]);
    // The move is said with the place it reached.
    assert.match(await spoken(), /^Moved Track 1 to position 4\./);
    // Clamped to where the row already is, it says so and the field shows the place.
    field.value = '12'; rowAction('move').click(); await settle();
    assert.deepEqual(keysOf(), [k2, k3, k4, k1]); assert.equal(field.value, '4');
    assert.equal(await spoken(), 'Track 1 is already at position 4.');
    // A fraction takes its whole part; Enter in the field moves too, keeping focus there.
    field.value = '2.7'; page.key(field, 'Enter'); await settle();
    assert.deepEqual(keysOf(), [k2, k1, k3, k4]); assert.equal(focused(), field);
    // Below 1 is the first position.
    field.value = '0'; rowAction('move').click(); await settle();
    assert.deepEqual(keysOf(), [k1, k2, k3, k4]);
    await spoken();
    // An empty or unreadable entry moves nothing: the field is marked invalid and takes focus,
    // and the range is said.
    for (const entry of ['', 'abc', '1e999']) {
        field.value = entry; rowAction('move').focus(); rowAction('move').click(); await settle();
        assert.deepEqual(keysOf(), [k1, k2, k3, k4], entry);
        assert.equal(field.getAttribute('aria-invalid'), 'true', entry);
        assert.equal(focused(), field, entry);
        assert.equal(await spoken(), 'Enter a position from 1 to 4.', entry);
    }
    field.value = '3'; rowAction('move').click(); await settle();
    assert.equal(field.hasAttribute('aria-invalid'), false); assert.deepEqual(keysOf(), [k2, k3, k1, k4]);
    // Every word was spoken: no toasts.
    assert.equal(page.notices.length, toasts);
    agrees(); clean();
""")


def test_the_position_field_follows_every_move(node, db_app):
    run_workspace(node, workspace_page(db_app, tracks=[1, 2, 3, 4]), r"""
    await openWorkshop();
    const [k1, k2, k3, k4] = keysOf();
    const field = page.byId('track-actions-position'), list = page.byId('track-list');
    openActions(0);
    assert.equal(field.value, '1');
    // A step carries the field to the row's new place, so Move then leaves the row there.
    rowAction('down').click(); await settle();
    assert.deepEqual(keysOf(), [k2, k1, k3, k4]); assert.equal(field.value, '2');
    await spoken();
    rowAction('move').click(); await settle();
    assert.deepEqual(keysOf(), [k2, k1, k3, k4]);
    assert.equal(await spoken(), 'Track 1 is already at position 2.');
    rowAction('up').click(); await settle();
    assert.deepEqual(keysOf(), [k1, k2, k3, k4]); assert.equal(field.value, '1');
    // A drop carries it too: the open row dropped third, then another row dropped above it.
    list.insertBefore(rows()[0], rows()[3]);
    page.window.sortableOptions.onEnd({oldIndex: 0, newIndex: 2, oldDraggableIndex: 0, newDraggableIndex: 2});
    assert.deepEqual(keysOf(), [k2, k3, k1, k4]); assert.equal(field.value, '3');
    list.insertBefore(rows()[3], rows()[0]);
    page.window.sortableOptions.onEnd({oldIndex: 3, newIndex: 0, oldDraggableIndex: 3, newDraggableIndex: 0});
    assert.deepEqual(keysOf(), [k4, k2, k3, k1]); assert.equal(field.value, '4');
    assert.equal(page.byId('track-actions').hidden, false);
    agrees(); clean();
""")


def test_locked_rows_stay_put_and_no_row_moves_onto_one(node, db_app):
    locks = {api_path("/workshop/target/locks"): {"success": True, "locks": [
        {"track_uri": "spotify:track:t2", "lock_tier": "standard", "position": 1, "expires_at": None}]}}
    run_workspace(node, workspace_page(db_app, tracks=[1, 2, 3]), r"""
    await openWorkshop('/workshop/target', __LOCKS__);
    const before = keysOf(), toasts = page.notices.length;
    // A locked row stays put, and says why.
    openActions(1); rowAction('down').click(); await settle();
    assert.deepEqual(keysOf(), before);
    assert.equal(await spoken(), 'Track 2 is locked in place. Unlock it to move it.');
    // No row moves onto a locked row's place, by a step or by position.
    openActions(0); rowAction('down').click(); await settle();
    assert.deepEqual(keysOf(), before);
    assert.equal(await spoken(), 'A locked track holds position 2, so Track 1 cannot move there.');
    page.byId('track-actions-position').value = '2'; rowAction('move').click(); await settle();
    assert.deepEqual(keysOf(), before); assert.equal(evaluate('workshopState.isDirty'), false);
    // A refused move is said as its reason, never as a move, and never as a toast.
    assert.match(await spoken(), /^A locked track holds position 2, so Track 1 cannot move there\./);
    assert.equal(page.notices.length, toasts);
    // Past it is drag's rule too: Track 1 to position 3.
    page.byId('track-actions-position').value = '3'; rowAction('move').click(); await settle();
    assert.deepEqual(keysOf(), [before[1], before[2], before[0]]);
    // Drag asks the same rule, silently: a locked row is not picked up, nothing is dropped onto
    // one, and nothing moves while the draft is blocked.
    const options = page.window.sortableOptions;
    const handle = row => row.querySelector('.drag-handle');
    const picks = row => options.filter({target: handle(row)}, row);
    assert.deepEqual(rows().map(picks), [true, false, false]);
    // Only the handle is filtered: the locked row's own controls take taps and focus as usual.
    assert.equal(options.filter({target: rows()[0].querySelector('.track-actions-btn')}, rows()[0]), false);
    const drags = (from, to) => options.onMove({dragged: rows()[from], related: rows()[to]});
    assert.equal(drags(1, 0), false); assert.equal(drags(0, 1), false);
    assert.equal(drags(1, 2), undefined); assert.equal(drags(2, 1), undefined);
    evaluate('workshopState.isSaving = true');
    assert.equal(drags(1, 2), false);
    evaluate('workshopState.isSaving = false');
    assert.doesNotMatch(await spoken(), /Unavailable|locked/);
    assert.equal(page.notices.length, toasts);
    agrees(); clean();
""".replace("__LOCKS__", json.dumps(locks)))


def test_remove_from_draft_takes_exactly_its_occurrence(node, db_app):
    run_workspace(node, workspace_page(db_app, tracks=[1, 2, 1]), r"""
    await openWorkshop();
    const [first, second, repeat] = keysOf();
    // Removing the second copy of Track 1 removes that row only; the first keeps its key, and
    // focus goes to the previous row's actions button (it was the last row).
    await removeRow(2);
    assert.deepEqual(keysOf(), [first, second]);
    assert.deepEqual(plain(evaluate('workshopState.workingUris')), ['spotify:track:t1', 'spotify:track:t2']);
    assert.equal(focused(), actionsButton(1));
    assert.deepEqual(page.notices.at(-1), {message: 'Removed "Track 1" from your draft.', type: 'info'});
    assert.equal(page.text(page.byId('track-count')), '2');
    assert.equal(page.byId('track-actions').hidden, true);
    assert.equal(page.byId('track-actions').parentElement, page.byId('workshop-track-section'));
    // Discarding the draft brings the removed copy back as a new occurrence; the others keep theirs.
    page.byId('undo-preview-btn').click(); await settle();
    assert.deepEqual(keysOf().slice(0, 2), [first, second]);
    assert.equal(keysOf().length, 3); assert.notEqual(keysOf()[2], repeat);
    // A row in the middle hands focus to the next row's actions button, which takes its place.
    const next = keysOf()[2];
    await removeRow(1);
    assert.deepEqual(keysOf(), [first, next]);
    assert.equal(focused(), actionsButton(1)); assert.equal(focused().closest('li').dataset.occurrence, next);
    agrees(); clean();
""")


def test_row_actions_follow_their_groups_while_the_draft_is_blocked(node, db_app):
    run_workspace(node, workspace_page(db_app, tracks=[1, 2, 3]), r"""
    await openWorkshop();
    const before = keysOf();
    openActions(1);
    // While Save writes the playlist the draft and locks wait, saying why; the view stays.
    evaluate('workshopState.isSaving = true; renderWorkshopCompat()');
    for (const name of ['up', 'down', 'position', 'move', 'remove', 'lock']) {
        assert.equal(rowAction(name).disabled, true, name);
        assert.match(rowAction(name).getAttribute('aria-describedby'), /workshop-compat-reason-/, name);
    }
    assert.equal(rowAction('close').disabled, false); assert.equal(actionsButton(1).disabled, false);
    assert.equal(actionsButton(0).disabled, false);
    // A move, removal or lock reached anyway refuses with the reason and sends nothing; the
    // move says it.
    evaluate('moveTrackBy(-1, null)');
    assert.deepEqual(keysOf(), before);
    assert.equal(await spoken(), 'Unavailable while your draft saves to Spotify.');
    evaluate('removeFromDraft(trackActionsKey)'); evaluate('toggleTrackLock(trackActionsKey)');
    assert.deepEqual(keysOf(), before);
    assert.equal(page.calls.filter(call => call.url.endsWith('/locks/toggle')).length, 0);
    // The block lifts with Save, and the controls work again.
    evaluate('workshopState.isSaving = false; renderWorkshopCompat()');
    assert.equal(rowAction('up').disabled, false);
    assert.equal(rowAction('up').hasAttribute('aria-describedby'), false);
    rowAction('up').click(); await settle();
    assert.deepEqual(keysOf(), [before[1], before[0], before[2]]);
    // Each control declares its group: the moves and Remove are draft, the lock is locks, and
    // the actions button and Close are view.
    for (const name of ['up', 'down', 'position', 'move', 'remove']) {
        assert.equal(rowAction(name).dataset.workshopCompat, 'draft', name);
    }
    assert.equal(rowAction('lock').dataset.workshopCompat, 'locks');
    assert.equal(rowAction('close').dataset.workshopCompat, 'view');
    // While only the locks wait (a run that reads them), the lock refuses and the moves work.
    evaluate("workshopState.liveRun = {kind: 'drip', subject: 'target', steps: ['drip'], writes: []}");
    evaluate('renderWorkshopCompat()');
    assert.equal(rowAction('lock').disabled, true);
    assert.equal(rowAction('down').disabled, false); assert.equal(rowAction('remove').disabled, false);
    evaluate('toggleTrackLock(trackActionsKey)');
    assert.deepEqual(page.notices.at(-1), {message: 'Unavailable while adding collected tracks runs.', type: 'info'});
    assert.equal(page.calls.filter(call => call.url.endsWith('/locks/toggle')).length, 0);
    rowAction('down').focus(); rowAction('down').click(); await settle();
    assert.deepEqual(keysOf(), before); assert.equal(focused(), rowAction('down'));
    evaluate('workshopState.liveRun = null; renderWorkshopCompat()');
    assert.equal(rowAction('lock').disabled, false);
    agrees(); clean();
""")


def test_a_lock_is_toggled_at_its_occurrences_place_in_the_draft(node, db_app):
    run_workspace(node, workspace_page(db_app, tracks=[1, 2, 1]), r"""
    await openWorkshop();
    const toggles = () => page.calls.filter(call => call.url === '/api/workshop/target/locks/toggle');
    // The second copy of Track 1 (the third row) locks with its own place in the draft.
    openActions(2);
    assert.equal(page.text(page.byId('track-actions-lock-state')), 'Not locked.');
    assert.equal(page.text(rowAction('lock')), 'Lock for 30 days');
    assert.match(page.text(page.byId('track-actions-lock-note')), /save immediately, separately from your draft/);
    page.replies.push(page.reply({success: true, lock: {lock_tier: 'standard', position: 2, expires_at: null}}));
    // From the keyboard: the control keeps focus through the save, since no row is rebuilt.
    rowAction('lock').focus(); rowAction('lock').click(); await settle();
    assert.deepEqual(JSON.parse(toggles()[0].body), {track_uri: 'spotify:track:t1', position: 2});
    // A lock belongs to the track: both copies say so. The control names the next step.
    assert.deepEqual(rows().map(row => page.text(row.querySelector('.track-lock-state'))), ['Locked', '', 'Locked']);
    assert.equal(page.text(page.byId('track-actions-lock-state')), 'Locked.');
    assert.equal(page.text(rowAction('lock')), 'Lock permanently'); assert.equal(focused(), rowAction('lock'));
    page.replies.push(page.reply({success: true, lock: {lock_tier: 'super', position: 2, expires_at: null}}));
    rowAction('lock').click(); await settle();
    assert.equal(page.text(rowAction('lock')), 'Unlock');
    assert.equal(page.text(rows()[0].querySelector('.track-lock-state')), 'Super-locked');
    page.replies.push(page.reply({success: true, lock: null}));
    rowAction('lock').click(); await settle();
    assert.deepEqual(rows().map(row => row.querySelector('.track-lock-state').hidden), [true, true, true]);
    // Moved, the same occurrence locks at its new place; a lock is never part of the draft.
    page.byId('track-actions-position').value = '1'; rowAction('move').click(); await settle();
    page.replies.push(page.reply({success: true, lock: {lock_tier: 'standard', position: 0, expires_at: null}}));
    rowAction('lock').click(); await settle();
    assert.deepEqual(JSON.parse(toggles().at(-1).body), {track_uri: 'spotify:track:t1', position: 0});
    assert.equal(toggles().length, 4);
    agrees(); clean();
""")


def test_preview_and_drag_keep_each_tracks_keys(node, db_app):
    run_workspace(node, workspace_page(db_app, tracks=[1, 2, 1, 3]), r"""
    await openWorkshop();
    const [a1, b, a2, c] = keysOf();
    // A preview's order keeps each track's keys, in order: the copies of Track 1 keep theirs.
    page.replies.push(page.reply({success: true, algorithm_name: 'Basic',
        shuffled_uris: ['spotify:track:t3', 'spotify:track:t1', 'spotify:track:t2', 'spotify:track:t1']}));
    page.byId('preview-shuffle-btn').click(); await settle();
    assert.deepEqual(keysOf(), [c, a1, b, a2]);
    // A drop takes the keyboard's path: the dropped occurrence moves with its key.
    const options = page.window.sortableOptions, list = page.byId('track-list');
    assert.equal(options.handle, '.drag-handle'); assert.equal(options.draggable, '.track-item');
    list.insertBefore(rows()[3], rows()[0]);
    options.onEnd({oldIndex: 3, newIndex: 0, oldDraggableIndex: 3, newDraggableIndex: 0});
    await settle();
    assert.deepEqual(keysOf(), [a2, c, a1, b]);
    assert.deepEqual(plain(evaluate('workshopState.workingUris')),
        ['spotify:track:t1', 'spotify:track:t3', 'spotify:track:t1', 'spotify:track:t2']);
    assert.deepEqual(rows().map(row => page.text(row.querySelector('.track-number'))), ['1', '2', '3', '4']);
    agrees();
    // While the draft waits on Save, a drop is put back.
    evaluate('workshopState.isSaving = true');
    list.insertBefore(rows()[0], null);
    options.onEnd({oldIndex: 0, newIndex: 3, oldDraggableIndex: 0, newDraggableIndex: 3});
    assert.deepEqual(keysOf(), [a2, c, a1, b]);
    evaluate('workshopState.isSaving = false');
    agrees(); clean();
""")


def _css_rules(css):
    """Each rule's selector and declarations, comments removed (nested at-rules flattened)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(selector.strip(), body) for selector, body in re.findall(r"([^{};]+)\{([^{}]*)\}", css)]


def test_workspace_css_takes_cores_task_layout_token_breakpoints_and_shared_recipes():
    page_css = (ROOT / "assets/design/pages/workspace.css").read_text()
    component_css = (ROOT / "assets/design/components/workspace.css").read_text()
    rules = _css_rules(page_css) + _css_rules(component_css)
    selectors = " ".join(selector for selector, _ in rules)
    # Core lays out a task beside the playlist, full width or narrow, and omits an empty
    # footer: the page undoes none of it.
    for undone in ("sh-panel-narrow", ".sh-task-panel", ".sh-panel-body", ".sh-panel-footer:empty"):
        assert undone not in selectors, undone
    # The workspace is a core container with only its own top padding.
    template = (ROOT / "shuffify/templates/workshop.html").read_text()
    element = re.search(r'<div id="workshop-review-workspace" class="([^"]*)"', template)
    assert "sh-container" in element.group(1).split()
    workspace = [body for selector, body in rules if selector == "#workshop-review-workspace"]
    assert [re.findall(r"([\w-]+)\s*:", body) for body in workspace] == [["padding-top"]]
    # Breakpoints are the token screens, mobile first: no hard-coded width in the page, and the
    # component's widening steps use @screen.
    assert "@media" not in page_css
    assert "@media (min-width" not in component_css
    # Spotify attribution is the shared recipe; Workshop keeps no copy.
    assert "spotify" not in selectors
    # A control with no pointer events shows no pointer cursor.
    for selector, body in rules:
        if "pointer-events: none" in body:
            assert "cursor" not in body, selector
    # A row's actions button keeps its focus ring clear of the sticky draft footer.
    actions = [body for selector, body in rules if selector == ".sh-workshop .track-actions-btn"]
    assert len(actions) == 1 and "scroll-margin-bottom" in actions[0]
    # The footer's measured space is written on the document element and read there alone:
    # registered in core as not inherited (and kept by the build), a new measurement restyles
    # nothing else. The page registers nothing of its own.
    core = _css_rules((ROOT / "assets/design/core.css").read_text())
    registered = [body for selector, body in core if selector == "@property --app-footer-space"]
    assert len(registered) == 1 and re.search(r"inherits:\s*false", registered[0])
    assert "@property" not in page_css
    compiled = (ROOT / "shuffify/static/css/tailwind.css").read_text()
    assert re.search(r"@property --app-footer-space\s*\{[^}]*inherits:\s*false", compiled)
    assert "workspace-footer-space" not in compiled


def test_add_tracks_inbox_and_archive_rows_share_one_recipe(node, db_app):
    """Search, Inbox and Archive rows come from one builder: the same artwork (a URL that is not
    http(s) shows the placeholder), the title and its quieter lines, and buttons naming their
    track; each button acts on its own row's track."""
    image = "https://i.scdn.co/image/one"
    routes = {
        api_path("/playlist/target/pending-raids"): {
            "tracks": [
                {"id": 1, "track_uri": "spotify:track:p1", "track_name": "Pending 1", "track_artists": "Artist",
                 "source_name": "Source", "track_image_url": image},
                {"id": 2, "track_uri": "spotify:track:p2", "track_name": "Pending 2", "track_artists": "Artist",
                 "source_name": "Source", "track_image_url": "javascript:alert(1)"},
            ]
        },
        api_path("/playlist/target/pair"): {
            "success": True,
            "paired": True,
            "pair": {"archive_playlist_id": "archive", "archive_playlist_name": "Archive",
                     "auto_archive_on_remove": True},
        },
        api_path("/playlist/target/pair/archive-tracks"): {
            "success": True,
            "total": 2,
            # The Archive lists its tracks most recently archived first.
            "tracks": [
                {"uri": "spotify:track:a2", "name": "Archived 2", "artists": ["A"],
                 "album_image_url": "javascript:alert(1)"},
                {"uri": "spotify:track:a1", "name": "Archived 1", "artists": ["A", "B"], "album_image_url": image},
            ],
        },
        api_path("/playlist/target/rotation-status"): {"success": True, "has_pair": True,
                                             "pair": {"archive_playlist_id": "archive"},
                                             "has_rotation_schedule": False},
    }
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop('/workshop/target', __ROUTES__);
    // A row's shape: its artwork, its title and lines (each with its recipe), and its buttons.
    const shape = row => {
        const [art, text] = row.children;
        return {art: art.tagName === 'IMG' ? art.getAttribute('src') : 'placeholder', artClass: art.className,
            text: [...text.children].map(line => [line.className, page.text(line)]),
            buttons: row.querySelectorAll('button').map(button => page.text(button))};
    };
    const rowsIn = list => page.$$('#' + list + ' > div').map(shape);
    const title = text => ['truncate sh-detail sh-workshop-emphasis', text];
    const line = text => ['truncate sh-text-muted sh-detail', text];
    page.byId('workshop-tool-add').click(); await settle();
    page.byId('search-input').value = 'night drive';
    page.replies.push(page.reply({success: true, tracks: [
        {uri: 'spotify:track:s1', id: 's1', name: 'Song 1', artists: ['A', 'B'], duration_ms: 61000,
            album_image_url: '__IMAGE__'},
        {uri: 'spotify:track:s2', id: 's2', name: 'Song 2', artists: ['A'], duration_ms: 1000,
            album_image_url: 'javascript:alert(1)'}]}));
    page.byId('search-btn').click(); await settle();
    const search = rowsIn('search-results');
    page.byId('workshop-tool-inbox').click(); await settle();
    const inbox = rowsIn('inbox-raids-list');
    page.byId('workshop-tool-archive').click(); await settle();
    const archive = rowsIn('inbox-archive-list');
    // One artwork recipe everywhere; only http(s) artwork is shown.
    const artClasses = new Set([...search, ...inbox, ...archive].filter(row => row.art !== 'placeholder')
        .map(row => row.artClass));
    assert.deepEqual([...artClasses], ['w-8 h-8 object-contain flex-shrink-0 sh-workshop-artwork']);
    assert.deepEqual([search, inbox, archive].map(rows => rows.map(row => row.art)),
        [['__IMAGE__', 'placeholder'], ['__IMAGE__', 'placeholder'], ['__IMAGE__', 'placeholder']]);
    assert.deepEqual(search.map(row => row.text), [[title('Song 1'), line('A, B')], [title('Song 2'), line('A')]]);
    assert.deepEqual(inbox[0].text, [title('Pending 1'), line('Artist'), line('from Source')]);
    assert.deepEqual(archive[0].text, [title('Archived 1'), line('A, B')]);
    assert.equal(page.text(page.$('#search-results > div > span')), '1:01');
    assert.deepEqual([search[1].buttons, inbox[1].buttons, archive[1].buttons], [['Add to draft: Song 2'],
        ['Add to draft: Pending 2', 'Dismiss now: Pending 2'], ['Add to draft: Archived 2']]);
    // Each button acts on its own row's track.
    page.$$('#inbox-archive-list .archive-add-btn')[1].click(); await settle();
    assert.deepEqual(plain(evaluate('workshopState.stagedRestorations.map(track => track.uri)')),
        ['spotify:track:a2']);
    page.byId('workshop-tool-inbox').click(); await settle();
    page.$$('#inbox-raids-list .inbox-add-btn')[1].click(); await settle();
    assert.deepEqual(plain(evaluate('pendingRaidsState.stagedPromotions.map(track => track.id)')), [2]);
    page.byId('workshop-tool-add').click(); await settle();
    page.$$('#search-results .add-track-btn')[1].click(); await settle();
    assert.deepEqual(plain(evaluate('workshopState.workingUris.slice(-3)')),
        ['spotify:track:a2', 'spotify:track:p2', 'spotify:track:s2']);
    clean();
""".replace("__ROUTES__", json.dumps(routes)).replace("__IMAGE__", image),
    )


def test_rows_are_numbered_in_one_place_and_only_where_their_place_changed(node, db_app):
    run_workspace(node, workspace_page(db_app, tracks=[1, 2, 3, 4, 5, 6]), r"""
    await openWorkshop();
    const numbers = () => rows().map(row => page.text(row.querySelector('.track-number')));
    const names = () => rows().map(row => row.querySelector('.track-actions-btn').getAttribute('aria-label'));
    // Each row shows its position, and its Row actions button names its track and position.
    assert.deepEqual(numbers(), ['1', '2', '3', '4', '5', '6']);
    assert.deepEqual(names(), [1, 2, 3, 4, 5, 6].map(n => 'Actions for Track ' + n + ', position ' + n));
    // Marked rows show which rows a change writes again: only those whose place changed.
    const mark = () => rows().forEach(row => {
        row.querySelector('.track-number').textContent = '-';
        row.querySelector('.track-actions-btn').setAttribute('aria-label', '-');
    });
    // A move writes the rows from where it started to where it ended.
    mark(); openActions(1);
    page.byId('track-actions-position').value = '4'; rowAction('move').click(); await settle();
    assert.deepEqual(numbers(), ['-', '2', '3', '4', '-', '-']);
    assert.deepEqual(names(), ['-', 'Actions for Track 3, position 2', 'Actions for Track 4, position 3',
        'Actions for Track 2, position 4', '-', '-']);
    page.key(rowAction('move'), 'Escape'); await settle();
    // So does a drop: the fifth row dropped third.
    mark();
    const list = page.byId('track-list');
    list.insertBefore(rows()[4], rows()[2]);
    page.window.sortableOptions.onEnd({oldIndex: 4, newIndex: 2, oldDraggableIndex: 4, newDraggableIndex: 2});
    assert.deepEqual(numbers(), ['-', '-', '3', '4', '5', '-']);
    // A removal writes the rows after it.
    mark(); await removeRow(3);
    assert.deepEqual(numbers(), ['-', '-', '-', '4', '5']);
    // Added tracks write only their own rows.
    mark(); evaluate("appendToDraft(['spotify:track:t1'])");
    assert.deepEqual(numbers(), ['-', '-', '-', '-', '-', '6']);
    assert.equal(names()[5], 'Actions for Track 1, position 6');
    // A whole new order writes every row, and keeps the rows shown: it reorders them by key.
    const shown = new Set(rows());
    mark(); evaluate('replaceDraftOrder(workshopState.workingUris.slice().reverse()); fullRerenderTrackList()');
    assert.deepEqual(numbers(), ['1', '2', '3', '4', '5', '6']);
    assert.equal(names()[0], 'Actions for Track 1, position 1');
    assert.equal(rows().filter(row => shown.has(row)).length, 6);
    agrees(); clean();
""")


def test_archive_and_archive_setup_opened_together_read_the_pairing_once(node, db_app):
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop();
    // The pairing reads are counted as they start, and held in flight.
    let release, pairReads = 0;
    const held = new Promise(resolve => { release = resolve; });
    const plainFetch = page.window.fetch;
    page.window.fetch = (url, init) => {
        if (url !== '/api/playlist/target/pair') return plainFetch(url, init);
        pairReads += 1;
        return held.then(() => plainFetch(url, init));
    };
    page.byId('workshop-tool-archive').click(); await settle();
    assert.equal(pairReads, 1);
    // Archive setup, opened from Archive while that read is in flight, waits for it.
    page.byId('workshop-archive-setup-btn').click(); await settle();
    assert.equal(page.byId('workshop-archive-setup-panel').hidden, false);
    assert.equal(pairReads, 1);
    release(); await settle();
    assert.equal(pairReads, 1); assert.equal(reads('/api/playlist/target/pair'), 1);
    assert.equal(page.byId('archive-no-pair').classList.contains('hidden'), false);
    clean();
""",
    )


def test_rows_and_row_actions_take_their_blocked_state_from_the_save_status_render(node, db_app):
    run_workspace(
        node,
        workspace_page(db_app),
        r"""
    await openWorkshop();
    let renders = 0;
    const render = page.window.renderWorkshopCompat;
    page.window.renderWorkshopCompat = () => { renders += 1; return render(); };
    // Save running blocks the draft group, rendered once by the Save status (markDirty).
    evaluate('setWorkshopBusy(true)');
    const rendered = renders;
    assert.ok(rendered > 0);
    // Row actions opened meanwhile show their moves and Remove blocked without a render of their own.
    openActions(0); await settle();
    assert.deepEqual(['up', 'down', 'move', 'remove'].map(name => rowAction(name).disabled), [true, true, true, true]);
    assert.equal(renders, rendered);
    evaluate('setWorkshopBusy(false)');
    assert.equal(rowAction('up').disabled, false);
    // Added rows and a whole new order need none either: their only grouped control, the
    // actions button, is in the never-blocked view group.
    const settled = renders;
    evaluate("appendToDraft(['spotify:track:t1']); fullRerenderTrackList()");
    assert.equal(renders, settled);
    assert.deepEqual(rows().map(row => row.querySelector('.track-actions-btn').dataset.workshopCompat),
        ['view', 'view', 'view', 'view']);
    clean();
""",
    )


# ---------------------------------------------------------------------------------------
# Focus while a control is busy, hidden or rebuilt by its own action.
# ---------------------------------------------------------------------------------------

# A reply the scenario releases itself: page.replies takes it, and answer(data, status) settles it.
HELD = r"""
function held() {
    const hold = {};
    page.replies.push(() => new Promise(resolve => {
        hold.answer = (data, status) => resolve(page.reply(data, status));
    }));
    return hold;
}
const busy = button => button.getAttribute('aria-disabled') === 'true' && button.disabled === false;
const searches = () => page.calls.filter(call => call.url === '/api/workshop/search').length;
const found = n => ({success: true, tracks: Array.from({length: n}, (_, i) => ({id: 's' + i,
    uri: 'spotify:track:s' + i, name: 'S' + i, artists: ['A'], duration_ms: 1000}))});
"""


def test_preview_search_and_load_more_keep_focus_while_busy_and_refuse_a_second_press(node, db_app):
    """A control waiting on its own request stays focusable, marked aria-disabled, never
    disabled (which drops focus to the page); its handler refuses another press, and focus is
    still on it when the reply lands. Its group's block still reads as before meanwhile."""
    run_workspace(node, workspace_page(db_app), HELD + r"""
    await openWorkshop();
    const preview = page.byId('preview-shuffle-btn'), fromOptions = page.byId('workshop-options-preview');
    let shuffle = held();
    preview.focus(); preview.click(); await settle();
    assert.equal(focused(), preview);
    assert.ok(busy(preview) && busy(fromOptions));
    preview.click(); fromOptions.click(); await settle();
    assert.equal(reads('/api/workshop/target/preview-shuffle'), 1);
    shuffle.answer({success: true, shuffled_uris: ['spotify:track:t3', 'spotify:track:t1', 'spotify:track:t2'],
        algorithm_name: 'Basic'}); await settle();
    assert.equal(focused(), preview);
    assert.ok(!preview.hasAttribute('aria-disabled') && !fromOptions.hasAttribute('aria-disabled'));
    assert.deepEqual(urisOf(), ['spotify:track:t3', 'spotify:track:t1', 'spotify:track:t2']);
    // While it waits, a block of its group still disables it and names the reason; each state
    // returns when the block lifts and the reply lands.
    shuffle = held();
    preview.click(); await settle();
    evaluate('workshopState.isFinalizing = true; markDirty()');
    assert.equal(preview.disabled, true);
    assert.match(preview.getAttribute('aria-describedby'), /workshop-compat-reason-draft/);
    evaluate('workshopState.isFinalizing = false; markDirty()');
    assert.equal(preview.disabled, false);
    assert.equal(preview.getAttribute('aria-describedby'), 'workshop-algorithm-summary');
    assert.equal(preview.getAttribute('aria-disabled'), 'true');
    shuffle.answer({success: true, algorithm_name: 'Basic',
        shuffled_uris: ['spotify:track:t1', 'spotify:track:t2', 'spotify:track:t3']}); await settle();
    assert.equal(preview.hasAttribute('aria-disabled'), false);

    // Search, by button or Enter, and Load more.
    page.byId('workshop-tool-add').click(); await settle();
    const search = page.byId('search-btn'), more = page.byId('search-load-more');
    page.byId('search-input').value = 'x';
    let reply = held();
    search.focus(); search.click(); await settle();
    assert.equal(focused(), search); assert.ok(busy(search));
    // Another search waits for this one before it changes anything.
    page.byId('search-input').value = 'y';
    search.click(); page.key(page.byId('search-input'), 'Enter'); await settle();
    assert.equal(searches(), 1);
    reply.answer(found(10)); await settle();
    assert.equal(focused(), search); assert.equal(search.hasAttribute('aria-disabled'), false);
    reply = held();
    more.focus(); more.click(); await settle();
    assert.equal(focused(), more); assert.ok(busy(more) && busy(search));
    more.click(); search.click(); await settle();
    assert.equal(searches(), 2);
    assert.equal(JSON.parse(page.calls.at(-1).body).query, 'x');
    reply.answer(found(10)); await settle();
    assert.equal(focused(), more); assert.equal(more.hasAttribute('aria-disabled'), false);
    assert.equal(page.$$('#search-results > *').length, 20);
    clean();
""")


def test_review_and_save_keeps_focus_while_preparing_and_closing_returns_to_it(node, db_app):
    """Review & save waits for its review focusable and marked busy; the Save panel opens with
    it as trigger, so Back to Workshop and Escape return there, and Review again keeps focus
    while it prepares. Review save results returns to its own button."""
    run_workspace(node, workspace_page(db_app), HELD + r"""
    const context = {success: true, target: {id: 'target', name: 'Morning Mix'}};
    await openWorkshop('/workshop/target', {'/api/workshop/target/source-context': context});
    await removeRow(1);
    const save = page.byId('save-btn'), heading = page.byId('workshop-review-heading');
    let reply = held();
    save.focus(); save.click(); await settle();
    assert.equal(focused(), save); assert.ok(busy(save));
    save.click(); await settle();
    assert.equal(reads('/api/workshop/target/source-context'), 1);
    // The opener is taken when the review starts, wherever focus goes meanwhile.
    page.byId('workshop-tracks-heading').focus();
    reply.answer(context); await settle();
    assert.equal(page.byId('workshop-save-panel').hidden, false); assert.equal(focused(), heading);
    assert.equal(save.hasAttribute('aria-disabled'), false); assert.equal(save.disabled, false);
    // Review again keeps focus while it prepares.
    const again = page.byId('workshop-review-refresh');
    reply = held();
    again.focus(); again.click(); await settle();
    assert.equal(focused(), again); assert.ok(busy(again));
    again.click(); await settle();
    assert.equal(reads('/api/workshop/target/source-context'), 2);
    reply.answer(context); await settle();
    assert.equal(focused(), again); assert.equal(again.hasAttribute('aria-disabled'), false);
    page.byId('workshop-review-back').click(); await settle();
    assert.equal(page.byId('workshop-save-panel').hidden, true); assert.equal(focused(), save);
    save.click(); await settle();
    page.key(heading, 'Escape'); await settle();
    assert.equal(focused(), save);
    // Save to Spotify hides itself as the results show: focus goes to the panel's heading.
    save.click(); await settle();
    page.replies.push(page.reply({success: true, target_status: 'saved',
        saved_track_uris: ['spotify:track:t1', 'spotify:track:t3']}));
    const final = page.byId('workshop-review-save');
    final.focus(); final.click(); await settle();
    assert.equal(final.hidden, true); assert.equal(focused(), heading);
    assert.equal(page.text(heading), 'Save results');
    page.key(heading, 'Escape'); await settle();
    // With nothing left to save, Review & save is unavailable: focus goes to the draft footer.
    assert.equal(save.disabled, true); assert.equal(focused(), page.byId('workshop-draft-footer'));
    // Review save results opens from its own button and returns there.
    const results = page.byId('workshop-open-results');
    results.focus(); results.click(); await settle();
    assert.equal(page.byId('workshop-save-panel').hidden, false);
    page.byId('workshop-review-back').click(); await settle();
    assert.equal(focused(), results);
    clean();
""")


def test_create_and_link_controls_keep_focus_while_busy_and_send_once(node, db_app):
    """Each create, link and add control keeps focus while its request runs: marked busy, never
    disabled, with a second press (or Enter) refused. A refusal leaves it in place, focused and
    available again."""
    status = {api_path("/playlist/target/raid-status"): {"raid_status": {
        "raid_link": None, "sources": [{"id": 1, "source_name": "One", "raid_count": 5}],
        "schedule": None, "drip_schedule": None, "source_count": 1, "max_sources": 10}},
        api_path("/playlist/target/rotation-status"): {"success": True, "schedule": None},
        api_path("/playlist/target/pair/archive-tracks"): {"success": True, "tracks": [], "total": 0}}
    page = workspace_page(db_app)
    flows = HELD + r"""
    const posts = path => page.calls.filter(call => call.method !== 'GET' && call.url === path).length;
    async function pressWhileBusy(control, path, again = () => control.click(), during = () => {}) {
        const sent = posts(path), reply = held();
        control.focus(); control.click(); await settle();
        assert.equal(focused(), control, path); assert.ok(busy(control), path);
        during();
        again(); await settle();
        assert.equal(posts(path), sent + 1, path);
        reply.answer({success: false, message: 'Refused'}, 400); await settle();
        assert.equal(focused(), control, path); assert.equal(control.hasAttribute('aria-disabled'), false, path);
    }
    """
    run_workspace(node, page, flows + r"""
    await openWorkshop('/workshop/target?setup=archive', __STATUS__);
    await pressWhileBusy(page.$('[data-action-click="wk-050"]'), '/api/playlist/target/pair');
    const select = page.byId('archive-link-select');
    select.value = select.options[1].value; page.change(select); await settle();
    await pressWhileBusy(page.byId('archive-link-btn'), '/api/playlist/target/pair');
    // A link that succeeds gives its button its label back for when the form shows again.
    const link = page.byId('archive-link-btn');
    page.replies.push(page.reply({success: true, pair: {archive_playlist_id: 'next',
        archive_playlist_name: 'Later Mix', auto_archive_on_remove: false}}));
    link.focus(); link.click(); await settle();
    assert.equal(page.byId('archive-no-pair').classList.contains('hidden'), true);
    assert.equal(link.textContent, 'Link as archive now');
    clean();
""".replace("__STATUS__", json.dumps(status)))
    run_workspace(node, page, flows + r"""
    await openWorkshop('/workshop/target?setup=raids', __STATUS__);
    const create = page.$('[data-action-click="wk-025"]'), raidSelect = page.byId('raid-link-select');
    // The playlist choice is held too: picking one would enable a second, linking request.
    await pressWhileBusy(create, '/api/playlist/target/raid-link', undefined,
        () => assert.equal(raidSelect.disabled, true));
    assert.equal(raidSelect.disabled, false);
    // Linking an existing playlist follows the same rule, with Create held meanwhile.
    evaluate('raidPanel.populateRaidLinkSelect()'); await settle();
    raidSelect.value = raidSelect.options[1].value; page.change(raidSelect); await settle();
    await pressWhileBusy(page.byId('raid-link-btn'), '/api/playlist/target/raid-link', undefined,
        () => { assert.equal(create.disabled, true); assert.equal(raidSelect.disabled, true); });
    assert.equal(create.disabled, false); assert.equal(raidSelect.disabled, false);
    const field = page.byId('raid-url-input');
    field.value = 'https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M';
    const enter = () => page.key(field, 'Enter');
    await pressWhileBusy(page.byId('raid-add-url-btn'), '/api/playlist/target/raid-add-url', enter);
    clean();
""".replace("__STATUS__", json.dumps(status)))


def test_controls_that_hide_themselves_hand_focus_to_a_status_or_their_section(node, db_app):
    """Discard draft changes and each tool's Add all to draft hide once they act: focus goes to
    the save status or the tool's section, never to a control that writes."""
    routes = {api_path("/playlist/target/pair"): {"success": True, "paired": True, "pair": {
        "archive_playlist_id": "arch", "archive_playlist_name": "Arch", "auto_archive_on_remove": False}},
        api_path("/playlist/target/pair/archive-tracks"): {"success": True, "total": 1, "tracks": [
            {"uri": "spotify:track:a1", "name": "Archived 1", "artists": ["X"]}]},
        api_path("/playlist/target/rotation-status"): {"success": True, "schedule": None}}
    run_workspace(node, workspace_page(db_app), r"""
    await openWorkshop('/workshop/target', __ROUTES__);
    await removeRow(0);
    const discard = page.byId('undo-preview-btn');
    discard.focus(); discard.click(); await settle();
    assert.equal(discard.classList.contains('hidden'), true);
    assert.equal(focused(), page.byId('workshop-save-status'));
    assert.equal(page.text(page.byId('workshop-save-state')), 'Ready');
    page.byId('workshop-tool-inbox').click(); await settle();
    const addAll = page.$('[data-action-click="wk-047"]');
    addAll.focus(); addAll.click(); await settle();
    assert.equal(page.byId('inbox-raids-actions').classList.contains('hidden'), true);
    assert.equal(focused().getAttribute('aria-labelledby'), 'inbox-raids-heading');
    closeTask('workshop-inbox-panel'); await settle();
    page.byId('workshop-tool-archive').click(); await settle();
    const restoreAll = page.$('[data-action-click="wk-058"]');
    restoreAll.focus(); restoreAll.click(); await settle();
    assert.equal(page.byId('inbox-archive-actions').classList.contains('hidden'), true);
    assert.equal(focused(), page.byId('archive-tracks-section'));
    clean();
""".replace("__ROUTES__", json.dumps(routes)))


def test_forms_and_lists_that_close_hand_focus_to_their_opener_or_section(node, db_app):
    """The snapshot form returns focus to Take snapshot when cancelled or saved; Load more that
    ends the results, and a chosen external playlist, return it to their section; a saved
    Drip count returns it to the count."""
    link = {"raid_playlist_id": "coll", "raid_playlist_name": "Coll", "drip_enabled": True, "drip_count": 3}
    routes = {api_path("/playlist/target/raid-status"): {"raid_status": {
        "raid_link": link, "sources": [], "schedule": None, "drip_schedule": None,
        "source_count": 0, "max_sources": 10}}}
    run_workspace(node, workspace_page(db_app), HELD + r"""
    await openWorkshop('/workshop/target', __ROUTES__);
    page.byId('workshop-tool-history').click(); await settle();
    const take = page.byId('snapshot-take-btn');
    take.focus(); take.click(); await settle();
    const cancel = page.$('[data-action-click="wk-063"]');
    cancel.focus(); cancel.click(); await settle();
    assert.equal(focused(), take);
    take.click(); await settle();
    page.replies.push(page.reply({success: true, snapshot: {id: 9}}));
    const saveSnapshot = page.byId('snapshot-save-btn');
    saveSnapshot.focus(); saveSnapshot.click(); await settle();
    assert.equal(page.byId('snapshot-create-form').classList.contains('hidden'), true);
    assert.equal(focused(), take);
    closeTask('workshop-history-panel'); await settle();

    page.byId('workshop-tool-add').click(); await settle();
    page.byId('search-input').value = 'x';
    page.replies.push(page.reply(found(10)));
    page.byId('search-btn').click(); await settle();
    const more = page.byId('search-load-more');
    page.replies.push(page.reply(found(3)));
    more.focus(); more.click(); await settle();
    assert.equal(more.classList.contains('hidden'), true);
    assert.equal(focused().getAttribute('aria-labelledby'), 'workshop-catalog-heading');
    const field = page.byId('external-playlist-input');
    field.value = 'mix';
    page.replies.push(page.reply({success: true, mode: 'search',
        playlists: [{id: 'found', name: 'Found', owner_id: 'u1', track_count: 1, image_url: ''}]}));
    page.$('[data-action-click="wk-022"]').click(); await settle();
    const choice = page.$('#external-search-results button');
    page.replies.push(page.reply({success: true, mode: 'tracks', playlist: {id: 'found', name: 'Found'},
        tracks: []}));
    choice.focus(); choice.click(); await settle();
    assert.equal(focused().getAttribute('aria-labelledby'), 'workshop-external-heading');
    closeTask('workshop-add-panel'); await settle();

    page.byId('workshop-tool-inbox').click(); await settle();
    const count = page.byId('raid-drip-count'), saveCount = page.byId('raid-drip-count-save');
    count.value = '5'; page.input(count); await settle();
    page.replies.push(page.reply({success: true, raid_link: Object.assign({}, __LINK__, {drip_count: 5})}));
    saveCount.focus(); saveCount.click(); await settle();
    assert.equal(saveCount.classList.contains('hidden'), true);
    assert.equal(focused(), count);
    clean();
""".replace("__ROUTES__", json.dumps(routes)).replace("__LINK__", json.dumps(link)))


def test_save_panel_steps_keep_focus_in_the_panel(node, db_app):
    """Check current keeps focus while it checks and, once the check resolves the attention it
    belonged to, hands it to the panel heading; a step's retry, rebuilt as it starts, does the
    same. Keep reviewing my draft lands on the save status."""
    pair = {api_path("/playlist/target/pair"): {"success": True, "paired": True, "pair": {
        "archive_playlist_id": "arch", "archive_playlist_name": "Arch", "auto_archive_on_remove": True}},
        api_path("/playlist/target/pair/archive-tracks"): {"success": True, "tracks": [], "total": 0},
        api_path("/playlist/target/rotation-status"): {"success": True, "schedule": None},
        api_path("/workshop/target/source-context"): {"success": True,
            "target": {"id": "target", "name": "Morning Mix"},
            "archive": {"id": "arch", "name": "Arch", "baseline_track_uris": []}}}
    run_workspace(node, workspace_page(db_app), HELD + r"""
    await openWorkshop('/workshop/target', __PAIR__);
    await removeRow(1);
    page.byId('save-btn').click(); await settle();
    page.replies.push(page.reply({success: false, target_status: 'unknown', message: 'Not confirmed.'}, 502));
    page.byId('workshop-review-save').click(); await settle();
    // Keep reviewing my draft closes the panel onto the save status; its results reopen.
    page.byId('workshop-keep-draft').click(); await settle();
    assert.equal(page.byId('workshop-save-panel').hidden, true);
    assert.equal(focused(), page.byId('workshop-save-status'));
    page.byId('workshop-open-results').click(); await settle();
    const heading = page.byId('workshop-review-heading'), check = page.byId('workshop-check-current');
    const reply = held();
    check.focus(); check.click(); await settle();
    assert.equal(focused(), check); assert.ok(busy(check));
    check.click(); await settle();
    assert.equal(reads('/api/workshop/target/current-state'), 1);
    reply.answer({success: true, representable: true, current_track_uris: ['spotify:track:t1', 'spotify:track:t3'],
        current_tracks: [], loaded_at: '2026-10-12T09:00:00Z'}); await settle();
    assert.equal(page.byId('workshop-save-recovery').classList.contains('hidden'), true);
    assert.equal(focused(), heading);
    clean();
""".replace("__PAIR__", json.dumps(pair)))
    run_workspace(node, workspace_page(db_app), HELD + r"""
    await openWorkshop('/workshop/target', __PAIR__);
    page.byId('workshop-tool-archive').click(); await settle(); closeTask('workshop-archive-panel'); await settle();
    await removeRow(0);
    page.byId('save-btn').click(); await settle();
    page.replies.push(page.reply({success: true, target_status: 'saved',
        saved_track_uris: ['spotify:track:t2', 'spotify:track:t3']}));
    page.replies.push(page.reply({success: false, resource: 'arch', status: 'failed', retryable: true,
        error_code: 'upstream_read_failed', message: 'Could not read source.'}, 502));
    page.byId('workshop-review-save').click(); await settle();
    const heading = page.byId('workshop-review-heading'), retry = page.$('[data-source-retry="archive"]');
    const step = held();
    retry.focus(); retry.click(); await settle();
    assert.equal(retry.isConnected, false); assert.equal(focused(), heading);
    step.answer({success: true, resource: 'arch', status: 'complete', retryable: false}); await settle();
    assert.equal(focused(), heading);
    assert.equal(page.calls.filter(call => call.url === '/api/playlist/target/pair/archive').length, 2);
    clean();
""".replace("__PAIR__", json.dumps(pair)))


# ---------------------------------------------------------------------------------------
# Remove from draft undoes the staging the draft made.
# ---------------------------------------------------------------------------------------


def test_removing_a_staged_inbox_tracks_last_copy_returns_it_to_the_inbox(node, db_app):
    """An inbox track added to the draft, then removed, goes back to the inbox, so Save is not
    refused for staging the draft no longer holds; two inbox items for one track wait for its
    last copy, a track already in the playlist returns too, and adding it back stages it again."""
    pending = {"tracks": [
        {"id": 1, "track_uri": "spotify:track:p1", "track_name": "Pending 1", "track_artists": "A", "source_name": "S"},
        {"id": 2, "track_uri": "spotify:track:p1", "track_name": "Pending 1", "track_artists": "A", "source_name": "T"},
        {"id": 3, "track_uri": "spotify:track:t1", "track_name": "Track 1", "track_artists": "A", "source_name": "S"}]}
    routes = {api_path("/playlist/target/pending-raids"): pending,
        api_path("/workshop/target/source-context"): {"success": True,
            "target": {"id": "target", "name": "Morning Mix"},
            "raid": {"id": "coll", "name": "Coll"}}}
    run_workspace(node, workspace_page(db_app, tracks=[1, 1, 2]), r"""
    await openWorkshop('/workshop/target', __ROUTES__);
    const staged = () => plain(evaluate('pendingRaidsState.stagedPromotions.map(track => track.id)'));
    const inbox = () => plain(evaluate('pendingRaidsState.tracks.map(track => track.id)'));
    page.byId('workshop-tool-inbox').click(); await settle();
    page.$('[data-action-click="wk-047"]').click(); await settle();
    closeTask('workshop-inbox-panel'); await settle();
    assert.deepEqual(urisOf(), ['spotify:track:t1', 'spotify:track:t1', 'spotify:track:t2', 'spotify:track:p1']);
    assert.deepEqual(staged(), [1, 2, 3]);
    // Both inbox items for p1 go back with its one row.
    await removeRow(3);
    assert.deepEqual(staged(), [3]); assert.deepEqual(inbox(), [1, 2]);
    // t1 was in the playlist twice: its inbox item waits for the last copy, then goes back too.
    await removeRow(0);
    assert.deepEqual(staged(), [3]); assert.deepEqual(inbox(), [1, 2]);
    await removeRow(0);
    assert.deepEqual(staged(), []); assert.deepEqual(inbox(), [3, 1, 2]);
    assert.equal(page.$$('#inbox-raids-list > *').length, 3);
    assert.equal(page.byId('workshop-inbox-count').textContent.trim(), '3');
    assert.equal(evaluate('workshopState.archiveQueue.length'), 0);
    // Adding p1 back stages it again, and Save reviews it instead of refusing.
    page.byId('workshop-tool-inbox').click(); await settle();
    page.$('#inbox-raids-list [data-track-id="1"] .inbox-add-btn').click(); await settle();
    closeTask('workshop-inbox-panel'); await settle();
    assert.deepEqual(staged(), [1]);
    page.byId('save-btn').click(); await settle();
    assert.equal(page.byId('workshop-save-panel').hidden, false);
    assert.equal(page.$$('#workshop-source-results > li').length, 1);
    assert.ok(!page.notices.some(notice => /missing/.test(notice.message)));
    clean();
""".replace("__ROUTES__", json.dumps(routes)))


def test_only_a_copy_the_saved_playlist_had_queues_for_archiving(node, db_app):
    """With auto-archive on, removing a track the draft added queues nothing, so Save never
    appends a track that was never in the playlist; removing a saved copy queues one per copy,
    and adding a track back takes its queued archiving back."""
    routes = {api_path("/playlist/target/pair"): {"success": True, "paired": True, "pair": {
        "archive_playlist_id": "arch", "archive_playlist_name": "Arch", "auto_archive_on_remove": True}},
        api_path("/playlist/target/pair/archive-tracks"): {"success": True, "tracks": [], "total": 0},
        api_path("/playlist/target/rotation-status"): {"success": True, "schedule": None}}
    run_workspace(node, workspace_page(db_app, tracks=[1, 1, 2]), r"""
    await openWorkshop('/workshop/target', __ROUTES__);
    const queue = () => plain(evaluate('workshopState.archiveQueue'));
    page.byId('workshop-tool-archive').click(); await settle(); closeTask('workshop-archive-panel'); await settle();
    const search = async track => {
        page.byId('workshop-tool-add').click(); await settle();
        page.replies.push(page.reply({success: true, tracks: [track]}));
        page.byId('search-input').value = track.name; page.byId('search-btn').click(); await settle();
        page.$('#search-results .add-track-btn').click(); await settle();
        closeTask('workshop-add-panel'); await settle();
    };
    await search({id: 'new', uri: 'spotify:track:new', name: 'New Song', artists: ['A'], duration_ms: 1000});
    await removeRow(3);
    assert.deepEqual(queue(), []); assert.equal(evaluate('hasWorkshopDraft()'), false);
    // Each saved copy removed queues one; the badge counts them.
    await removeRow(0);
    assert.deepEqual(queue(), ['spotify:track:t1']);
    await removeRow(0);
    assert.deepEqual(queue(), ['spotify:track:t1', 'spotify:track:t1']);
    assert.equal(page.text(page.byId('archive-queue-badge')), '2 queued');
    // Adding the track back keeps the playlist one copy short: one copy stays queued.
    await search({id: 't1', uri: 'spotify:track:t1', name: 'Track 1', artists: ['A'], duration_ms: 1000});
    assert.deepEqual(queue(), ['spotify:track:t1']);
    assert.equal(page.text(page.byId('archive-queue-badge')), '1 queued');
    clean();
""".replace("__ROUTES__", json.dumps(routes)))


def test_a_preview_applies_only_to_the_draft_it_was_asked_for(node, db_app):
    """An edit made while Preview shuffle waits keeps the user's draft (and what it queued for
    archiving) and says the preview was not applied; a lock changed meanwhile counts as an edit,
    so a stale order never moves a row locked since. An unchanged draft takes the reply."""
    routes = {api_path("/playlist/target/pair"): {"success": True, "paired": True, "pair": {
        "archive_playlist_id": "arch", "archive_playlist_name": "Arch", "auto_archive_on_remove": True}},
        api_path("/playlist/target/pair/archive-tracks"): {"success": True, "tracks": [], "total": 0},
        api_path("/playlist/target/rotation-status"): {"success": True, "schedule": None}}
    run_workspace(node, workspace_page(db_app), HELD + r"""
    await openWorkshop('/workshop/target', __ROUTES__);
    page.byId('workshop-tool-archive').click(); await settle(); closeTask('workshop-archive-panel'); await settle();
    const t = n => 'spotify:track:t' + n, preview = page.byId('preview-shuffle-btn');
    const notApplied = () => /^Preview not applied: your draft changed/.test(page.notices.at(-1).message);
    let shuffle = held();
    preview.click(); await settle();
    await removeRow(0);
    shuffle.answer({success: true, shuffled_uris: [t(3), t(1), t(2)], algorithm_name: 'Basic'}); await settle();
    assert.deepEqual(urisOf(), [t(2), t(3)]); agrees();
    assert.deepEqual(plain(evaluate('workshopState.archiveQueue')), [t(1)]);
    assert.ok(notApplied());
    // Locking Track 2 in place while the preview waits.
    shuffle = held();
    preview.click(); await settle();
    page.replies.push(page.reply({success: true, lock: {lock_tier: 'standard', position: 0, expires_at: null}}));
    openActions(0); rowAction('lock').click(); await settle();
    shuffle.answer({success: true, shuffled_uris: [t(3), t(2)], algorithm_name: 'Basic'}); await settle();
    assert.deepEqual(urisOf(), [t(2), t(3)]); assert.ok(notApplied());
    // Nothing changed meanwhile: the preview applies.
    page.replies.push(page.reply({success: true, lock: null}));
    rowAction('lock').click(); await settle();
    shuffle = held();
    preview.click(); await settle();
    shuffle.answer({success: true, shuffled_uris: [t(3), t(2)], algorithm_name: 'Basic'}); await settle();
    assert.deepEqual(urisOf(), [t(3), t(2)]); agrees();
    assert.equal(page.notices.at(-1).message, 'Preview applied: Basic');
    clean();
""".replace("__ROUTES__", json.dumps(routes)))


def test_an_adopted_playlist_keeps_focus_on_the_same_rows_actions(node, db_app):
    """Adopting a verified playlist rebuilds the list in place: focus on a row's actions button,
    or in its open row actions, returns to the same occurrence's actions button, else the list
    heading; focus outside the list stays where it is."""
    run_workspace(node, workspace_page(db_app), r"""
    await openWorkshop();
    const t = n => 'spotify:track:t' + n;
    const adopt = async uris => {
        const tracks = uris.map(uri => ({uri, id: uri.split(':').pop(), name: 'Song ' + uri.split(':').pop(),
            artists: ['A'], duration_ms: 1000}));
        page.replies.push(page.reply({success: true, representable: true, current_track_uris: uris,
            current_tracks: tracks, loaded_at: '2026-10-12T09:00:00Z'}));
        assert.equal(await evaluate('adoptVerifiedTarget()'), true); await settle();
    };
    const key = keysOf()[1];
    actionsButton(1).focus();
    await adopt([t(2), t(1), t(4)]);
    assert.deepEqual(urisOf(), [t(2), t(1), t(4)]);
    assert.equal(focused(), actionsButton(0)); assert.equal(keysOf()[0], key);
    openActions(1); rowAction('down').focus();
    await adopt([t(1), t(2), t(4)]);
    assert.equal(page.byId('track-actions').hidden, true);
    assert.equal(focused(), actionsButton(0)); assert.equal(focused().closest('li').dataset.uri, t(1));
    actionsButton(1).focus();
    await adopt([t(1), t(4)]);
    assert.equal(focused(), page.byId('workshop-tracks-heading'));
    const preview = page.byId('preview-shuffle-btn');
    preview.focus();
    await adopt([t(4), t(1)]);
    assert.equal(focused(), preview);
    clean();
""")


# ---------------------------------------------------------------------------------------
# Focus handoffs and tool views in the workspace.
# ---------------------------------------------------------------------------------------

# Every element focus reaches, in order, to show where it went on its way (focusin bubbles).
SEEN = r"""
const seen = [];
function watchFocus() {
    seen.length = 0;
    page.document.addEventListener('focusin', event => seen.push(event.target));
}
"""


def test_closing_a_review_with_nothing_left_to_save_keeps_focus_in_the_draft_footer(node, db_app):
    """After a save leaves no draft, Review & save is unavailable: Back to Workshop and Escape
    return focus to the draft footer, a named section that stays in place, never by way of
    the Tracks heading (which would scroll the list away). Keep reviewing my draft, in the
    Attention state, reaches the save status the same way."""
    context = {api_path("/workshop/target/source-context"): {"success": True,
        "target": {"id": "target", "name": "Morning Mix"}}}
    run_workspace(node, workspace_page(db_app), SEEN + r"""
    await openWorkshop('/workshop/target', __CONTEXT__);
    const footer = page.byId('workshop-draft-footer'), tracks = page.byId('workshop-tracks-heading');
    assert.equal(footer.getAttribute('role'), 'group');
    assert.equal(page.text(page.byId(footer.getAttribute('aria-labelledby'))), 'Your changes');
    watchFocus();
    async function saveThenClose(close, saved) {
        page.byId('save-btn').focus(); page.byId('save-btn').click(); await settle();
        page.replies.push(page.reply({success: true, target_status: 'saved', saved_track_uris: saved}));
        page.byId('workshop-review-save').click(); await settle();
        seen.length = 0;
        close(); await settle();
        assert.equal(page.byId('workshop-save-panel').hidden, true);
        assert.equal(page.byId('save-btn').disabled, true);
        assert.equal(focused(), footer); assert.ok(!seen.includes(tracks));
    }
    await removeRow(1);
    await saveThenClose(() => page.byId('workshop-review-back').click(), ['spotify:track:t1', 'spotify:track:t3']);
    await removeRow(0);
    await saveThenClose(() => page.key(page.byId('workshop-review-heading'), 'Escape'), ['spotify:track:t3']);
    clean();
""".replace("__CONTEXT__", json.dumps(context)))
    run_workspace(node, workspace_page(db_app), SEEN + r"""
    await openWorkshop('/workshop/target', __CONTEXT__);
    await removeRow(1);
    page.byId('save-btn').focus(); page.byId('save-btn').click(); await settle();
    page.replies.push(page.reply({success: false, target_status: 'unknown', message: 'Not confirmed.'}, 502));
    page.byId('workshop-review-save').click(); await settle();
    assert.equal(page.byId('save-btn').disabled, true);
    watchFocus();
    page.byId('workshop-keep-draft').click(); await settle();
    assert.equal(focused(), page.byId('workshop-save-status'));
    assert.ok(!seen.includes(page.byId('workshop-tracks-heading')));
    clean();
""".replace("__CONTEXT__", json.dumps(context)))


def test_add_source_at_the_limit_hands_focus_to_its_section(node, db_app):
    """Adding the tenth of ten sources disables Add source now when the routines refresh; focus
    goes to the Add a source section instead of falling to the page."""
    sources = [{"id": n, "source_name": f"Source {n}", "raid_count": 5} for n in range(1, 10)]
    run_workspace(node, workspace_page(db_app), r"""
    const status = {raid_status: {raid_link: null, sources: __SOURCES__, schedule: null, drip_schedule: null,
        source_count: 9, max_sources: 10}};
    await openWorkshop('/workshop/target?setup=raids', {'/api/playlist/target/raid-status': status});
    const add = page.byId('raid-add-url-btn');
    assert.equal(add.disabled, false);
    page.byId('raid-url-input').value = 'https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M';
    // The refresh after the add reads the tenth source.
    status.raid_status.sources.push({id: 10, source_name: 'Source 10', raid_count: 5});
    status.raid_status.source_count = 10;
    page.replies.push(page.reply({success: true}));
    add.focus(); add.click(); await settle();
    assert.equal(add.disabled, true);
    assert.equal(page.text(page.byId('raid-source-count')), '10 / 10');
    assert.equal(focused().getAttribute('aria-labelledby'), 'raid-add-heading');
    clean();
""".replace("__SOURCES__", json.dumps(sources)))


def test_a_tool_set_aside_keeps_its_view_when_the_step_over_it_is_replaced(node, db_app):
    """Only a shown tool remembers its body's scroll: when a live review replaces Source setup,
    the Inbox it was nested in (hidden, so its scroll reads 0 in browsers) keeps what it
    remembered when it was set aside."""
    status = {"raid_status": {"raid_link": None, "sources": [{"id": 1, "source_name": "One", "raid_count": 5}],
        "schedule": {"id": 5, "job_type": "raid", "schedule_type": "interval", "schedule_value": "daily",
            "is_enabled": True}, "drip_schedule": None, "source_count": 1, "max_sources": 10}}
    run_workspace(node, workspace_page(db_app), r"""
    await openWorkshop('/workshop/target', {'/api/playlist/target/raid-status': __STATUS__});
    const remembered = evaluate(`(() => {
        const calls = [], remember = PlaylistWorkspace.rememberView;
        PlaylistWorkspace.rememberView = function (name, body) {
            calls.push(name + (body.closest('[data-app-panel]').hidden ? ' (hidden)' : ''));
            return remember.apply(this, arguments);
        };
        return calls;
    })()`);
    page.byId('workshop-tool-inbox').click(); await settle();
    page.byId('workshop-sources-btn').click(); await settle();
    assert.equal(page.byId('workshop-sources-panel').hidden, false);
    page.replies.push(page.reply({success: false, error_code: 'schedule_unavailable', message: 'Unavailable.'}, 503));
    page.byId('raid-now-btn').click(); await settle();
    assert.equal(page.byId('live-operation-panel').hidden, false);
    assert.deepEqual(plain(remembered), ['inbox', 'sources']);
    clean();
""".replace("__STATUS__", json.dumps(status)))


def test_a_suggested_search_waits_for_the_search_in_flight(node, db_app):
    """While a search runs, a restricted playlist's suggestion leaves the field and its results
    as they are and sends nothing; once the search ends it searches."""
    restricted = {"success": True, "mode": "restricted", "playlist": {"name": "Locked list", "track_count": None},
        "message": "Spotify keeps this playlist private.", "suggested_search": "Locked list"}
    run_workspace(node, workspace_page(db_app), HELD + r"""
    await openWorkshop();
    page.byId('workshop-tool-add').click(); await settle();
    const field = page.byId('search-input');
    field.value = 'first';
    const reply = held();
    page.byId('search-btn').click(); await settle();
    page.byId('external-playlist-input').value = 'Locked list';
    page.replies.push(page.reply(__RESTRICTED__));
    page.$('#workshop-add-panel [data-action-click="wk-022"]').click(); await settle();
    const suggestion = page.$('[data-action-click="wk-suggest-search"]');
    suggestion.click(); await settle();
    assert.equal(field.value, 'first'); assert.equal(searches(), 1);
    reply.answer(found(2)); await settle();
    assert.equal(page.$$('#search-results .add-track-btn').length, 2);
    page.replies.push(page.reply(found(1)));
    suggestion.click(); await settle();
    assert.equal(field.value, 'Locked list'); assert.equal(searches(), 2);
    assert.equal(JSON.parse(page.calls.at(-1).body).query, 'Locked list');
    clean();
""".replace("__RESTRICTED__", json.dumps(restricted)))


# The shared action footer's scroll padding: on while a shown footer does not hold focus.
ACTION_FOOTER_LIFT = (
    "html:has([data-action-footer]:not([hidden], [hidden] *))"
    ":not(:has([data-action-footer]:focus-within))"
)


def test_the_footer_space_lifts_while_focus_is_in_the_draft_footer_and_only_below_the_workspace_width():
    """Below the workspace width, focused or scrolled-to content clears the sticky draft footer
    by its measured space, lifted while focus is in the footer itself (so focusing it never
    scrolls the list away) and only while the footer shows (a narrow task hides it with the
    page); at the workspace width the footer is in flow and the space is 0. The draft footer
    and the setup page's actions are one shared recipe (components/action-footer.css)."""
    css = re.sub(r"/\*.*?\*/", "", (ROOT / "assets/design/components/action-footer.css").read_text(), flags=re.S)
    start = css.index("@screen sh-workspace")
    depth, end = 0, css.index("{", start)
    for index in range(end, len(css)):
        depth += {"{": 1, "}": -1}.get(css[index], 0)
        if depth == 0:
            end = index
            break
    wide, narrow = css[start:end], css[:start] + css[end:]

    def declarations(part, selector):
        return [re.sub(r"\s+", " ", body).strip() for name, body in _css_rules(part) if name == selector]

    assert declarations(narrow, ACTION_FOOTER_LIFT) == [
        "scroll-padding-bottom: var(--app-footer-space, var(--sh-space-22));"]
    assert declarations(wide, ACTION_FOOTER_LIFT) == ["scroll-padding-bottom: 0;"]
    # Sticky at the bottom, after the content, below the workspace width; in flow and bordered from it.
    sticky = declarations(narrow, ".sh-action-footer")[0]
    for declaration in ("order: 1;", "position: sticky;", "bottom: 0;", "border-top: 1px solid var(--sh-border);",
                        "background: var(--sh-base);"):
        assert declaration in sticky, declaration
    [static] = declarations(wide, ".sh-action-footer")
    for declaration in ("order: 0;", "position: static;", "margin-inline: 0;", "border: 1px solid var(--sh-border);",
                        "border-radius: var(--sh-radius-panel);", "background: var(--sh-surface);"):
        assert declaration in static, declaration
    # The committed build keeps both paddings, the workspace-width one after the narrow one.
    compiled = re.sub(r"\s+", "", (ROOT / "shuffify/static/css/tailwind.css").read_text())
    built = re.sub(r"\s+", "", ACTION_FOOTER_LIFT)
    narrow_at = compiled.index(built + "{scroll-padding-bottom:var(--app-footer-space,var(--sh-space-22))}")
    assert compiled.index(built + "{scroll-padding-bottom:0}") > narrow_at
    # Both footers are that one recipe, and neither page restates it.
    for template, footer in (("workshop.html", "workshop-draft-footer"), ("playlist_setup.html", "setup-footer")):
        markup = (ROOT / "shuffify/templates" / template).read_text()
        tag = re.search(r'<div id="' + footer + r'"[^>]*>', markup).group(0)
        assert "sh-action-footer" in re.search(r'class="([^"]*)"', tag).group(1).split(), footer
        assert " data-action-footer " in tag, footer
    for page in ("workspace.css", "setup.css"):
        rules = _css_rules((ROOT / "assets/design/pages" / page).read_text())
        restated = ("footer-space", "draft-footer", "#setup-footer")
        assert not [name for name, _ in rules if any(word in name for word in restated)], page
        assert not [body for _, body in rules if "scroll-padding" in body or "footer-space" in body], page
    setup_footer = " ".join(body for name, body in _css_rules((ROOT / "assets/design/pages/setup.css").read_text())
                            if name == ".sh-setup-footer")
    assert not re.search(r"\b(?:order|position|bottom|margin-inline|border|background|padding)\s*:", setup_footer)
