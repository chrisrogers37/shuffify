"""Execute the shipped save client and its Workshop integration with Node."""

import shutil
from pathlib import Path

import pytest

from tests.node_runtime import run_scenario

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is needed for Workshop runtime checks")
    return executable


def run_node(node, body):
    run_scenario(node, body, ROOT)


CLIENT = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const calls = [], replies = [];
const window = {fetch: async (url, options) => {
    calls.push({url, options});
    const reply = replies.shift();
    return typeof reply === 'function' ? reply() : reply;
}};
const response = (body, status = 200) => ({ok: status < 400, status,
    json: async () => body, clone: () => ({arrayBuffer: async () => new ArrayBuffer(0)})});
const context = {window, AbortController, setTimeout, clearTimeout, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync('shuffify/static/js/workshop-save.js', 'utf8'), context);
const client = window.WorkshopSave;
client.configure('playlist');
const plain = value => JSON.parse(JSON.stringify(value));
"""


def test_immutable_duplicate_and_empty_requests_and_inflight_guard(node):
    run_node(
        node,
        CLIENT
        + """
(async () => {
    let complete;
    replies.push(() => new Promise(resolve => {complete = resolve;}));
    const baseline = ['a', 'a', 'b'], desired = ['b', 'a', 'a'];
    const pending = client.submit({baselineTrackUris: baseline, trackUris: desired});
    baseline.pop(); desired.reverse();
    const duplicate = await client.submit({baselineTrackUris: [], trackUris: []});
    assert.equal(duplicate.error_code, 'operation_in_progress');
    assert.equal(calls.length, 1);
    assert.deepEqual(JSON.parse(calls[0].options.body), {
        baseline_track_uris: ['a', 'a', 'b'], track_uris: ['b', 'a', 'a']});
    complete(response({success: true, target_status: 'saved', saved_track_uris: ['b', 'a', 'a']}));
    assert.equal((await pending).success, true);
    replies.push(response({success: true, target_status: 'already_applied', saved_track_uris: []}));
    assert.equal((await client.submit({baselineTrackUris: [], trackUris: []})).success, true);
    assert.deepEqual(JSON.parse(calls[1].options.body), {baseline_track_uris: [], track_uris: []});
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize(
    "result",
    [
        "response({success: true, target_status: 'saved', saved_track_uris: ['wrong']})",
        "response({success: true})",
        "response({success: false, message: 'Unavailable'}, 503)",
        "response({success: false, message: 'Bad request'}, 400)",
        "() => {throw new Error('connection lost');}",
        "{ok: false, status: 502, json: async () => {throw new Error('not JSON');}}",
    ],
)
def test_uncertain_responses_never_replay_or_claim_saved(node, result):
    run_node(
        node,
        CLIENT
        + f"""
(async () => {{
    replies.push({result});
    const result = await client.submit({{baselineTrackUris: ['a'], trackUris: ['b']}});
    assert.equal(result.success, false);
    assert.equal(result.target_status, 'unknown');
    assert.equal(result.requires_refresh, true);
    assert.equal(result.retryable, false);
    assert.equal(result.saved_track_uris, undefined);
    assert.equal(calls.length, 1);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_structured_failure_and_read_only_reconciliation(node):
    run_node(
        node,
        CLIENT
        + """
(async () => {
    for (const target_status of ['partial', 'unknown']) {
        replies.push(response({success: false, target_status, retryable: true}, 502));
        const result = await client.submit({baselineTrackUris: ['a'], trackUris: ['b']});
        assert.equal(result.target_status, target_status);
        assert.equal(result.retryable, false);
    }
    replies.push(response({success: false, target_status: 'not_written',
        error_code: 'stale_draft', current_track_uris: ['c'], retryable: false}, 409));
    const stale = await client.submit({baselineTrackUris: ['a'], trackUris: ['b']});
    assert.deepEqual(plain(stale.current_track_uris), ['c']);
    replies.push(response({success: true, current_track_uris: ['b'], representable: true}));
    assert.equal((await client.readCurrent('playlist')).success, true);
    assert.equal(calls.at(-1).url, '/api/workshop/playlist/current-state');
    assert.equal(calls.at(-1).options.method, 'GET');
    assert.equal(calls.at(-1).options.cache, 'no-store');
    replies.push(response({success: false, error_code: 'operation_in_progress'}, 409));
    assert.equal((await client.readCurrent()).error_code, 'operation_in_progress');
    replies.push(response({success: true, current_track_uris: [], representable: false}));
    assert.equal((await client.readCurrent()).success, false);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


UI = (
    CLIENT
    + """
const elements = new Map(), events = {}, notices = [], announcements = [];
function element(id) {
    if (!elements.has(id)) elements.set(id, {id, disabled: false, textContent: '', children: [], dataset: {},
        classList: {toggle() {}, remove() {}, add() {}},
        setAttribute(name, value) {this[name] = value;}, getAttribute(name) {return this[name];},
        removeAttribute(name) {delete this[name];},
        replaceChildren(...nodes) {this.children = nodes;}, appendChild(child) {this.children.push(child);},
        addEventListener(type, callback) {this['on' + type] = callback;},
        closest(selector) {return selector === '[data-workshop-compat]' && this.dataset.workshopCompat ? this : null;},
        focus() {this.focused = true;}});
    return elements.get(id);
}
// A few grouped controls stand in for the page: a draft edit, a routine pause and a view tab.
element('edit-btn').dataset.workshopCompat = 'draft';
element('pause-btn').dataset.workshopCompat = 'pause';
element('view-btn').dataset.workshopCompat = 'view';
const document = {getElementById: element, createElement: () => element('generated-' + elements.size),
    // Each simple selector the renderer asks for: a held control or a control of one group.
    querySelectorAll: selector => [...elements.values()].filter(el => selector.split(', ').some(part =>
        part === '[data-compat-disabled]' ? el.dataset.compatDisabled !== undefined
            : el.dataset.workshopCompat === part.slice(23, -2))),
    addEventListener(type, callback) {(events[type] ||= []).push(callback);}};
window.addEventListener = () => {};
const state = {playlistId: 'playlist', savedUris: ['a', 'b'], workingUris: ['b', 'a'],
    isDirty: true, isSaving: false, isFinalizing: false, isShuffling: false,
    pendingMutations: 0, pendingWrites: 0, saveAttention: null, saveIntent: null, observedCurrent: null,
    sourceRecovery: [], saveResults: [], review: null, reviewMode: null, isPreparing: false,
    playlistName: 'Target', loadedAt: null, archiveQueue: [], stagedRestorations: [], archiveTracks: []};
const pendingRaidsState = {tracks: [], stagedPromotions: [], dismissing: new Set()};
const trackDataByUri = {a: {name: 'A'}, b: {name: 'B'}};
Object.assign(context, {document, workshopState: state, pendingRaidsState, trackDataByUri,
    PlaylistOperations: {hasUnresolved: () => false, unresolvedClaims: () => []}, raidPanel: {status: null},
    WorkshopSave: client, fetch: window.fetch, sortableInstance: {option(name, value) {this[name] = value;}},
    showNotification: (message, level) => notices.push({message, level}),
    updateArchiveQueueBadge() {}, renderInboxArchiveTracks() {},
    renderPendingRaids() {}, updatePendingBadge() {}, fullRerenderTrackList() {}, updateTrackCount() {},
    AppPanels: {open: async (id, options) => {context.panelOpen = {id, options}; return true;},
        requestClose: async () => true, announce: message => announcements.push(message)},
    PlaylistWorkspace: (vm.runInContext(fs.readFileSync('shuffify/static/js/playlist-workspace.js', 'utf8'), context),
        window.PlaylistWorkspace),
    markDirty() {state.isDirty = context.hasWorkshopDraft();
        element('save-btn').disabled = !context.canSaveWorkshop(); context.renderSaveStatus();},
});
const template = fs.readFileSync('shuffify/templates/workshop.html', 'utf8');
const uiStart = template.indexOf('function workshopBusy()');
const uiEnd = template.indexOf('// Undo Preview', uiStart);
vm.runInContext(template.slice(uiStart, uiEnd), context);
const sourceContext = {success: true, target: {id: 'playlist', name: 'Target'},
    raid: {id: 'collection', name: 'Collection'},
    archive: {id: 'archive', name: 'Archive', baseline_track_uris: ['old', 'b', 'b']}};
const contextReads = [];
const originalFetch = window.fetch;
window.fetch = async (url, options) => {
    if (url.includes('/source-context')) {
        contextReads.push({url, options}); return response(sourceContext);
    }
    return originalFetch(url, options);
};
async function saveThroughReview() {
    await context.commitToSpotify();
    await context.saveReviewedWorkshop();
}
const completeSource = resource => response({success: true, resource, status: 'complete', retryable: false});
const failedSource = resource => response({success: false, resource, status: 'failed', retryable: true,
    error_code: 'upstream_read_failed', message: 'Could not read source.'}, 502);
const savedTarget = () => response({success: true, target_status: 'saved',
    saved_track_uris: state.workingUris.slice()});
const observed = (uris, tracks = []) => ({success: true, representable: true,
    current_track_uris: uris, current_tracks: tracks, loaded_at: '2026-10-11T12:00:00Z'});
"""
)


def test_review_summary_says_when_the_order_is_unchanged(node):
    run_node(
        node,
        UI
        + """
(async () => {
    // A track added at the end leaves the order of the tracks kept as it was.
    state.workingUris = ['a', 'b', 'c'];
    await context.commitToSpotify();
    assert.equal(context.panelOpen.id, 'workshop-save-panel');
    assert.equal(element('workshop-review-summary').textContent,
        'Target: 1 added, 0 removed (counting repeated tracks). Order unchanged. Final size: 3.');
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_review_is_read_only_names_sources_and_invalidates_changed_staging(node):
    run_node(
        node,
        UI
        + """
(async () => {
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}];
    state.archiveQueue = ['a', 'a'];
    await context.commitToSpotify();
    assert.equal(calls.length, 0);
    assert.equal(contextReads.length, 1);
    assert.equal(contextReads[0].options.method, 'GET');
    assert.equal(context.panelOpen.id, 'workshop-save-panel');
    assert.equal(context.panelOpen.options.modal, false);
    assert.equal(element('workshop-review-save').disabled, false);
    // The summary names the target, counts repeated tracks and says the order changed.
    assert.equal(element('workshop-review-summary').textContent,
        'Target: 0 added, 0 removed (counting repeated tracks). Order changed. Final size: 2.');
    assert.equal(state.review.sources[0].resourceName, 'Collection');
    assert.equal(state.review.sources[1].resourceName, 'Archive');
    assert.equal(Object.isFrozen(state.review.sources[1].body.track_uris), true);
    state.archiveQueue.push('new'); context.markDirty();
    assert.equal(element('workshop-review-save').disabled, true);
    await context.saveReviewedWorkshop();
    assert.equal(calls.length, 0);
    assert.match(element('workshop-review-message').textContent, /changed/);
    assert.deepEqual(plain(state.review.sources[1].body.track_uris), ['a', 'a']);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_duplicate_aware_summary_and_restore_append_baseline(node):
    run_node(
        node,
        UI
        + """
(async () => {
    assert.deepEqual(plain(client.summarize(['a', 'a', 'b'], ['b', 'a', 'c'])),
        {added: 1, removed: 1, orderChanged: true});
    state.stagedRestorations = [{uri: 'b'}]; state.archiveQueue = ['a', 'a'];
    await context.commitToSpotify();
    assert.deepEqual(plain(state.review.sources[1].body.baseline_track_uris), ['old']);
    assert.deepEqual(plain(state.review.sources[1].body.track_uris), ['a', 'a']);
    assert.deepEqual(plain(state.review.sources[1].dependsOn), ['restorations']);
    assert.match(context.sourceTaskDescription(state.review.sources[0]), /ALL occurrences/);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_explicit_preflight_failure_keeps_draft_and_staging_for_reviewed_retry(node):
    run_node(
        node,
        UI
        + """
(async () => {
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}];
    state.stagedRestorations = [{uri: 'b'}]; state.archiveQueue = ['a'];
    const sources = plain(context.captureSourceIntent());
    const failure = {success: false, target_status: 'not_written',
        error_code: 'preflight_failed', requires_refresh: false, retryable: true,
        message: 'Database is unavailable.'};
    replies.push(response(failure, 503));
    await saveThroughReview();
    assert.deepEqual(plain(state.savedUris), ['a', 'b']);
    assert.deepEqual(plain(state.workingUris), ['b', 'a']);
    assert.deepEqual(plain(context.captureSourceIntent()), sources);
    assert.equal(state.sourceRecovery.length, 0);
    assert.equal(state.saveAttention.target_status, 'not_written');
    assert.equal(context.canSaveWorkshop(), true);
    assert.equal(calls.length, 1);
    replies.push(response(failure, 503));
    await saveThroughReview();
    assert.equal(calls.length, 2);
    assert.equal(calls[1].options.body, calls[0].options.body);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_stale_keeps_draft_and_adoption_is_local_before_new_baseline_save(node):
    run_node(
        node,
        UI
        + """
(async () => {
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}];
    state.stagedRestorations = [{uri: 'b'}]; state.archiveQueue = ['a'];
    replies.push(response({...observed(['new', 'a'], [{uri: 'new', name: '<New title>'}]),
        success: false, target_status: 'not_written', error_code: 'stale_draft', retryable: false,
        message: 'Spotify changed. Your draft is still here.'}, 409));
    await saveThroughReview();
    assert.deepEqual(plain(state.savedUris), ['a', 'b']);
    assert.deepEqual(plain(state.workingUris), ['b', 'a']);
    assert.equal(context.canSaveWorkshop(), false);
    assert.equal(element('workshop-save-state').textContent, 'Attention');
    assert.equal(element('workshop-review-heading').focused, true);
    // Using the current Spotify version replaces the order through remap: 'a' keeps its key.
    const keyOf = uri => context.draftOccurrences().find(item => item.uri === uri).key;
    const kept = keyOf('a');
    await context.keepReviewingDraft(); context.useCurrentSpotify();
    assert.equal(calls.length, 1);
    assert.deepEqual(plain(state.workingUris), ['new', 'a']);
    assert.equal(keyOf('a'), kept); assert.notEqual(keyOf('new'), kept);
    assert.deepEqual(plain(context.PlaylistWorkspace.uris(state.occurrences)), ['new', 'a']);
    assert.deepEqual(plain(state.savedUris), ['new', 'a']);
    assert.equal(trackDataByUri.new.name, '<New title>');
    assert.deepEqual(plain(context.captureSourceIntent()), []);
    state.workingUris = ['a', 'new']; context.markDirty();
    replies.push(savedTarget()); await saveThroughReview();
    assert.deepEqual(JSON.parse(calls[1].options.body), {
        baseline_track_uris: ['new', 'a'], track_uris: ['a', 'new']});
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_target_commit_serializes_all_but_view_and_pause_and_final_save_dispatches_once(node):
    run_node(
        node,
        UI
        + """
(async () => {
    await context.commitToSpotify();
    let complete;
    replies.push(() => new Promise(resolve => {complete = resolve;}));
    element('edit-btn').disabled = false;
    const saving = context.saveReviewedWorkshop();
    assert.equal(state.isSaving, true);
    // The commit blocks the draft control and drag reordering; view and pause stay usable.
    assert.equal(element('edit-btn').disabled, true);
    assert.match(element('edit-btn')['aria-describedby'], /^workshop-compat-reason-draft$/);
    assert.equal(element('pause-btn').disabled, false); assert.equal(element('view-btn').disabled, false);
    assert.equal(context.sortableInstance.disabled, true);
    const reasons = element('workshop-compat-reasons').children;
    assert.deepEqual(reasons.find(node => node.id === 'workshop-compat-reason-draft').textContent,
        'Unavailable while your draft saves to Spotify');
    // The barrier refuses events only for a control in a blocked group.
    let prevented = 0, stopped = 0;
    const event = target => ({target, key: 'Enter', preventDefault() {prevented++;},
        stopImmediatePropagation() {stopped++;}});
    events.click[0](event(element('edit-btn')));
    assert.equal(prevented, 1); assert.equal(stopped, 1);
    events.click[0](event(element('pause-btn'))); events.click[0](event(element('view-btn')));
    events.click[0](event({closest: () => null}));
    assert.equal(prevented, 1); assert.equal(stopped, 1);
    // A write waits while its group does; a pause write and a POST read never wait.
    await assert.rejects(context.workshopRequest('/locks/toggle', {method: 'POST'}, {group: 'locks'}),
        /Unavailable while your draft saves to Spotify/);
    replies.push(response({success: true}));
    assert.equal((await context.workshopRequest(
        '/api/schedules/5/toggle', {method: 'POST'}, {group: 'pause'})).ok, true);
    replies.push(response({success: true, tracks: []}));
    assert.equal((await context.workshopRequest('/api/workshop/search', {method: 'POST'}, {read: true})).ok, true);
    await context.saveReviewedWorkshop();
    assert.equal(calls.filter(call => call.url.endsWith('/commit')).length, 1);
    assert.equal(context.panelOpen.options.beforeClose(), false);
    for (const name of ['removeFromDraft', 'moveTrackOccurrence', 'addTrackFromSearch', 'promotePendingRaid',
        'promoteAllPendingRaids', 'stageArchiveRestore', 'restoreAllArchiveTracks', 'undoPreview']) {
        const start = template.indexOf('function ' + name + '(');
        const end = template.indexOf('\\nfunction ', start + 1);
        vm.runInContext(template.slice(start, end), context);
        context[name]();
    }
    assert.deepEqual(plain(state.workingUris), ['b', 'a']);
    complete(response({success: true, target_status: 'saved', saved_track_uris: ['b', 'a']}));
    await saving;
    assert.equal(state.isSaving, false);
    // Its own state returns once the block lifts, and its description no longer names a reason.
    assert.equal(element('edit-btn').disabled, false);
    assert.equal(element('edit-btn')['aria-describedby'], undefined);
    assert.equal(element('edit-btn').dataset.compatDisabled, undefined);
    assert.equal(context.sortableInstance.disabled, false);
    state.workingUris = ['a', 'b']; context.markDirty();
    state.isShuffling = true;
    await context.commitToSpotify();
    assert.equal(calls.filter(call => call.url.endsWith('/commit')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_finalize_keeps_only_draft_link_archive_unlink_and_dismissal_blocked(node):
    run_node(
        node,
        UI
        + """
(async () => {
    const groups = ['draft', 'locks', 'link', 'drip-gate', 'drip-count', 'sources', 'pair', 'pair-unlink',
        'dismiss', 'routine', 'create', 'view', 'pause', 'save', 'auto-archive'];
    const blockedDuring = () => groups.filter(group => context.workshopBlockedReason(group) !== null);
    state.isSaving = true;
    assert.deepEqual(blockedDuring(), ['draft', 'locks', 'link', 'drip-gate', 'drip-count', 'sources', 'pair',
        'pair-unlink', 'dismiss', 'routine', 'create']);
    state.isSaving = false; state.isFinalizing = true;
    assert.deepEqual(blockedDuring(), ['draft', 'link', 'pair-unlink', 'dismiss']);
    assert.equal(context.workshopBlockedReason('dismiss'),
        'Unavailable while your saved collection and archive changes finish');
    // A routine's own key follows the commit through its group.
    state.isFinalizing = false; state.isSaving = true;
    assert.equal(context.workshopBlockedReason('routine:5'), 'Unavailable while your draft saves to Spotify');
    state.isSaving = false;
    assert.deepEqual(blockedDuring(), []);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_read_reconciliation_confirms_revision_without_source_writes(node):
    run_node(
        node,
        UI
        + """
(async () => {
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}];
    replies.push(() => {throw new Error('lost response');});
    await saveThroughReview();
    assert.equal(state.saveAttention.target_status, 'unknown');
    pendingRaidsState.stagedPromotions.push({id: 8, track_uri: 'c'});
    state.workingUris.push('c'); context.markDirty();
    replies.push(response({success: false, error_code: 'operation_in_progress', message: 'Busy'}, 409));
    await context.checkCurrentSpotify();
    assert.equal(state.observedCurrent, null);
    replies.push(response(observed(['b', 'a'])));
    await context.checkCurrentSpotify();
    assert.deepEqual(plain(state.savedUris), ['b', 'a']);
    assert.deepEqual(plain(state.workingUris), ['b', 'a', 'c']);
    assert.deepEqual(plain(pendingRaidsState.stagedPromotions), [{id: 8, track_uri: 'c'}]);
    assert.equal(state.sourceRecovery.length, 1);
    assert.equal(calls.filter(c => c.options.method === 'POST').length, 1);
    assert.equal(element('workshop-save-state').textContent, 'Attention');
    replies.push(response(observed(['b', 'a'])));
    await context.checkCurrentSpotify();
    assert.equal(state.sourceRecovery.length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_failed_source_after_target_continues_independent_tasks_and_retries_only_source(
    node,
):
    run_node(
        node,
        UI
        + """
(async () => {
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}]; state.archiveQueue = ['a'];
    replies.push(savedTarget(), failedSource('collection'), completeSource('archive'));
    await saveThroughReview();
    assert.deepEqual(plain(state.savedUris), ['b', 'a']);
    assert.equal(state.sourceRecovery.length, 1);
    assert.equal(calls.length, 3);
    assert.equal(state.saveResults[0].status, 'failed');
    assert.equal(state.saveResults[1].status, 'complete');
    assert.equal(element('workshop-save-state').textContent, 'Attention');
    assert.equal(context.canSaveWorkshop(), false);
    assert.match(element('workshop-review-summary').textContent, /saved and verified/);
    const task = state.sourceRecovery[0];
    state.workingUris.push('new'); state.archiveQueue.push('new');
    sourceContext.raid = {id: 'different', name: 'New collection'};
    await context.openWorkshopResults(); await context.keepReviewingDraft();
    replies.push(completeSource('collection'));
    await context.recoverSourceStep('raids', false);
    assert.equal(calls.filter(c => c.url.endsWith('/commit')).length, 1);
    assert.deepEqual(JSON.parse(calls.at(-1).options.body), {
        track_ids: [7], source_playlist_id: 'collection', target_track_uris: ['b', 'a']});
    assert.equal(task.resourceName, 'Collection');
    assert.equal(state.sourceRecovery.length, 0);
    assert.deepEqual(plain(state.archiveQueue), ['new']);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_unknown_append_never_replays_and_inspection_is_read_only(node):
    run_node(
        node,
        UI
        + """
(async () => {
    state.archiveQueue = ['a', 'a'];
    replies.push(savedTarget(), () => {throw new Error('append succeeded; response lost');});
    await saveThroughReview();
    const task = state.sourceRecovery[0];
    assert.equal(task.status, 'unknown'); assert.equal(task.retryable, false);
    await context.recoverSourceStep('archive', false);
    assert.equal(calls.length, 2);
    replies.push(failedSource('archive'));
    await context.recoverSourceStep('archive', true);
    assert.equal(JSON.parse(calls.at(-1).options.body).inspect_only, true);
    assert.equal(task.uncertainAppend, true); assert.equal(task.retryable, false);
    assert.equal(task.status, 'unknown');
    await context.recoverSourceStep('archive', false);
    assert.equal(calls.length, 3);
    replies.push(completeSource('archive'));
    await context.recoverSourceStep('archive', true);
    assert.equal(state.sourceRecovery.length, 0);
    assert.equal(calls.filter(c => c.url.endsWith('/commit')).length, 1);
    assert.equal(calls.filter(c => c.url.endsWith('/pair/archive') &&
        !JSON.parse(c.options.body).inspect_only).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize(
    "source_result",
    [
        "response({success: true})",
        "response({success: true, status: 'complete', resource: 'wrong'})",
        "response({success: true, status: 'complete', resource: 'archive'}, 502)",
    ],
)
def test_source_success_requires_http_status_verified_outcome_and_captured_identity(
    node, source_result
):
    run_node(
        node,
        UI
        + f"""
(async () => {{
    state.archiveQueue = ['a']; replies.push(savedTarget(), {source_result});
    await saveThroughReview();
    assert.equal(state.sourceRecovery[0].status, 'unknown');
    assert.equal(state.sourceRecovery[0].retryable, false);
    assert.equal(element('workshop-save-state').textContent, 'Attention');
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_restore_failure_defers_dependent_append_with_adjusted_immutable_baseline(node):
    run_node(
        node,
        UI
        + """
(async () => {
    state.stagedRestorations = [{uri: 'b'}]; state.archiveQueue = ['a', 'a'];
    replies.push(savedTarget(), failedSource('archive'));
    await saveThroughReview();
    assert.equal(calls.length, 2);
    const append = state.sourceRecovery[1];
    assert.equal(append.status, 'unattempted'); assert.equal(context.sourceCanRetry(append), false);
    replies.push(completeSource('archive'));
    await context.recoverSourceStep('restorations', false);
    assert.equal(calls.length, 3);
    assert.equal(context.sourceCanRetry(append), true);
    replies.push(completeSource('archive'));
    await context.recoverSourceStep('archive', false);
    assert.deepEqual(JSON.parse(calls.at(-1).options.body).baseline_track_uris, ['old']);
    assert.deepEqual(JSON.parse(calls.at(-1).options.body).track_uris, ['a', 'a']);
    assert.equal(state.sourceRecovery.length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_raid_database_partial_result_allows_scoped_retry(node):
    run_node(
        node,
        UI
        + """
(async () => {
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}];
    replies.push(savedTarget(), response({success: false, resource: 'collection', status: 'unknown',
        source_status: 'complete', database_status: 'failed',
        error_code: 'database_update_failed', retryable: true}, 502));
    await saveThroughReview();
    assert.equal(context.sourceCanRetry(state.sourceRecovery[0]), true);
    replies.push(completeSource('collection'));
    await context.recoverSourceStep('raids', false);
    assert.equal(state.sourceRecovery.length, 0);
    assert.equal(calls.filter(c => c.url.endsWith('/commit')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_source_only_staging_is_draft_and_discard_is_local_preserving_recovery(node):
    run_node(
        node,
        UI
        + """
(async () => {
    state.workingUris = state.savedUris.slice();
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}]; context.markDirty();
    assert.equal(state.isDirty, true);
    assert.equal(element('workshop-save-state').textContent, 'Draft');
    assert.equal(context.canSaveWorkshop(), true);
    replies.push(savedTarget(), failedSource('collection')); await saveThroughReview();
    const recovery = state.sourceRecovery[0];
    pendingRaidsState.stagedPromotions = [{id: 8, track_uri: 'new'}];
    state.archiveQueue = ['new']; state.workingUris.push('new');
    const start = template.indexOf('function undoPreview()');
    const end = template.indexOf('async function toggleWorkshopVisibility()', start);
    vm.runInContext(template.slice(start, end), context);
    context.undoPreview();
    assert.equal(calls.length, 2);
    assert.deepEqual(plain(state.workingUris), ['a', 'b']);
    assert.deepEqual(plain(state.archiveQueue), []);
    assert.equal(state.sourceRecovery[0], recovery);
    assert.equal(element('workshop-save-state').textContent, 'Attention');
    assert.equal(calls.some(c => c.url.includes('unpromote')), false);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_abort_is_unknown_and_current_check_is_read_only(node):
    run_node(
        node,
        CLIENT
        + """
(async () => {
    let timeout;
    context.setTimeout = callback => {timeout = callback; return 1;};
    context.clearTimeout = () => {};
    window.fetch = (url, options) => {
        calls.push({url, options});
        return new Promise((resolve, reject) => options.signal.addEventListener('abort',
            () => reject(new Error('aborted'))));
    };
    const saving = client.submit({baselineTrackUris: ['a'], trackUris: ['b']});
    timeout();
    const result = await saving;
    assert.equal(result.target_status, 'unknown');
    assert.equal(result.retryable, false);
    assert.equal(calls.length, 1);
    const checking = client.readCurrent(); timeout();
    assert.equal((await checking).error_code, 'upstream_read_failed');
    assert.equal(calls[1].options.method, 'GET');
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_partial_adoption_retains_uncertain_source_intent_and_comparison_updates(node):
    run_node(
        node,
        UI
        + """
(async () => {
    state.archiveQueue = ['a', 'a'];
    replies.push(response({success: false, target_status: 'partial', error_code: 'partial_write',
        retryable: false, message: 'Part of the playlist may have changed.'}, 502));
    await saveThroughReview();
    replies.push(response(observed(['partial'])));
    await context.checkCurrentSpotify();
    state.workingUris = ['a', 'b', 'a']; context.markDirty();
    assert.deepEqual(element('workshop-draft-order').children.map(li => li.textContent),
        ['1. A', '2. B', '3. A']);
    await context.keepReviewingDraft(); context.useCurrentSpotify();
    assert.equal(calls.length, 2); // One target request, then one read.
    assert.equal(state.sourceRecovery.length, 1);
    assert.deepEqual(plain(state.sourceRecovery[0].body.track_uris), ['a', 'a']);
    assert.equal(element('workshop-save-state').textContent, 'Attention');
    assert.equal(element('workshop-save-message').textContent.includes('was saved'), false);
    assert.equal(element('workshop-review-heading').focused, true);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_pending_live_action_body_blocks_save_until_settled(node):
    run_node(
        node,
        UI
        + """
(async () => {
    let finishBody;
    replies.push({ok: true, clone: () => ({arrayBuffer: () => new Promise(resolve => {finishBody = resolve;})})});
    const live = context.workshopRequest('/api/playlist/playlist/drip-now', {method: 'POST'});
    await Promise.resolve(); await Promise.resolve();
    assert.equal(state.pendingMutations, 1);
    await context.commitToSpotify();
    assert.equal(calls.length, 1);
    finishBody(new ArrayBuffer(0)); await live;
    assert.equal(state.pendingMutations, 0);
    assert.equal(context.canSaveWorkshop(), true);
    replies.push(() => {throw new Error('failed live action');});
    await assert.rejects(context.workshopRequest('/live', {method: 'POST'}));
    assert.equal(state.pendingMutations, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_a_whole_new_order_keeps_rows_by_key_and_numbers_them_once(node):
    run_node(
        node,
        UI
        + """
// Rows as the page builds them, with the parts renumberTracks writes; each build is counted.
let built = 0;
context.trackRowData = new WeakMap();
context.createTrackElement = (track, key) => {
    built += 1;
    const row = {key, dataset: {occurrence: key, uri: track.uri}, number: {textContent: ''},
        actions: {setAttribute(name, value) {this[name] = value;}},
        querySelector(selector) {return selector === '.track-number' ? this.number : this.actions;}};
    context.trackRowData.set(row, track);
    return row;
};
// Rows apply their own locks when built, and kept rows keep theirs.
context.applyAllLockStates = () => { throw new Error('locks applied to every row again'); };
// The row actions are a disclosure: the shared helper comes from the page's first script.
const disclosure = template.indexOf('function createDisclosure(');
vm.runInContext(template.slice(disclosure, template.indexOf('</script>', disclosure)), context);
const start = template.indexOf('function fullRerenderTrackList()');
const end = template.indexOf('// Search Spotify', start);
vm.runInContext(template.slice(start, end), context);
const numbering = template.indexOf('function renumberTracks(');
vm.runInContext(template.slice(numbering, template.indexOf('function markDirty()', numbering)), context);
const renumber = context.renumberTracks;
let renumbers = 0;
context.renumberTracks = (...range) => { renumbers += 1; return renumber(...range); };
const rows = () => element('track-list').children;
const positions = () => rows().map(row => [row.number.textContent, row.actions['aria-label']]);
Object.entries(trackDataByUri).forEach(([uri, track]) => { track.uri = uri; });
state.workingUris = ['a', 'a', 'new'];
context.fullRerenderTrackList();
assert.deepEqual(positions(), [[1, 'Actions for A, position 1'], [2, 'Actions for A, position 2'],
    [3, 'Actions for Track new, position 3']]);
assert.equal(built, 3); assert.equal(renumbers, 1);
// Each row is its own occurrence, repeated songs included.
const [firstA, secondA, added] = rows();
assert.equal(new Set([firstA, secondA, added].map(row => row.key)).size, 3);
assert.deepEqual(plain(context.PlaylistWorkspace.uris(state.occurrences)), ['a', 'a', 'new']);
assert.equal(trackDataByUri.new.uri, 'new');
assert.equal(trackDataByUri.new.name, 'Track new');
// A new order keeps each track's keys in order (remap), and their rows: the rows shown are put in
// the new order, a row whose key left goes, nothing is built, and the rows are numbered once.
state.workingUris = ['new', 'a'];
context.fullRerenderTrackList();
assert.deepEqual(rows(), [added, firstA]); assert.equal(built, 3); assert.equal(renumbers, 2);
assert.deepEqual(positions(), [[1, 'Actions for Track new, position 1'], [2, 'Actions for A, position 2']]);
// Only a new key gets a row, and so does a track whose data a fresh read replaced.
trackDataByUri.a = {uri: 'a', name: 'A, as Spotify has it now'};
state.workingUris = ['a', 'new', 'b'];
context.fullRerenderTrackList();
assert.equal(rows()[1], added); assert.notEqual(rows()[0], firstA); assert.equal(rows()[0].key, firstA.key);
assert.equal(built, 5); assert.equal(renumbers, 3);
assert.deepEqual(positions(), [[1, 'Actions for A, as Spotify has it now, position 1'],
    [2, 'Actions for Track new, position 2'], [3, 'Actions for B, position 3']]);
""",
    )


def test_rebuilt_tracks_use_placeholder_for_rejected_artwork(node):
    run_node(
        node,
        UI
        + """
context.document.createElement = () => ({dataset: {}, querySelector() {return null;}});
context.escapeHtml = context.escapeAttr = value => String(value);
context.applyLockStateToElement = () => {};
const safeStart = template.indexOf('function safeUrl(');
vm.runInContext(template.slice(safeStart, template.indexOf('</script>', safeStart)), context);
const rowStart = template.indexOf('const trackRowData = new WeakMap();');
const rowEnd = template.indexOf('// External Playlist Loading', rowStart);
vm.runInContext(template.slice(rowStart, rowEnd), context);
for (const url of ['', 'data:image/svg+xml;base64,PHN2Zz4=', 'javascript:alert(1)']) {
    const row = context.createTrackElement({uri: 'a', name: 'A', album_image_url: url}, 'o1');
    assert.equal(row.innerHTML.includes('<img'), false);
}
const cover = 'https://example.com/cover.png';
const row = context.createTrackElement({uri: 'a', name: 'A', album_image_url: cover}, 'o1');
assert.equal(row.innerHTML.includes('<img src="https://example.com/cover.png"'), true);
""",
    )


def test_source_context_freezes_capture_before_await_and_current_draft_change_blocks_save(
    node,
):
    run_node(
        node,
        UI
        + """
(async () => {
    let finish;
    window.fetch = (url, options) => new Promise(resolve => {finish = resolve;});
    state.archiveQueue = ['a', 'a'];
    const reviewing = context.commitToSpotify();
    state.archiveQueue.push('new'); state.workingUris.push('new');
    finish(response(sourceContext)); await reviewing;
    assert.deepEqual(plain(state.review.desired), ['b', 'a']);
    assert.deepEqual(plain(state.review.sources[0].body.track_uris), ['a', 'a']);
    await context.saveReviewedWorkshop();
    assert.equal(calls.length, 0);
    assert.match(element('workshop-review-message').textContent, /changed/);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_review_entry_returns_the_opened_review_and_both_openers_share_one_close_handler(
    node,
):
    run_node(
        node,
        UI
        + """
(async () => {
    const review = await context.commitToSpotify();
    assert.equal(review, state.review); assert.equal(state.reviewMode, 'review');
    const reviewOptions = context.panelOpen.options;
    assert.equal(reviewOptions.modal, false); assert.equal(typeof reviewOptions.onClose, 'function');
    // AppPanels keeps the first opener's hooks while the panel stays open, so both openers pass the
    // same ones, each with its own trigger: here nothing has focus, so the save status.
    assert.equal(reviewOptions.trigger, element('workshop-save-status'));
    await context.openWorkshopResults();
    for (const hook of ['modal', 'beforeClose', 'onClose']) {
        assert.equal(context.panelOpen.options[hook], reviewOptions[hook], hook);
    }
    assert.equal(context.panelOpen.options.trigger, element('workshop-save-status'));
    // Nothing opens, and nothing is returned, while Save is unavailable or its context read fails.
    state.isShuffling = true; assert.equal(await context.commitToSpotify(), null); state.isShuffling = false;
    window.fetch = async () => response({success: false, message: 'Archive unavailable'}, 502);
    state.archiveQueue = ['a']; assert.equal(await context.commitToSpotify(), null);
    assert.equal(calls.length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_failed_context_read_never_opens_review_or_writes(node):
    run_node(
        node,
        UI
        + """
(async () => {
    window.fetch = async () => response({success: false, message: 'Archive unavailable'}, 502);
    state.archiveQueue = ['a']; await context.commitToSpotify();
    assert.equal(state.review, null); assert.equal(state.isPreparing, false);
    assert.equal(calls.length, 0);
    assert.equal(notices.at(-1).message, 'Archive unavailable');
    assert.deepEqual(plain(state.archiveQueue), ['a']);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_unknown_removal_failed_inspection_stays_unknown_then_precise_db_pending_can_retry(
    node,
):
    run_node(
        node,
        UI
        + """
(async () => {
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}];
    replies.push(savedTarget(), () => {throw new Error('response lost');});
    await saveThroughReview(); const task = state.sourceRecovery[0];
    replies.push(failedSource('collection'));
    await context.recoverSourceStep('raids', true);
    assert.equal(task.status, 'unknown'); assert.equal(context.sourceCanRetry(task), false);
    replies.push(response({success: false, resource: 'collection', status: 'unknown',
        source_status: 'complete', database_status: 'pending',
        error_code: 'database_update_pending', retryable: true}, 502));
    await context.recoverSourceStep('raids', true);
    assert.equal(context.sourceCanRetry(task), true);
    assert.equal(calls.filter(c => c.url.endsWith('/commit')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_verified_append_refreshes_inbox_and_captured_identity_is_read_only(node):
    run_node(
        node,
        UI
        + """
(async () => {
    state.archiveQueue = ['a']; context.archiveInboxLoaded = true;
    let refreshed = 0;
    context.fetchArchiveTracks = async force => {
        assert.equal(force, true); assert.equal(context.archiveInboxLoaded, false);
        assert.equal(state.sourceRecovery.length, 0); refreshed++;
    };
    replies.push(savedTarget(), completeSource('archive')); await saveThroughReview();
    assert.equal(refreshed, 1); assert.deepEqual(plain(state.archiveQueue), []);
    assert.equal(element('workshop-save-state').textContent, 'Ready');
    const task = state.saveResults[0];
    task.resource = 'wrong'; task.resourceName = 'Wrong';
    assert.equal(task.resource, 'archive'); assert.equal(task.resourceName, 'Archive');
    assert.equal(Object.isFrozen(task.body), true);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_inconclusive_inspection_preserves_known_prewrite_failure_and_safe_retry(node):
    run_node(
        node,
        UI
        + """
(async () => {
    state.archiveQueue = ['a', 'a'];
    replies.push(savedTarget(), failedSource('archive')); await saveThroughReview();
    const task = state.sourceRecovery[0];
    assert.equal(context.sourceCanRetry(task), true);
    replies.push(response({success: false, resource: 'archive', status: 'unknown',
        error_code: 'upstream_read_failed', retryable: false, message: 'Cannot read archive'}, 502));
    await context.recoverSourceStep('archive', true);
    assert.equal(task.status, 'failed');
    assert.equal(Boolean(task.uncertainAppend), false);
    assert.equal(context.sourceCanRetry(task), true);
    assert.equal(task.inspectionResult.message, 'Cannot read archive');
    replies.push(completeSource('archive'));
    await context.recoverSourceStep('archive', false);
    assert.equal(state.sourceRecovery.length, 0);
    assert.equal(calls.filter(c => c.url.endsWith('/commit')).length, 1);
    assert.deepEqual(JSON.parse(calls.at(-1).options.body).track_uris, ['a', 'a']);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_discard_after_uncertain_target_preserves_later_identical_source_staging(node):
    run_node(
        node,
        UI
        + """
(async () => {
    state.archiveQueue = ['a'];
    replies.push(() => {throw new Error('target response lost');}); await saveThroughReview();
    const start = template.indexOf('function undoPreview()');
    const end = template.indexOf('async function toggleWorkshopVisibility()', start);
    vm.runInContext(template.slice(start, end), context);
    context.undoPreview();
    assert.equal(state.sourceRecovery.length, 1);
    state.archiveQueue = ['a']; state.workingUris = ['a', 'b', 'a'];
    replies.push(response(observed(['b', 'a']))); await context.checkCurrentSpotify();
    assert.deepEqual(plain(state.archiveQueue), ['a']);
    assert.equal(state.sourceRecovery[0].targetConfirmed, true);
    assert.equal(calls.filter(c => c.options.method === 'POST').length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize("restore", [False, True])
def test_shared_collection_archive_removals_adjust_append_baseline(node, restore):
    run_node(
        node,
        UI
        + f"""
(async () => {{
    sourceContext.raid = {{id: 'archive', name: 'Shared collection and archive'}};
    sourceContext.archive.baseline_track_uris = ['b', 'old', 'restore', 'b', 'old', 'restore', 'keep'];
    pendingRaidsState.stagedPromotions = [{{id: 7, track_uri: 'b'}}];
    state.archiveQueue = ['out', 'out'];
    if ({str(restore).lower()}) {{
        state.workingUris.push('restore'); state.stagedRestorations = [{{uri: 'restore'}}];
    }}
    await context.commitToSpotify();
    const append = state.review.sources.at(-1);
    assert.deepEqual(plain(append.dependsOn), {"['raids', 'restorations']" if restore else "['raids']"});
    assert.deepEqual(plain(append.body.baseline_track_uris),
        {"['old', 'old', 'keep']" if restore else "['old', 'restore', 'old', 'restore', 'keep']"});
    assert.deepEqual(plain(state.review.sources[0].removalUris), ['b']);
    assert.equal(Object.isFrozen(state.review.sources[0].removalUris), true);
    replies.push(savedTarget(), completeSource('archive'));
    if ({str(restore).lower()}) replies.push(completeSource('archive'));
    replies.push(completeSource('archive'));
    await context.saveReviewedWorkshop();
    assert.equal(state.sourceRecovery.length, 0);
    const raidBody = JSON.parse(calls[1].options.body);
    assert.deepEqual(Object.keys(raidBody).sort(), ['source_playlist_id', 'target_track_uris', 'track_ids']);
    assert.deepEqual(JSON.parse(calls.at(-1).options.body).baseline_track_uris,
        {"['old', 'old', 'keep']" if restore else "['old', 'restore', 'old', 'restore', 'keep']"});
    assert.deepEqual(JSON.parse(calls.at(-1).options.body).track_uris, ['out', 'out']);
    assert.equal(calls.filter(call => call.url.endsWith('/commit')).length, 1);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_shared_source_raid_rejection_blocks_append_after_restoration_completes(node):
    run_node(
        node,
        UI
        + """
(async () => {
    sourceContext.raid = {id: 'archive', name: 'Shared collection and archive'};
    sourceContext.archive.baseline_track_uris = ['b', 'b', 'restore', 'old', 'restore'];
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}];
    state.stagedRestorations = [{uri: 'restore'}]; state.workingUris.push('restore');
    state.archiveQueue = ['out', 'out'];
    replies.push(savedTarget(), response({success: false, resource: 'archive', status: 'failed',
        retryable: true, error_code: 'write_rejected', message: 'Source write rejected'}, 502),
        completeSource('archive'));
    await saveThroughReview();
    assert.equal(calls.length, 3);
    assert.equal(state.saveResults[1].status, 'complete');
    const append = state.saveResults[2];
    assert.equal(append.status, 'unattempted');
    assert.equal(context.sourceCanRetry(append), false);
    await context.recoverSourceStep('archive', false);
    assert.equal(calls.length, 3);
    replies.push(completeSource('archive'));
    await context.recoverSourceStep('raids', false);
    assert.equal(context.sourceCanRetry(append), true);
    replies.push(completeSource('archive'));
    await context.recoverSourceStep('archive', false);
    assert.deepEqual(JSON.parse(calls.at(-1).options.body).baseline_track_uris, ['old']);
    assert.equal(state.sourceRecovery.length, 0);
    assert.equal(calls.filter(call => call.url.endsWith('/commit')).length, 1);
    assert.equal(calls.filter(call => call.url.endsWith('/pair/archive')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_distinct_collection_leaves_archive_baseline_and_dependencies_unchanged(node):
    run_node(
        node,
        UI
        + """
(async () => {
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}]; state.archiveQueue = ['out'];
    await context.commitToSpotify();
    const append = state.review.sources[1];
    assert.deepEqual(plain(append.dependsOn), []);
    assert.deepEqual(plain(append.body.baseline_track_uris), ['old', 'b', 'b']);
    replies.push(savedTarget(), failedSource('collection'), completeSource('archive'));
    await context.saveReviewedWorkshop();
    assert.equal(state.saveResults[1].status, 'complete');
    assert.equal(state.sourceRecovery.length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_staged_existing_raid_returns_to_the_inbox_and_missing_staging_blocks_review_without_writes(node):
    run_node(
        node,
        UI
        + """
(async () => {
    const track = {id: 7, track_uri: 'b'};
    pendingRaidsState.tracks = [track];
    const promoteStart = template.indexOf('function promotePendingRaid(');
    const promoteEnd = template.indexOf('function dismissPendingRaid(', promoteStart);
    vm.runInContext(template.slice(promoteStart, promoteEnd), context);
    context.promotePendingRaid(7, track);
    // The staged track's row is removed by its occurrence key.
    Object.assign(context, {renumberTracks() {}, closeTrackActions() {}, trackRow: () => null, trackTitle: uri => uri});
    element('track-list').querySelector = () => null;
    const removeStart = template.indexOf('function removeFromDraft(');
    vm.runInContext(template.slice(removeStart, template.indexOf('function updateTrackCount(', removeStart)), context);
    assert.equal(context.removeFromDraft(context.draftOccurrences().find(item => item.uri === 'b').key), true);
    assert.deepEqual(plain(state.workingUris), ['a']);
    // Its last copy gone, the staged track goes back to the inbox.
    assert.equal(pendingRaidsState.stagedPromotions.length, 0);
    assert.deepEqual(plain(pendingRaidsState.tracks), [{id: 7, track_uri: 'b'}]);
    // Staging whose track is missing from the draft still never reaches a review or a write.
    pendingRaidsState.stagedPromotions = [track];
    await context.commitToSpotify(); await context.saveReviewedWorkshop();
    assert.equal(state.review, null);
    assert.equal(calls.length, 0); assert.equal(contextReads.length, 0);
    assert.match(notices.at(-1).message, /staged.*missing|missing.*staged/i);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_missing_staged_restoration_blocks_review_and_preserves_staging(node):
    run_node(
        node,
        UI
        + """
(async () => {
    state.stagedRestorations = [{uri: 'restored'}];
    state.workingUris.push('restored');
    state.workingUris = state.workingUris.filter(uri => uri !== 'restored');
    await context.commitToSpotify(); await context.saveReviewedWorkshop();
    assert.equal(state.review, null);
    assert.equal(calls.length, 0); assert.equal(contextReads.length, 0);
    assert.deepEqual(plain(state.stagedRestorations), [{uri: 'restored'}]);
    assert.match(notices.at(-1).message, /staged.*missing|missing.*staged/i);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_changed_raid_uri_metadata_invalidates_review_even_when_ids_are_unchanged(node):
    run_node(
        node,
        UI
        + """
(async () => {
    pendingRaidsState.stagedPromotions = [{id: 7, track_uri: 'b'}];
    await context.commitToSpotify();
    pendingRaidsState.stagedPromotions[0].track_uri = 'a';
    await context.saveReviewedWorkshop();
    assert.equal(calls.length, 0);
    assert.deepEqual(plain(state.review.sources[0].removalUris), ['b']);
    assert.match(element('workshop-review-message').textContent, /changed/);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize("kind", ["archive", "restorations", "raids"])
def test_inspected_noop_verifies_prior_unknown_without_claiming_no_original_change(
    node, kind
):
    run_node(
        node,
        UI
        + f"""
(async () => {{
    const kind = '{kind}';
    if (kind === 'archive') state.archiveQueue = ['a', 'a'];
    if (kind === 'restorations') state.stagedRestorations = [{{uri: 'b'}}];
    if (kind === 'raids') pendingRaidsState.stagedPromotions = [{{id: 7, track_uri: 'b'}}];
    const resource = kind === 'raids' ? 'collection' : 'archive';
    replies.push(savedTarget(), () => {{throw new Error('effect applied; response lost');}});
    await saveThroughReview();
    const task = state.sourceRecovery[0]; assert.equal(task.status, 'unknown');
    replies.push(response({{success: true, status: 'not_required', resource, affected_count: 0,
        retryable: false, message: 'Current contents verified.'}}));
    await context.recoverSourceStep(kind, true);
    assert.equal(task.status, 'not_required'); assert.equal(task.result.affected_count, 0);
    assert.equal(task.verifiedByInspection, true);
    assert.equal(state.sourceRecovery.length, 0);
    const row = element('workshop-source-results').children.find(item => item.id === 'workshop-source-' + kind);
    const rendered = row.children.map(item => item.textContent).join(' ');
    assert.match(rendered, /Complete — verified after checking current contents/);
    assert.equal(rendered.includes('no change needed'), false);
    assert.equal(JSON.parse(calls.at(-1).options.body).inspect_only, true);
    assert.equal(calls.length, 3);
    assert.equal(calls.filter(call => call.url.endsWith('/commit')).length, 1);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_csrf_rejected_append_stays_failed_without_uncertainty_or_automatic_reload(
    node,
):
    run_node(
        node,
        UI
        + """
(async () => {
    window.location = {reload() {throw new Error('Recovery must not reload the page');}};
    state.archiveQueue = ['a', 'a'];
    const message = 'Session verification expired. This attempt applied no source changes. ' +
        'Keep this page open: reloading loses recovery details while earlier Spotify changes remain.';
    replies.push(savedTarget(), response({success: false, resource: 'archive', status: 'failed',
        retryable: false, affected_count: 0, error_code: 'session_verification_expired', message}, 400));
    await saveThroughReview(); const task = state.sourceRecovery[0];
    assert.equal(task.status, 'failed'); assert.equal(task.retryable, false);
    assert.equal(Boolean(task.uncertainAppend), false);
    assert.equal(task.result.error_code, 'session_verification_expired');
    assert.deepEqual(plain(state.savedUris), ['b', 'a']);
    assert.deepEqual(plain(task.body.track_uris), ['a', 'a']);
    const row = element('workshop-source-results').children.find(item => item.id === 'workshop-source-archive');
    const rendered = row.children.map(item => item.textContent).join(' ');
    assert.match(rendered, /Failed — no change applied/);
    assert.equal(rendered.includes(message), true);
    assert.equal(row.children.some(item => item['data-source-retry'] === 'archive'), false);
    assert.equal(row.children.some(item => item.href === 'https://open.spotify.com/playlist/archive'), true);
    await context.recoverSourceStep('archive', false);
    assert.equal(calls.length, 2); assert.equal(state.sourceRecovery.length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_csrf_rejected_inspection_preserves_earlier_append_uncertainty_and_details(
    node,
):
    run_node(
        node,
        UI
        + """
(async () => {
    window.location = {reload() {throw new Error('Recovery must not reload the page');}};
    state.archiveQueue = ['a', 'a'];
    replies.push(savedTarget(), () => {throw new Error('append acknowledgement lost');});
    await saveThroughReview(); const task = state.sourceRecovery[0];
    const previousResult = task.result;
    const message = 'Session verification expired. The inspection did not run; ' +
        'the earlier effect remains unverified. Keep this page open to retain recovery details.';
    replies.push(response({success: false, resource: 'archive', status: 'unknown',
        retryable: false, error_code: 'session_verification_expired', message}, 400));
    await context.recoverSourceStep('archive', true);
    assert.equal(task.status, 'unknown'); assert.equal(task.uncertainAppend, true);
    assert.equal(task.retryable, false); assert.equal(task.result, previousResult);
    assert.equal(task.inspectionResult.error_code, 'session_verification_expired');
    const row = element('workshop-source-results').children.find(item => item.id === 'workshop-source-archive');
    const rendered = row.children.map(item => item.textContent).join(' ');
    assert.match(rendered, /Unknown — effect not established/);
    assert.equal(rendered.includes(message), true);
    assert.equal(row.children.some(item => item['data-source-retry'] === 'archive'), false);
    await context.recoverSourceStep('archive', false);
    assert.equal(calls.length, 3); assert.equal(state.sourceRecovery[0], task);
    assert.equal(JSON.parse(calls.at(-1).options.body).inspect_only, true);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )
