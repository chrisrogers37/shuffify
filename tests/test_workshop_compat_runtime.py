"""Workshop compatibility with running work.

Which Workshop controls a running live action, Save, staged or recovering source work, or an
unresolved live result makes unavailable, and why; the shared confirmations that replace the
native dialogs; and the refresh paths that never overwrite or hide work on the page.

The scenarios run the shipped controller and the Workshop page's own functions (sliced from
the template) under Node, on the same DOM and network stubs as the live-action suite.
"""

import re

import pytest

from tests.page_runtime import page_tree, render_page, run_page
from tests.test_playlist_operations_runtime import ROOT, run_node
from tests.test_playlist_operations_runtime import node as node
from tests.test_workshop_live_runtime import WORKSHOP

TEMPLATE = ROOT / "shuffify/templates/workshop.html"
TOOLS = ROOT / "shuffify/templates/partials/workspace_tools.html"
INCLUDE = '{% include "partials/workspace_tools.html" %}'


def workshop_markup():
    """The Workshop template with its tool panels' partial inlined where it is included."""
    text = TEMPLATE.read_text()
    assert text.count(INCLUDE) == 1, "workshop.html includes the tool panels once"
    return text.replace(INCLUDE, TOOLS.read_text())


COMPAT = (
    WORKSHOP
    + r"""
// A scenario that fails inside a held run must not wait out the controller's five-minute
// run timer: long timers do not keep Node alive (a passing scenario clears them anyway).
const plainSetTimeout = context.setTimeout;
context.setTimeout = (callback, ms, ...args) => {
    const timer = plainSetTimeout(callback, ms, ...args);
    if (ms >= 10000) timer.unref();
    return timer;
};
// A grouped control as the page renders one; events on it reach the capture barrier.
function grouped(id, group, extra = {}) {
    const el = element(id);
    Object.assign(el.dataset, {workshopCompat: group}, extra);
    el.closest = selector => (selector === '[data-workshop-compat]' ? el : null);
    return el;
}
const barrier = type => listeners[type].find(listener => String(listener).includes('data-workshop-compat'));
// True when the capture barrier refuses an event on this control.
function barred(el, type = 'click') {
    let prevented = false;
    barrier(type)({target: el, key: 'Enter', preventDefault() {prevented = true;}, stopImmediatePropagation() {}});
    return prevented;
}
const blockedKeys = () => [...context.workshopBlockedGroups().keys()].sort();
const reasonOf = key => context.workshopBlockedReason(key);
const lastNotice = () => notices[notices.length - 1];
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };
// Hold a reviewed run open, its reply pending, to observe the page while it runs.
async function holdRun(ctx, start) {
    let finish;
    replies.push(replyWith(ctx), () => new Promise(resolve => {finish = resolve;}));
    const running = start(); await tick(); await tick(); runReview(); await tick();
    assert.ok(state.liveRun, 'the run is Busy');
    return {running, end: (...afterwards) => {
        replies.push(...afterwards); finish(response(verified(ctx))); return running;
    }};
}
// A reviewed run whose reply is lost leaves the playlists it writes unresolved.
async function lose(ctx, start) {
    replies.push(replyWith(ctx), () => {throw new Error('response lost');});
    const running = start(); await tick(); await tick(); runReview();
    return running;
}
const GROUPS = ['draft', 'locks', 'link', 'drip-gate', 'drip-count', 'sources', 'pair', 'pair-unlink', 'dismiss',
    'create', 'view', 'pause', 'save', 'auto-archive'];
// One control per group, plus controls whose rules name a routine, a snapshot or the collection.
function controlsForEveryGroup() {
    const controls = {};
    GROUPS.forEach(group => { controls[group] = grouped('control-' + group, group); });
    [5, 6, 7].forEach(id => { controls['routine:' + id] = grouped('control-routine-' + id, 'routine',
        {scheduleId: String(id)}); });
    controls['unlink:collection'] = grouped('control-unlink', 'link', {collectionId: 'collection'});
    controls['snapshot:17'] = grouped('control-snapshot-17', 'create', {snapId: '17'});
    controls['snapshot:18'] = grouped('control-snapshot-18', 'create', {snapId: '18'});
    return controls;
}
"""
)


def scenario(body, **values):
    """Fill __NAME__ placeholders, keeping JavaScript braces literal."""
    for name, value in values.items():
        body = body.replace(f"__{name.upper()}__", value)
    return COMPAT + body


# Routine cards as stubs whose controls are found by class, like the template's card markup.
# The list holds the cards shown: a full rebuild clears it; a kept card stays in place.


RUNS = r"""
const RUNS = {
    collect: {ctx: () => liveContext('raid'), start: () => raidPanel.raidNow(), fresh: false,
        blocked: ['dismiss', 'link', 'pair', 'pair-unlink', 'sources'],
        group: 'sources', reason: 'Unavailable while collecting tracks for review runs'},
    drip: {ctx: () => liveContext('drip'), start: () => raidPanel.dripNow(), fresh: true,
        blocked: ['dismiss', 'draft', 'drip-count', 'link', 'locks'],
        group: 'locks', reason: 'Unavailable while adding collected tracks runs'},
    raid_and_drip: {ctx: () => liveContext('raid_and_drip'), start: () => raidPanel.raidAndDrip(), fresh: true,
        blocked: ['dismiss', 'draft', 'drip-count', 'link', 'locks', 'pair', 'pair-unlink', 'sources'],
        group: 'pair', reason: 'Unavailable while collecting and adding tracks runs'},
    raid_routine: {ctx: () => routineContext(5, 'raid'), fresh: false,
        start: () => {raidPanel.status.schedule = {id: 5, job_type: 'raid'}; return startExactRoutine(5);},
        blocked: ['dismiss', 'link', 'pair', 'pair-unlink', 'routine:5', 'sources'],
        group: 'link', reason: 'Unavailable while the collect routine runs'},
    collect_and_add_routine: {ctx: () => routineContext(5, 'raid_and_drip'), start: () => startExactRoutine(5),
        fresh: true, blocked: ['dismiss', 'draft', 'drip-count', 'drip-gate', 'link', 'locks', 'pair', 'pair-unlink',
            'routine:5', 'sources'],
        group: 'drip-gate', reason: 'Unavailable while the collect and add routine runs'},
    shuffle_routine: {ctx: () => routineContext(6, 'shuffle'), fresh: true,
        start: () => context.reviewedRun({kind: 'schedule', scheduleId: 6, targetId: 'target',
            trigger: element('run-6')}),
        blocked: ['draft', 'locks', 'routine:6'], group: 'locks', reason: 'Unavailable while the shuffle routine runs'},
    rotate: {ctx: () => routineContext(7, 'rotate'), start: () => startExactRoutine(7), fresh: true,
        blocked: ['draft', 'locks', 'pair', 'pair-unlink', 'routine:7'],
        group: 'pair-unlink', reason: 'Unavailable while the rotation runs'},
    restore: {ctx: () => snapshotContext(17), start: () => context.restoreSnapshot(17, element('snapshot-17')),
        fresh: true, blocked: ['create:17', 'draft'], group: 'draft',
        reason: 'Unavailable while the snapshot restore runs'},
    privacy: {ctx: () => reviewContext('privacy'), fresh: false, blocked: [], group: null, reason: null,
        start: () => {
            element('workshop-visibility-btn').dataset.public = 'true'; return context.toggleWorkshopVisibility();
        }},
};
"""


@pytest.mark.parametrize(
    "kind",
    [
        "collect",
        "drip",
        "raid_and_drip",
        "raid_routine",
        "collect_and_add_routine",
        "shuffle_routine",
        "rotate",
        "restore",
        "privacy",
    ],
)
def test_a_running_action_blocks_only_its_incompatible_groups_and_releases_them(
    node, kind
):
    run_node(
        node,
        scenario(
            RUNS
            + r"""
(async () => {
    const spec = RUNS['__KIND__'];
    const controls = controlsForEveryGroup();
    // A control's own state is kept aside while it is blocked and returns afterwards.
    controls.sources.disabled = true;
    const held = await holdRun(spec.ctx(), spec.start);
    // The page tracks the running intent the controller reports, and Save waits for it.
    assert.equal(state.liveRun.kind, spec.ctx().kind);
    assert.equal(context.workshopBusy(), true);
    assert.deepEqual(blockedKeys(), spec.blocked);
    // Steps come from the reviewed intent, and drag moves read the same draft rule cheaply.
    assert.deepEqual(plain(state.liveRun.steps), spec.ctx().steps || []);
    assert.equal(context.workshopDraftBlocked(), spec.blocked.includes('draft'));
    for (const [name, control] of Object.entries(controls)) {
        const blocked = context.workshopControlKeys(control).some(key => spec.blocked.includes(key));
        assert.equal(barred(control), blocked, name);
        assert.equal(barred(control, 'keydown'), blocked, name);
        if (blocked) {
            assert.equal(control.disabled, true, name);
            assert.match(control.getAttribute('aria-describedby'), /^workshop-compat-reason-/, name);
        } else {
            assert.equal(control.disabled, name === 'sources', name);
            assert.equal(control.getAttribute('aria-describedby'), undefined, name);
        }
    }
    // Tab always passes, so focus can leave a blocked control.
    let tabbed = false;
    barrier('keydown')({target: controls.draft, key: 'Tab', preventDefault() {tabbed = true;},
        stopImmediatePropagation() {}});
    assert.equal(tabbed, false);
    if (spec.reason) {
        assert.equal(reasonOf(spec.group), spec.reason);
        const id = 'workshop-compat-reason-' + spec.group;
        assert.equal(controls[spec.group].getAttribute('aria-describedby'), id);
        assert.equal(element('workshop-compat-reasons').children.find(node => node.id === id).textContent, spec.reason);
    }
    const record = await held.end(...(spec.fresh ? [fresh()] : []));
    assert.equal(record.complete, true);
    assert.equal(state.liveRun, null); assert.equal(context.workshopBusy(), false);
    assert.deepEqual(blockedKeys(), []);
    for (const [name, control] of Object.entries(controls)) {
        assert.equal(control.disabled, name === 'sources', name);
        assert.equal(control.getAttribute('aria-describedby'), undefined, name);
        assert.equal(control.dataset.compatDisabled, undefined, name);
        assert.equal(barred(control), false, name);
    }
    assert.equal(element('workshop-compat-reasons').children.length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
            kind=kind,
        ),
    )


def test_reads_never_wait_while_writes_follow_their_group(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    Object.assign(context, {createSearchResultElement: track => element('result-' + track.uri),
        displayExternalSearchResults() {}, displayExternalTracks() {}, displayRestrictedPlaylist() {}});
    slice('let searchState = {', 'function createSearchResultElement(');
    slice('function loadExternalPlaylist()', 'function loadExternalById(');
    const reads = path => calls.filter(call => call.url === path);
    const results = count => response({success: true,
        tracks: Array.from({length: count}, (_, i) => ({uri: uri(i + 1), name: 'Track ' + i}))});
    // While Drip runs, searching, loading more results and loading an external playlist all read.
    const drip = await holdRun(liveContext('drip'), () => raidPanel.dripNow());
    element('search-input').value = 'night drive';
    replies.push(results(10));
    context.searchSpotify(); await settle();
    assert.equal(reads('/api/workshop/search').length, 1);
    replies.push(results(2));
    context.searchSpotifyMore(); await settle();
    assert.equal(reads('/api/workshop/search').length, 2);
    assert.equal(JSON.parse(reads('/api/workshop/search')[1].options.body).offset, 10);
    element('external-playlist-input').value = 'chill';
    replies.push(response({success: true, mode: 'search', playlists: []}));
    context.loadExternalPlaylist(); await settle();
    assert.equal(reads('/api/workshop/load-external-playlist').length, 1);
    await drip.end(fresh());
    // During a target commit too, while a grouped write waits and a pause write does not.
    state.isSaving = true;
    replies.push(results(1));
    context.searchSpotify(); await settle();
    assert.equal(reads('/api/workshop/search').length, 3);
    await assert.rejects(context.workshopRequest(
        '/api/workshop/target/locks/toggle', {method: 'POST'}, {group: 'locks'}),
        /Unavailable while your draft saves to Spotify/);
    replies.push(response({success: true}));
    assert.equal((await context.workshopRequest(
        '/api/schedules/5/toggle', {method: 'POST'}, {group: 'pause'})).ok, true);
    state.isSaving = false;
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_hot_paths_skip_never_blocked_groups_and_unchanged_reasons(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    const controls = controlsForEveryGroup();
    // The barrier computes nothing for a never-blocked group.
    let computed = 0;
    const plainBlocked = context.workshopBlockedGroups;
    context.workshopBlockedGroups = () => { computed++; return plainBlocked(); };
    for (const group of ['view', 'pause', 'save', 'auto-archive']) assert.equal(barred(controls[group]), false);
    assert.equal(computed, 0);
    barred(controls.draft); assert.equal(computed, 1);
    context.workshopBlockedGroups = plainBlocked;
    // One computation reads the unresolved claims once.
    let claimReads = 0, singleReads = 0;
    const plainClaims = client.unresolvedClaims, plainHas = client.hasUnresolved;
    client.unresolvedClaims = () => { claimReads++; return plainClaims(); };
    client.hasUnresolved = claim => { singleReads++; return plainHas(claim); };
    context.workshopBlockedGroups();
    assert.equal(claimReads, 1); assert.equal(singleReads, 0);
    client.unresolvedClaims = plainClaims; client.hasUnresolved = plainHas;
    // The renderer touches only held controls and controls of a blocked group, and rebuilds
    // the reasons only when they change.
    const touched = [];
    const plainApply = context.applyWorkshopBlock;
    context.applyWorkshopBlock = (control, reason) => { touched.push(control.id); return plainApply(control, reason); };
    const reasons = element('workshop-compat-reasons');
    let rebuilt = 0;
    const plainReplace = reasons.replaceChildren;
    reasons.replaceChildren = function (...nodes) { rebuilt++; return plainReplace.apply(this, nodes); };
    context.renderWorkshopCompat();
    assert.deepEqual(touched, []); assert.equal(rebuilt, 0);
    state.stagedRestorations = [{uri: uri(4)}];
    context.renderWorkshopCompat();
    assert.deepEqual(touched, ['control-pair-unlink']); assert.equal(rebuilt, 1);
    assert.equal(controls['pair-unlink'].disabled, true);
    touched.length = 0;
    context.renderWorkshopCompat();
    assert.deepEqual(touched, ['control-pair-unlink']); assert.equal(rebuilt, 1);
    // Once released, the held control is restored and the reasons emptied.
    state.stagedRestorations = []; touched.length = 0;
    context.renderWorkshopCompat();
    assert.deepEqual(touched, ['control-pair-unlink']); assert.equal(rebuilt, 2);
    assert.equal(controls['pair-unlink'].disabled, false); assert.equal(reasons.children.length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_target_commit_serializes_all_but_view_and_pause_and_finalize_keeps_four_groups(
    node,
):
    run_node(
        node,
        scenario(r"""
(async () => {
    const controls = controlsForEveryGroup();
    state.isSaving = true; context.markDirty();
    const serialized = ['create', 'dismiss', 'draft', 'drip-count', 'drip-gate', 'link', 'locks', 'pair',
        'pair-unlink', 'routine', 'sources'];
    assert.deepEqual(blockedKeys(), serialized);
    for (const [name, control] of Object.entries(controls)) {
        const open = ['view', 'pause', 'save', 'auto-archive'].includes(name);
        assert.equal(control.disabled, !open, name); assert.equal(barred(control), !open, name);
    }
    assert.equal(reasonOf('locks'), 'Unavailable while your draft saves to Spotify');
    assert.equal(context.workshopDraftBlocked(), true);
    assert.equal(reasonOf('routine:7'), 'Unavailable while your draft saves to Spotify');
    // The source finalize allows locks, Drip settings, sources, pause, view and creating;
    // drafts, links, archive unlink and dismissal wait.
    state.isSaving = false; state.isFinalizing = true; context.markDirty();
    assert.deepEqual(blockedKeys(), ['dismiss', 'draft', 'link', 'pair-unlink']);
    const allowed = ['locks', 'drip-gate', 'drip-count', 'sources', 'pause', 'view', 'create', 'pair', 'routine:5'];
    for (const name of allowed) {
        assert.equal(controls[name].disabled, false, name); assert.equal(barred(controls[name]), false, name);
    }
    assert.equal(reasonOf('dismiss'), 'Unavailable while your saved collection and archive changes finish');
    state.isFinalizing = false; context.markDirty();
    assert.deepEqual(blockedKeys(), []);
    assert.equal(Object.values(controls).some(control => control.disabled), false);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


CONDITIONS = r"""
const checked = 'Unavailable until the live action result is checked';
// An open Save review as commitToSpotify leaves it, naming one source step.
const openSaveReview = task => {
    state.review = {baseline: state.savedUris.slice(), desired: state.workingUris.slice(), staging: [],
        target: {id: 'target', name: 'Target name'}, summary: {added: 0, removed: 0, orderChanged: false},
        sources: [Object.assign({resourceName: task.resource + ' name'}, task)]};
    state.reviewMode = 'review';
};
const CONDITIONS = {
    raid_staging: {setup: () => {pendingRaidsState.stagedPromotions = [{id: 1, track_uri: uri(1)}];},
        blocked: ['link'], reasons: {link: 'Save or discard the staged pending tracks first'}},
    raid_review: {setup: () => openSaveReview({kind: 'raids', resource: 'collection', body: {track_ids: [1]}}),
        blocked: ['link'], reasons: {link: 'Save or discard the staged pending tracks first'}},
    raid_recovery: {setup: () => {state.sourceRecovery = [{kind: 'raids', resource: 'collection', status: 'failed',
        body: {track_ids: [1]}}];},
        blocked: ['link'], reasons: {link: 'Resolve the saved pending-track changes in Save results first'}},
    staged_restorations: {setup: () => {state.stagedRestorations = [{uri: uri(4)}];},
        blocked: ['pair-unlink'], reasons: {'pair-unlink': 'Save or discard the staged archive tracks first'}},
    archive_queue: {setup: () => {state.archiveQueue = [uri(9)];},
        blocked: ['pair-unlink'], reasons: {'pair-unlink': 'Save or discard the staged archive tracks first'}},
    archive_review: {setup: () => openSaveReview({kind: 'restorations', resource: 'archive',
        body: {track_uris: [uri(4)]}}),
        blocked: ['pair-unlink'], reasons: {'pair-unlink': 'Save or discard the staged archive tracks first'}},
    archive_recovery: {setup: () => {state.sourceRecovery = [{kind: 'archive', resource: 'archive', status: 'unknown',
        body: {track_uris: [uri(9)]}}];},
        blocked: ['pair-unlink'], reasons: {'pair-unlink': 'Resolve the saved archive changes in Save results first'}},
    restoration_recovery: {setup: () => {state.sourceRecovery = [{kind: 'restorations', resource: 'archive',
        status: 'failed', body: {track_uris: [uri(4)]}}];},
        blocked: ['pair-unlink'], reasons: {'pair-unlink': 'Resolve the saved archive changes in Save results first'}},
    // A lost Drip leaves the target, the collection and the inbox unresolved.
    lost_drip: {setup: () => lose(liveContext('drip'), () => raidPanel.dripNow()),
        blocked: ['dismiss', 'link:collection', 'locks'],
        reasons: {locks: checked, dismiss: checked, 'link:collection': checked}},
    // A lost Collect leaves the collection and the inbox unresolved, never the target.
    lost_collect: {setup: () => lose(liveContext('raid'), () => raidPanel.raidNow()),
        blocked: ['dismiss', 'link:collection'], reasons: {dismiss: checked, 'link:collection': checked}},
    // A lost rotation leaves the target and the archive unresolved.
    lost_rotate: {setup: () => lose(routineContext(7, 'rotate'), () => startExactRoutine(7)),
        blocked: ['locks', 'pair-unlink'], reasons: {locks: checked, 'pair-unlink': checked}},
};
"""


def test_checking_the_current_version_blocks_what_save_blocks_with_its_own_reason(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    // Reading the current Spotify version holds the same groups a running Save holds, and says why.
    let answer;
    replies.push(() => new Promise(resolve => {answer = resolve;}));
    const checking = context.checkCurrentSpotify(); await tick();
    assert.equal(state.isSaving, true);
    const held = blockedKeys();
    assert.ok(held.includes('draft') && held.includes('locks'));
    const checked = 'Unavailable while the current Spotify version is checked';
    for (const key of held) assert.equal(reasonOf(key), checked, key);
    answer(fresh()); await checking;
    assert.equal(state.isSaving, false); assert.deepEqual(blockedKeys(), []);
    // A running Save holds exactly those groups, with its own reason.
    state.isSaving = true;
    assert.deepEqual(blockedKeys(), held);
    assert.equal(reasonOf('locks'), 'Unavailable while your draft saves to Spotify');
    state.isSaving = false;
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_a_confirmation_for_allowed_work_can_suspend_the_save_panel_while_save_runs(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    slice('// --- Delete ---', '</script>');
    context.flushSnapshotTimeline = () => {};
    // The shared panel contract in brief: a modal ask suspends the open context panel when its
    // beforeClose allows 'suspend'; otherwise the ask cannot open and answers null.
    let open = null;
    const asked = [];
    window.AppPanels.open = async (id, options = {}) => {open = {id, options, suspended: false}; return true;};
    window.AppPanels.ask = async (id, options = {}) => {
        asked.push(id);
        if (open && !open.options.modal) {
            if (open.options.beforeClose && (await open.options.beforeClose('suspend')) === false) return null;
            open.suspended = true;
        }
        return 'confirm';
    };
    window.AppConfirm.ask = options => window.AppPanels.ask('app-confirm', {trigger: options.trigger})
        .then(reason => reason === 'confirm');
    // Save finishes its saved collection and archive changes in its open panel.
    await window.AppPanels.open('workshop-save-panel', vm.runInContext('workshopSavePanelOptions', context));
    state.isFinalizing = true; context.markDirty();
    // Deleting a snapshot stays allowed then: its confirmation suspends the panel and the delete is sent.
    replies.push(response({success: true}));
    await context.deleteSnapshot(18, element('delete-18'));
    assert.deepEqual(asked, ['app-confirm']); assert.equal(open.suspended, true);
    assert.equal(calls.filter(call => call.url === '/api/snapshots/18' && call.options.method === 'DELETE').length, 1);
    // Closing the panel itself still waits for Save.
    assert.equal(open.options.beforeClose('close'), false); assert.equal(open.options.beforeClose('escape'), false);
    state.isFinalizing = false;
    assert.equal(open.options.beforeClose('close'), true);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


@pytest.mark.parametrize(
    "condition",
    [
        "raid_staging",
        "raid_review",
        "raid_recovery",
        "staged_restorations",
        "archive_queue",
        "archive_review",
        "archive_recovery",
        "restoration_recovery",
        "lost_drip",
        "lost_collect",
        "lost_rotate",
    ],
)
def test_staging_recovery_and_unresolved_results_block_only_the_groups_they_touch(
    node, condition
):
    run_node(
        node,
        scenario(
            CONDITIONS
            + r"""
(async () => {
    const condition = CONDITIONS['__CONDITION__'];
    const controls = controlsForEveryGroup();
    await condition.setup(); context.markDirty();
    assert.deepEqual(blockedKeys(), condition.blocked.slice().sort());
    Object.entries(condition.reasons).forEach(([key, reason]) => assert.equal(reasonOf(key), reason, key));
    // Unlinking the collection follows its own unresolved results; creating a link is not
    // blocked by them, and nothing here blocks view, pause, drafts or new routines.
    for (const [name, control] of Object.entries(controls)) {
        const blocked = context.workshopControlKeys(control).some(key => condition.blocked.includes(key));
        assert.equal(control.disabled, blocked, name); assert.equal(barred(control), blocked, name);
    }
    ['view', 'pause', 'draft', 'create', 'sources', 'drip-gate', 'drip-count'].forEach(group =>
        assert.equal(context.workshopBlockedReason(group), null, group));
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
            condition=condition,
        ),
    )


def test_on_busy_records_the_running_intent_and_never_resets_save_flags(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    // Read the hooks this page gives the controller.
    let hooks = null;
    const configure = client.configure;
    client.configure = options => {hooks = options; return configure(options);};
    listeners.DOMContentLoaded.forEach(listener => listener());
    const intent = {kind: 'raid', subject: 'target', writes: ['tracks:collection', 'inbox:target'], action: {},
        steps: ['raid']};
    state.isSaving = true; state.isFinalizing = true;
    hooks.onBusy(true, {intent});
    assert.equal(state.liveRun, intent);
    assert.equal(state.isSaving, true); assert.equal(state.isFinalizing, true);
    hooks.onBusy(false, {intent});
    assert.equal(state.liveRun, null);
    assert.equal(state.isSaving, true); assert.equal(state.isFinalizing, true);
    state.isSaving = false; state.isFinalizing = false;
    // A real run reports its intent: kind, subject, writes and the routine's saved job.
    const held = await holdRun(routineContext(7, 'rotate'), () => startExactRoutine(7));
    assert.equal(state.liveRun.kind, 'schedule'); assert.equal(state.liveRun.subject, 7);
    assert.equal(state.liveRun.action.job_type, 'rotate'); assert.deepEqual(plain(state.liveRun.steps), ['rotate']);
    assert.deepEqual(plain(state.liveRun.writes), ['tracks:target', 'tracks:archive']);
    await held.end(fresh());
    assert.equal(state.liveRun, null);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_save_results_open_mid_save_keep_the_wait_and_close_during_a_live_run(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    // A live action waits on this Save; opening its results while it saves does not abandon it.
    state.workingUris = [uri(2), uri(1)];
    replies.push(sourceContext());
    const resolved = context.resolveWorkshopConflict({type: 'draft', choice: 'save'}); await tick(); await tick();
    let finish;
    replies.push(() => new Promise(resolve => {finish = resolve;}));
    const saving = context.saveReviewedWorkshop(); await tick();
    assert.equal(state.isSaving, true);
    await context.openWorkshopResults();
    const savePanel = panels[panels.length - 1];
    assert.equal(savePanel.id, 'workshop-save-panel');
    // Save's own work still refuses closing its view and Back.
    assert.equal(savePanel.options.beforeClose(), false);
    assert.equal(element('workshop-review-back').disabled, true);
    finish(saved([uri(2), uri(1)])); await saving;
    assert.equal(await resolved, true);
    // A running live action does not trap the Save view: it can be closed, while Save waits.
    const collect = await holdRun(liveContext('raid'), () => raidPanel.raidNow());
    await context.openWorkshopResults(); context.markDirty();
    assert.equal(panels[panels.length - 1].options.beforeClose(), true);
    assert.equal(element('workshop-review-back').disabled, false);
    assert.equal(element('workshop-review-footer-status').textContent,
        'Busy — Save waits until the running live action finishes.');
    assert.equal(context.canSaveWorkshop(), false);
    await collect.end();
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_a_lock_is_saved_at_once_during_collect_but_refused_while_drip_reads_locks(
    node,
):
    run_node(
        node,
        scenario(r"""
(async () => {
    Object.assign(context, {applyLockStateToElement() {}, updateUnlockAllButton() {}, applyAllLockStates() {}});
    slice('function toggleTrackLock(', 'function applyLockStateToElement(');
    // A lock is toggled for one occurrence; its position is the occurrence's place in the draft.
    const [first, second] = context.draftOccurrences().map(occurrence => occurrence.key);
    const lockControl = grouped('lock-1', 'locks'), unlockAll = grouped('unlock-all-btn', 'locks');
    const lockCalls = () => calls.filter(call => call.url.endsWith('/locks/toggle'));
    // Collect reads no locks: the lock is saved and acknowledged at once.
    const collect = await holdRun(liveContext('raid'), () => raidPanel.raidNow());
    assert.equal(barred(lockControl), false); assert.equal(lockControl.disabled, false);
    const revision = state.configRevision;
    replies.push(response({success: true, lock: {lock_tier: 'standard', position: 0, expires_at: null}}));
    context.toggleTrackLock(first); await settle();
    assert.equal(lockCalls().length, 1);
    assert.deepEqual(JSON.parse(lockCalls()[0].options.body), {track_uri: uri(1), position: 0});
    assert.equal(state.locks[uri(1)].tier, 'standard');
    assert.equal(lastNotice(), 'Track locked (standard)');
    // A saved lock counts as a configuration change, so an open review would be read again.
    assert.ok(state.configRevision > revision);
    await collect.end();
    // Drip reads locks: the lock controls are blocked, and a handler reached anyway sends
    // nothing and says why. A refusal is expected, so it is never logged as an error.
    const drip = await holdRun(liveContext('drip'), () => raidPanel.dripNow());
    for (const control of [lockControl, unlockAll]) {
        assert.equal(barred(control), true); assert.equal(control.disabled, true);
        assert.equal(control.getAttribute('aria-describedby'), 'workshop-compat-reason-locks');
    }
    assert.equal(reasonOf('locks'), 'Unavailable while adding collected tracks runs');
    const errors = [];
    context.console = Object.assign(Object.create(console), {error: (...args) => {errors.push(args);}});
    const said = notices.length;
    context.toggleTrackLock(second); context.unlockAllTracks(); await settle();
    assert.equal(lockCalls().length, 1);
    assert.equal(calls.filter(call => call.url.endsWith('/locks/unlock-all')).length, 0);
    assert.deepEqual(notices.slice(said), Array(2).fill('Unavailable while adding collected tracks runs.'));
    assert.deepEqual(errors, []);
    await drip.end(fresh());
    replies.push(response({success: true, lock: null}));
    context.toggleTrackLock(first); await settle();
    assert.equal(lockCalls().length, 2); assert.equal(state.locks[uri(1)], undefined);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_own_states_return_after_a_request_and_a_blurred_raid_count_waits_for_collecting(
    node,
):
    run_node(
        node,
        scenario(r"""
(async () => {
    // Creating a raid playlist disables its section's buttons, then gives each its own state back.
    for (const name of ['createRaidLink', 'updateSourceRaidCount']) raidPanel[name] = loadWorkshopMethod(name);
    raidPanel.render = () => {};
    const create = grouped('raid-create-btn', 'link'), linkExisting = grouped('raid-link-btn', 'link');
    linkExisting.disabled = true;
    element('raid-link-no-pair').querySelectorAll = () => [create, linkExisting];
    let finish;
    replies.push(() => new Promise(resolve => {finish = resolve;}));
    raidPanel.createRaidLink(); await tick();
    assert.equal(create.disabled, true); assert.equal(linkExisting.disabled, true);
    finish(response({success: true, raid_link: {raid_playlist_id: 'collection'}})); await settle();
    assert.equal(create.disabled, false); assert.equal(linkExisting.disabled, true);
    // While a control is held, its own state is read and set aside from the block.
    pendingRaidsState.stagedPromotions = [{id: 1, track_uri: uri(1)}]; context.markDirty();
    assert.equal(create.disabled, true); assert.equal(context.workshopControlOwnDisabled(create), false);
    context.setWorkshopControlDisabled(create, true);
    assert.equal(context.workshopControlOwnDisabled(create), true); assert.equal(create.disabled, true);
    pendingRaidsState.stagedPromotions = []; context.markDirty();
    assert.equal(create.disabled, true); assert.equal(context.workshopControlOwnDisabled(create), true);
    // A raid count saved on blur has no control guard in front of it: while collecting runs it
    // is refused, says why and returns to its saved value.
    const input = {value: '9', dataset: {original: '5'}, classList: {add() {}, remove() {}}};
    const collect = await holdRun(liveContext('raid'), () => raidPanel.raidNow());
    raidPanel.updateSourceRaidCount(input, 1); await settle();
    assert.equal(calls.filter(call => call.url.endsWith('/raid-source-count')).length, 0);
    assert.equal(input.value, '5'); assert.equal(lastNotice(), 'Unavailable while collecting tracks for review runs.');
    await collect.end();
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_drafts_follow_only_target_writing_runs(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    // Collecting writes the collection and inbox only, so discarding a draft stays available.
    state.workingUris = [uri(2), uri(1)];
    const collect = await holdRun(liveContext('raid'), () => raidPanel.raidNow());
    context.undoPreview();
    assert.deepEqual(plain(state.workingUris), [uri(1), uri(2)]);
    await collect.end();
    // Drip writes the target, so the draft cannot change underneath it.
    const drip = await holdRun(liveContext('drip'), () => raidPanel.dripNow());
    state.workingUris = [uri(2), uri(1)];
    context.undoPreview();
    assert.deepEqual(plain(state.workingUris), [uri(2), uri(1)]);
    assert.equal(lastNotice(), 'Unavailable while adding collected tracks runs.');
    state.workingUris = [uri(1), uri(2)];
    await drip.end(fresh());
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_dismiss_all_sends_only_the_shown_rows_through_dismiss_after_asking(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    slice('function visiblePendingTracks(', 'function loadPendingRaids()');
    slice('// Dismissing writes the review inbox', 'function promoteAllPendingRaids(');
    slice('// The dismissal route takes at most', 'function updatePendingBadge()');
    const rows = Array.from({length: 205}, (_, i) => ({id: i + 1, track_uri: uri(i + 1)}));
    const staged = {id: 999, track_uri: uri(999)};
    pendingRaidsState.tracks = rows.slice(); pendingRaidsState.stagedPromotions = [staged];
    pendingRaidsState.revision = 0;
    const trigger = grouped('inbox-raids-dismiss-all', 'dismiss');
    const dismissals = () => calls.filter(call => call.url.endsWith('/pending-raids/dismiss'));
    // Declining sends nothing.
    confirmAnswer = false;
    await context.dismissAllPendingRaids(trigger);
    assert.equal(confirms.length, 1); assert.equal(calls.length, 0);
    assert.equal(confirms[0].title, 'Dismiss all pending tracks?'); assert.equal(confirms[0].trigger, trigger);
    assert.match(confirms[0].message, /205 pending tracks shown here/);
    assert.match(confirms[0].message, /staged for saving stay staged/);
    // A row staged while the confirmation is open is not dismissed underneath Save.
    window.AppConfirm.ask = async options => {
        confirms.push(options);
        pendingRaidsState.stagedPromotions.push(rows[0]); pendingRaidsState.tracks = pendingRaidsState.tracks.slice(1);
        return true;
    };
    replies.push(response({success: true, database_status: "complete", dismissed_count: 200,
        dismissed_ids: rows.slice(1, 201).map(row => row.id)}), response({success: true, database_status: "complete",
        dismissed_count: 4, dismissed_ids: rows.slice(201).map(row => row.id)}));
    await context.dismissAllPendingRaids(trigger);
    // Shown rows only, in batches the route accepts, never the dismiss-all route.
    assert.equal(dismissals().length, 2);
    const sent = dismissals().map(call => JSON.parse(call.options.body).track_ids);
    assert.deepEqual(sent[0], rows.slice(1, 201).map(row => row.id));
    assert.deepEqual(sent[1], rows.slice(201).map(row => row.id));
    assert.equal(sent.flat().includes(999), false); assert.equal(sent.flat().includes(1), false);
    assert.equal(calls.filter(call => call.url.includes('dismiss-all')).length, 0);
    assert.deepEqual(plain(pendingRaidsState.tracks), []);
    assert.deepEqual(plain(pendingRaidsState.stagedPromotions), [staged, rows[0]]);
    assert.equal(pendingRaidsState.revision, 2);
    assert.equal(lastNotice(), 'Dismissed 204 pending tracks.');
    // While collecting runs (it writes the inbox) the control is blocked, and a handler reached
    // anyway is refused before it asks: nothing is dismissed.
    window.AppConfirm.ask = async options => {confirms.push(options); return true;};
    pendingRaidsState.tracks = [rows[3]]; pendingRaidsState.stagedPromotions = [];
    const collect = await holdRun(liveContext('raid'), () => raidPanel.raidNow());
    assert.equal(barred(trigger), true); assert.equal(trigger.disabled, true);
    const sentBefore = dismissals().length, asked = confirms.length;
    await context.dismissAllPendingRaids(trigger);
    assert.equal(confirms.length, asked); assert.equal(dismissals().length, sentBefore);
    assert.equal(lastNotice(), 'Unavailable while collecting tracks for review runs.');
    assert.deepEqual(pendingRaidsState.tracks.map(track => track.id), [4]);
    await collect.end();
    // A failed batch reads the inbox again instead of guessing what remains.
    const reads = counts.pending;
    replies.push(response({success: false, message: 'Rejected'}, 502));
    await context.dismissAllPendingRaids(trigger);
    assert.equal(counts.pending, reads + 1);
    assert.deepEqual(pendingRaidsState.tracks.map(track => track.id), [4]);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_dismissals_keep_rows_staged_meanwhile_and_report_what_the_server_dismissed(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    slice('function visiblePendingTracks(', 'function loadPendingRaids()');
    slice('function promotePendingRaid(', 'function promoteAllPendingRaids(');
    slice('function promoteAllPendingRaids(', 'function updatePendingBadge()');
    Object.assign(context, {createTrackElement: track => element('track-row-' + track.uri), renumberTracks() {}});
    const rows = Array.from({length: 205}, (_, i) => ({id: i + 1, track_uri: uri(i + 1), track_name: 'Track ' + i}));
    pendingRaidsState.tracks = rows.slice(); pendingRaidsState.stagedPromotions = []; pendingRaidsState.revision = 0;
    const trigger = grouped('inbox-raids-dismiss-all', 'dismiss');
    const dismissals = () => calls.filter(call => call.url.endsWith('/pending-raids/dismiss'))
        .map(call => JSON.parse(call.options.body).track_ids);
    const staged = () => pendingRaidsState.stagedPromotions.map(track => track.id);
    // While a batch is in flight its rows cannot be staged; a later batch's row can, and that
    // batch is taken again from the rows still shown, so the staged row is never dismissed.
    let first;
    replies.push(() => new Promise(resolve => {first = resolve;}), response({success: true, database_status: "complete",
        dismissed_count: 4, dismissed_ids: [201, 202, 204, 205]}));
    const dismissing = context.dismissAllPendingRaids(trigger); await settle();
    assert.deepEqual(dismissals(), [rows.slice(0, 200).map(row => row.id)]);
    context.promotePendingRaid(7, rows[6]);
    assert.deepEqual(staged(), []); assert.equal(state.workingUris.includes(uri(7)), false);
    assert.equal(lastNotice(), 'This track is being dismissed, so it was not added.');
    context.promotePendingRaid(203, rows[202]);
    assert.deepEqual(staged(), [203]);
    first(response({success: true, database_status: "complete", dismissed_count: 200,
        dismissed_ids: rows.slice(0, 200).map(row => row.id)})); await dismissing;
    assert.equal(dismissals().length, 2); assert.deepEqual(dismissals()[1], [201, 202, 204, 205]);
    assert.deepEqual(staged(), [203]); assert.deepEqual(plain(pendingRaidsState.tracks), []);
    assert.equal(pendingRaidsState.dismissing.size, 0);
    // The notice reports what the server confirmed dismissed.
    assert.equal(lastNotice(), 'Dismissed 204 pending tracks.');
    // Rows that left during the question are not sent, and the count is the server's: one of
    // the two sent was already gone.
    const three = [301, 302, 303].map(id => ({id, track_uri: uri(id)}));
    pendingRaidsState.tracks = three.slice();
    window.AppConfirm.ask = async options => {
        confirms.push(options); context.promotePendingRaid(301, three[0]); return true;
    };
    replies.push(response({success: true, database_status: "complete", dismissed_count: 1, dismissed_ids: [302, 303]}));
    await context.dismissAllPendingRaids(trigger);
    assert.deepEqual(dismissals()[2], [302, 303]); assert.equal(lastNotice(), 'Dismissed 1 pending track.');
    // When every shown row left during the question, nothing is sent and nothing is reported.
    const last = {id: 401, track_uri: uri(401)};
    pendingRaidsState.tracks = [last];
    window.AppConfirm.ask = async options => {
        confirms.push(options); context.promotePendingRaid(401, last); return true;
    };
    const said = notices.length;
    await context.dismissAllPendingRaids(trigger);
    assert.equal(dismissals().length, 3); assert.deepEqual(notices.slice(said), ['Track staged for save.']);
    // One row's dismissal keeps that row out of staging too; Add all stages the others and says so.
    const inbox = [{id: 501, track_uri: uri(501)}, {id: 502, track_uri: uri(502)}];
    pendingRaidsState.tracks = inbox.slice(); pendingRaidsState.stagedPromotions = [];
    let single;
    replies.push(() => new Promise(resolve => {single = resolve;}));
    const one = context.dismissPendingRaid(501); await settle();
    context.promotePendingRaid(501, inbox[0]);
    assert.deepEqual(staged(), []);
    context.promoteAllPendingRaids();
    assert.deepEqual(staged(), [502]); assert.deepEqual(pendingRaidsState.tracks.map(track => track.id), [501]);
    assert.equal(lastNotice(), 'Staged 1 of 2 tracks for save; the rest are being dismissed.');
    single(response({success: true, database_status: "complete", dismissed_count: 1, dismissed_ids: [501]})); await one;
    assert.deepEqual(dismissals()[3], [501]);
    assert.deepEqual(plain(pendingRaidsState.tracks), []); assert.equal(pendingRaidsState.dismissing.size, 0);
    assert.equal(lastNotice(), 'Track dismissed.');
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_archive_unlink_stays_blocked_while_archive_work_exists_and_never_clears_it(
    node,
):
    run_node(
        node,
        scenario(r"""
(async () => {
    Object.assign(context, {renderArchivePanel() {}, renderInboxArchiveTracks() {}, populateArchiveLinkSelect() {},
        archiveInboxLoaded: true});
    slice('async function deleteArchivePair(', 'function populateArchiveLinkSelect(');
    const unlink = grouped('archive-unlink-btn', 'pair-unlink');
    const deletes = () => calls.filter(call => call.url === '/api/playlist/target/pair' &&
        call.options.method === 'DELETE');
    // Staged restorations and queued archive tracks block it with one reason, and stay put.
    state.stagedRestorations = [{uri: uri(4)}]; state.workingUris = [uri(1), uri(2), uri(4)]; context.markDirty();
    assert.equal(unlink.disabled, true); assert.equal(barred(unlink), true);
    assert.equal(element('workshop-compat-reasons').children.find(node =>
        node.id === unlink.getAttribute('aria-describedby')).textContent,
        'Save or discard the staged archive tracks first');
    // A handler reached anyway is refused before it asks, with the same reason.
    await context.deleteArchivePair(unlink); await settle();
    assert.equal(confirms.length, 0); assert.equal(deletes().length, 0);
    assert.equal(lastNotice(), 'Save or discard the staged archive tracks first.');
    assert.deepEqual(plain(state.stagedRestorations), [{uri: uri(4)}]);
    assert.equal(state.archivePair.archive_playlist_id, 'archive');
    state.stagedRestorations = []; state.workingUris = [uri(1), uri(2)]; state.archiveQueue = [uri(9)];
    context.markDirty();
    assert.equal(unlink.disabled, true);
    await context.deleteArchivePair(unlink); await settle();
    assert.equal(confirms.length, 0);
    assert.equal(deletes().length, 0); assert.deepEqual(plain(state.archiveQueue), [uri(9)]);
    // Discarding is local; then unlinking asks through the shared confirmation.
    context.undoPreview();
    assert.equal(unlink.disabled, false); assert.equal(barred(unlink), false);
    confirmAnswer = false;
    const asked = confirms.length;
    await context.deleteArchivePair(unlink);
    assert.equal(confirms.length, asked + 1); assert.equal(deletes().length, 0);
    const question = confirms[confirms.length - 1];
    assert.equal(question.title, 'Unlink the archive playlist?'); assert.equal(question.trigger, unlink);
    assert.match(question.message, /stay on Spotify/);
    // Archive work staged while the question was open keeps it blocked; nothing is cleared.
    window.AppConfirm.ask = async options => {confirms.push(options); state.archiveQueue = [uri(8)]; return true;};
    await context.deleteArchivePair(unlink); await settle();
    assert.equal(deletes().length, 0); assert.deepEqual(plain(state.archiveQueue), [uri(8)]);
    // Saved archive work in recovery blocks it too, before it asks, with its own reason.
    window.AppConfirm.ask = async options => {confirms.push(options); return true;};
    state.archiveQueue = [];
    state.sourceRecovery = [{kind: 'archive', resource: 'archive', status: 'unknown', body: {track_uris: [uri(7)]}}];
    const beforeRecovery = confirms.length;
    await context.deleteArchivePair(unlink); await settle();
    assert.equal(confirms.length, beforeRecovery); assert.equal(deletes().length, 0);
    assert.equal(lastNotice(), 'Resolve the saved archive changes in Save results first.');
    state.sourceRecovery = [];
    // With nothing staged or recovering, a confirmed unlink sends one request.
    window.AppConfirm.ask = async options => {confirms.push(options); return true;};
    replies.push(response({success: true}));
    await context.deleteArchivePair(unlink);
    assert.equal(deletes().length, 1); assert.equal(state.archivePair, null);
    assert.deepEqual(plain(state.stagedRestorations), []); assert.deepEqual(plain(state.archiveQueue), []);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_unwatching_shared_sources_confirms_and_refreshes_exact_routine_views(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    raidPanel.unwatchSource = loadWorkshopMethod('unwatchSource');
    raidPanel.loaded = true;
    raidPanel.status = {raid_link: {raid_playlist_id: 'collection'}, source_count: 2,
        sources: [{id: 3, source_playlist_id: 'one'}, {id: 4, source_playlist_id: 'two'}],
        schedule: {id: 5, job_type: 'raid', source_playlist_ids: ['one', 'two']}};
    const unwatches = () => calls.filter(call => call.url.endsWith('/raid-unwatch'));
    // Every shared-source removal asks because several exact routines may depend on it.
    replies.push(response({success: true}));
    await raidPanel.unwatchSource(3, element('unwatch-3')); await settle();
    assert.equal(confirms.length, 1); assert.equal(unwatches().length, 1);
    assert.deepEqual(JSON.parse(unwatches()[0].options.body), {source_id: 3});
    assert.match(confirms[0].message, /may change routines that use it/);
    assert.match(confirms[0].message, /Check the exact routines afterward/);
    // The last source still makes no first-match claim about which routine changes.
    raidPanel.status.schedule.source_playlist_ids = ['two'];
    confirmAnswer = false;
    await raidPanel.unwatchSource(4, element('unwatch-4'));
    assert.equal(confirms.length, 2); assert.equal(unwatches().length, 1);
    assert.doesNotMatch(confirms[1].message, /deletes that routine/);
    assert.equal(confirms[1].confirmLabel, 'Remove source');
    assert.equal(confirms[1].trigger, element('unwatch-4'));
    confirmAnswer = true; replies.push(response({success: true}));
    await raidPanel.unwatchSource(4, element('unwatch-4')); await settle();
    assert.equal(unwatches().length, 2);
    // Collecting reads the sources, so unwatching waits for it: even the last source is
    // refused before it asks.
    const collect = await holdRun(liveContext('raid'), () => raidPanel.raidNow());
    const asked = confirms.length;
    await raidPanel.unwatchSource(4, element('unwatch-4'));
    assert.equal(confirms.length, asked); assert.equal(unwatches().length, 2);
    assert.equal(lastNotice(), 'Unavailable while collecting tracks for review runs.');
    await collect.end();
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_snapshot_delete_asks_waits_for_its_restore_and_defers_timeline_rebuilds(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    Object.assign(context, {escapeHtml: String, escapeAttr: String, formatRelativeTime: () => 'now',
        getDateGroup: () => 'Today', humanizeDescription: value => value || '', SNAPSHOT_TYPE_CONFIG: {}});
    const form = Object.assign(element('snapshot-create-form'), {contains: () => false});
    form.classList = {hidden: true, contains(name) {return name === 'hidden' && this.hidden;},
        add(name) {if (name === 'hidden') this.hidden = true;},
        remove(name) {if (name === 'hidden') this.hidden = false;}, toggle() {}};
    slice('function renderSnapshotTimeline()', '// --- Manual Snapshot Creation ---');
    slice('function openManualSnapshotDialog()', '// --- Restore ---');
    slice('// --- Delete ---', '</script>');
    Object.assign(context.snapshotState, {isLoaded: true, pendingDeleteId: null, renderPending: false,
        snapshots: [{id: 17, snapshot_type: 'manual', track_count: 2, created_at: 'now', trigger_description: ''},
            {id: 18, snapshot_type: 'manual', track_count: 2, created_at: 'now', trigger_description: ''}]});
    const shown = id => Object.assign(element(id), {classList: {hidden: true, contains() {return false;},
        toggle(name, force) {if (name === 'hidden') this.hidden = force;}, add() {}, remove() {}}});
    const empty = shown('snapshot-empty'), timeline = shown('snapshot-timeline');
    const list = element('snapshot-list');
    const deletes = id => calls.filter(call => call.url === '/api/snapshots/' + id && call.options.method === 'DELETE');
    // While the question is open the timeline waits, so the trigger keeps its place.
    let answer;
    window.AppConfirm.ask = options => {confirms.push(options); return new Promise(resolve => {answer = resolve;});};
    const deleting = context.deleteSnapshot(18, element('delete-18')); await tick();
    assert.equal(context.snapshotState.pendingDeleteId, 18);
    assert.equal(confirms[0].title, 'Delete this snapshot?'); assert.equal(confirms[0].trigger, element('delete-18'));
    list.innerHTML = 'kept';
    context.renderSnapshotTimeline();
    assert.equal(list.innerHTML, 'kept'); assert.equal(context.snapshotState.renderPending, true);
    assert.equal(timeline.classList.hidden, true);
    answer(false); await deleting;
    assert.equal(context.snapshotState.pendingDeleteId, null); assert.equal(deletes(18).length, 0);
    assert.match(list.innerHTML, /data-workshop-compat="create" data-snap-id="18"/);
    // The waiting rebuild also chose between the timeline and the empty state.
    assert.equal(timeline.classList.hidden, false); assert.equal(empty.classList.hidden, true);
    // The same holds while the manual snapshot form is open.
    context.openManualSnapshotDialog();
    list.innerHTML = 'typing';
    const kept = context.snapshotState.snapshots;
    context.snapshotState.snapshots = [];
    context.renderSnapshotTimeline();
    assert.equal(list.innerHTML, 'typing'); assert.equal(timeline.classList.hidden, false);
    context.cancelManualSnapshot();
    assert.equal(empty.classList.hidden, false); assert.equal(timeline.classList.hidden, true);
    context.snapshotState.snapshots = kept; context.renderSnapshotTimeline();
    assert.match(list.innerHTML, /data-snap-id="17"/); assert.equal(empty.classList.hidden, true);
    // Confirmed, it sends one deletion.
    window.AppConfirm.ask = async options => {confirms.push(options); return true;};
    replies.push(response({success: true}));
    const reads = counts.snapshots;
    await context.deleteSnapshot(18, element('delete-18'));
    assert.equal(deletes(18).length, 1); assert.equal(counts.snapshots, reads + 1);
    // The snapshot being restored cannot be deleted: its control is blocked, and a handler
    // reached anyway is refused before it asks. Another snapshot can be deleted.
    const restoring = await holdRun(snapshotContext(17), () => context.restoreSnapshot(17, element('snapshot-17')));
    const delete17 = grouped('delete-17', 'create', {snapId: '17'});
    const delete18 = grouped('delete-18', 'create', {snapId: '18'});
    assert.equal(barred(delete17), true); assert.equal(barred(delete18), false);
    const asked = confirms.length;
    await context.deleteSnapshot(17, delete17);
    assert.equal(confirms.length, asked); assert.equal(context.snapshotState.pendingDeleteId, null);
    assert.equal(deletes(17).length, 0); assert.equal(lastNotice(), 'Unavailable while this snapshot is restored.');
    window.AppConfirm.ask = async options => {confirms.push(options); return false;};
    await context.deleteSnapshot(18, delete18);
    assert.equal(confirms.length, asked + 1);
    await restoring.end(fresh());
    assert.equal(deletes(17).length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_every_handler_that_asks_is_refused_before_asking_while_its_group_is_blocked(
    node,
):
    run_node(
        node,
        scenario(r"""
(async () => {
    slice('async function deleteArchivePair(', 'function populateArchiveLinkSelect(');
    slice('// The dismissal route takes at most', 'function updatePendingBadge()');
    slice('// --- Delete ---', '</script>');
    for (const name of ['unwatchSource', 'unlinkRaidPlaylist']) {
        raidPanel[name] = loadWorkshopMethod(name);
    }
    raidPanel.status = {raid_link: {raid_playlist_id: 'collection', raid_playlist_name: 'Collection list'},
        source_count: 1, sources: [{id: 4, source_playlist_id: 'two'}],
        schedule: {id: 5, job_type: 'raid', source_playlist_ids: ['two']}};
    pendingRaidsState.tracks = [{id: 1, track_uri: uri(1)}];
    // No question is ever answered, as in a browser where nobody clicks: a handler that asked
    // would never settle.
    window.AppConfirm.ask = options => {confirms.push(options); return new Promise(() => {});};
    // One direct call: what it asked, what it sent, whether it settled and what it said.
    async function direct(call) {
        const before = {asked: confirms.length, sent: calls.length, said: notices.length};
        let settled = false;
        Promise.resolve(call()).then(() => {settled = true;});
        await settle();
        return {asked: confirms.length - before.asked, sent: calls.length - before.sent, settled,
            said: notices.slice(before.said)};
    }
    const refused = reason => ({asked: 0, sent: 0, settled: true, said: [reason + '.']});
    const unlinkRaid = () => raidPanel.unlinkRaidPlaylist(element('raid-link-unlink-btn'));
    const unlinkArchive = () => context.deleteArchivePair(element('archive-unlink-btn'));
    // Staged pending tracks block the raid unlink; staged archive tracks block the archive unlink.
    pendingRaidsState.stagedPromotions = [{id: 2, track_uri: uri(2)}];
    assert.deepEqual(await direct(unlinkRaid), refused('Save or discard the staged pending tracks first'));
    pendingRaidsState.stagedPromotions = []; state.archiveQueue = [uri(9)];
    assert.deepEqual(await direct(unlinkArchive), refused('Save or discard the staged archive tracks first'));
    state.archiveQueue = [];
    // While the collect routine runs: dismiss all, its source and both unlinks.
    const raiding = await holdRun(routineContext(5, 'raid'), () => startExactRoutine(5));
    const collecting = 'Unavailable while the collect routine runs';
    assert.deepEqual(await direct(() => context.dismissAllPendingRaids(element('inbox-raids-dismiss-all'))),
        refused(collecting));
    assert.deepEqual(await direct(() => raidPanel.unwatchSource(4, element('unwatch-4'))), refused(collecting));
    assert.deepEqual(await direct(unlinkRaid), refused(collecting));
    assert.deepEqual(await direct(unlinkArchive), refused(collecting));
    await raiding.end();
    // Snapshot 17's delete is refused while that exact snapshot is restored.
    const restoring = await holdRun(snapshotContext(17), () => context.restoreSnapshot(17, element('snapshot-17')));
    assert.deepEqual(await direct(() => context.deleteSnapshot(17, element('delete-17'))),
        refused('Unavailable while this snapshot is restored'));
    assert.equal(context.snapshotState.pendingDeleteId, null);
    await restoring.end(fresh());
    assert.equal(confirms.length, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_confirmed_actions_return_focus_near_their_trigger_after_the_re_render(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    // Focus as a browser keeps it: a removed control hands it to the page body; a hidden one
    // keeps it but shows nowhere. Each control sits in a [data-panel-return] section.
    document.body = Object.assign(element('page-body'), {isConnected: true});
    const area = id => Object.assign(element(id), {isConnected: true, getClientRects: () => [{}]});
    function control(id, section, group) {
        const el = Object.assign(element(id), {isConnected: true, shown: true});
        el.dataset.workshopCompat = group;
        el.getClientRects = () => (el.isConnected && el.shown ? [{}] : []);
        el.closest = selector => (selector === '[data-panel-return]' ? section
            : selector === '[data-workshop-compat]' ? el : null);
        return el;
    }
    const remove = el => {
        el.isConnected = false;
        if (document.activeElement === el) document.activeElement = document.body;
    };
    // Archive unlink re-renders its area at once: the hidden Unlink hands focus to the area.
    Object.assign(context, {renderInboxArchiveTracks() {}, populateArchiveLinkSelect() {}});
    slice('async function deleteArchivePair(', 'function populateArchiveLinkSelect(');
    const archiveArea = area('archive-config');
    const archiveUnlink = control('archive-unlink-btn', archiveArea, 'pair-unlink');
    context.renderArchivePanel = () => { archiveUnlink.shown = Boolean(state.archivePair); };
    document.activeElement = archiveUnlink;
    replies.push(response({success: true}));
    await context.deleteArchivePair(archiveUnlink);
    assert.equal(state.archivePair, null); assert.equal(document.activeElement, archiveArea);
    // Raid unlink waits for the status read to render.
    raidPanel.unlinkRaidPlaylist = loadWorkshopMethod('unlinkRaidPlaylist');
    raidPanel.status.raid_link = {raid_playlist_id: 'collection', raid_playlist_name: 'Collection list'};
    const linkArea = area('raid-link-area');
    const raidUnlink = control('raid-link-unlink-btn', linkArea, 'link');
    let statusRead;
    raidPanel.loadStatus = () => new Promise(resolve => {statusRead = resolve;});
    document.activeElement = raidUnlink;
    replies.push(response({success: true}));
    const unlinking = raidPanel.unlinkRaidPlaylist(raidUnlink); await settle();
    assert.equal(document.activeElement, raidUnlink);
    raidUnlink.shown = false; statusRead(); await unlinking;
    assert.equal(document.activeElement, linkArea);
    // Unwatching the routine's last source asks, then the sources re-render without its row.
    raidPanel.unwatchSource = loadWorkshopMethod('unwatchSource');
    raidPanel.loaded = true;
    const sourcesArea = area('raid-sources-area');
    const unwatch = control('unwatch-4', sourcesArea, 'sources');
    Object.assign(raidPanel.status, {sources: [{id: 4, source_playlist_id: 'two'}],
        schedule: {id: 5, job_type: 'raid', source_playlist_ids: ['two']}});
    let sourcesRead;
    raidPanel.loadStatus = () => new Promise(resolve => {sourcesRead = resolve;});
    document.activeElement = unwatch;
    const asked = confirms.length;
    replies.push(response({success: true}));
    const unwatching = raidPanel.unwatchSource(4, unwatch); await settle();
    assert.equal(confirms.length, asked + 1); assert.equal(document.activeElement, unwatch);
    remove(unwatch); sourcesRead(); await unwatching;
    assert.equal(document.activeElement, sourcesArea);
    // Dismiss all: rows left keep the control and its focus; the last rows hide it.
    slice('function visiblePendingTracks(', 'function loadPendingRaids()');
    slice('// Dismissing writes the review inbox', 'function promoteAllPendingRaids(');
    slice('// The dismissal route takes at most', 'function updatePendingBadge()');
    const inboxArea = area('inbox-column');
    const dismissAll = control('inbox-raids-dismiss-all', inboxArea, 'dismiss');
    context.renderPendingRaids = () => { dismissAll.shown = pendingRaidsState.tracks.length > 0; };
    pendingRaidsState.tracks = [{id: 1, track_uri: uri(1)}, {id: 2, track_uri: uri(2)}];
    window.AppConfirm.ask = async options => {
        confirms.push(options); pendingRaidsState.tracks.push({id: 3, track_uri: uri(3)}); return true;
    };
    document.activeElement = dismissAll;
    const moves = focusMoves.length;
    replies.push(response({success: true, database_status: "complete", dismissed_count: 2, dismissed_ids: [1, 2]}));
    await context.dismissAllPendingRaids(dismissAll);
    assert.equal(document.activeElement, dismissAll); assert.equal(focusMoves.length, moves);
    window.AppConfirm.ask = async options => {confirms.push(options); return confirmAnswer;};
    replies.push(response({success: true, database_status: "complete", dismissed_count: 1, dismissed_ids: [3]}));
    await context.dismissAllPendingRaids(dismissAll);
    assert.deepEqual(plain(pendingRaidsState.tracks), []); assert.equal(document.activeElement, inboxArea);
    // Snapshot delete: the timeline rebuilt while the question was open replaces the trigger,
    // so focus goes to its rebuilt control; once the deleted snapshot is gone, to the section.
    slice('// --- Delete ---', '</script>');
    const snapshotArea = area('snapshot-section');
    const delete18 = control('delete-18', snapshotArea, 'create');
    const rebuilt18 = control('rebuilt-delete-18', snapshotArea, 'create');
    document.querySelector = selector => (rebuilt18.isConnected &&
        selector === '[data-action-click="wk-snapshot-delete"][data-snap-id="18"]' ? rebuilt18 : null);
    context.flushSnapshotTimeline = () => { if (delete18.isConnected) remove(delete18); };
    let snapshotsRead;
    context.loadSnapshots = () => new Promise(resolve => {snapshotsRead = resolve;});
    document.activeElement = delete18;
    confirmAnswer = false;
    await context.deleteSnapshot(18, delete18);
    assert.equal(document.activeElement, rebuilt18);
    confirmAnswer = true;
    replies.push(response({success: true}));
    const deleting = context.deleteSnapshot(18, rebuilt18); await settle();
    assert.equal(document.activeElement, rebuilt18);
    remove(rebuilt18); snapshotsRead(); await deleting;
    assert.equal(document.activeElement, snapshotArea);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def _focus_regions(html):
    """Each element id with the [data-panel-return] sections around it, nearest first."""
    found = {}

    def walk(node, regions):
        if isinstance(node, str):
            return
        attrs = dict(node["a"])
        here = [node] + regions if "data-panel-return" in attrs else regions
        if "id" in attrs:
            found[attrs["id"]] = here
        for child in node["c"]:
            walk(child, here)

    for top in page_tree(html):
        walk(top, [])
    return found


@pytest.mark.parametrize(
    "trigger,remains,neighbour",
    [
        ("raid-link-unlink-btn", "raid-link-no-pair", "raid-sources-empty"),
        ("raid-sources-list", "raid-sources-empty", "raid-link-no-pair"),
        ("archive-unlink-btn", "archive-no-pair", "inbox-archive-list"),
        ("inbox-raids-dismiss-all", "inbox-raids-empty", "raid-sources-empty"),
        ("snapshot-list", "snapshot-empty", "schedules-empty"),
        ("schedules-list", "schedules-empty", "snapshot-empty"),
        # Per-row actions that remove their row fall back to the rows' own section.
        ("inbox-raids-list", "inbox-raids-empty", "raid-now-btn"),
        ("inbox-archive-list", "inbox-archive-empty", "archive-pair-summary"),
        ("track-list", "workshop-tracks-empty", "workshop-draft-footer"),
        ("unlock-all-btn", "workshop-lock-summary", "workshop-visibility-btn"),
    ],
)
def test_each_confirmed_action_returns_focus_to_its_own_section(trigger, remains, neighbour):
    """The section nearest a confirmed action's trigger holds what shows once it is done, and
    is that action's own section, not one around its neighbours too."""
    regions = _focus_regions(workshop_markup())
    assert regions[trigger], f"{trigger} sits in no [data-panel-return] section"
    section = regions[trigger][0]
    assert any(region is section for region in regions[remains]), (
        f"{remains} is outside the section that takes focus from {trigger}"
    )
    assert not any(region is section for region in regions[neighbour]), (
        f"the section that takes focus from {trigger} also holds {neighbour}"
    )


def test_section_reads_settle_once_they_have_rendered(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    // An action that waits for a section's read before returning focus needs the read to
    // settle only after it rendered.
    async function settlesAfterRendering(name, start, body) {
        let answer, rendered = false, settled = null;
        replies.push(() => new Promise(resolve => {answer = resolve;}));
        const reading = start(() => {rendered = true;});
        assert.equal(typeof reading.then, 'function', name);
        reading.then(() => {settled = rendered;});
        await settle(); assert.equal(settled, null, name);
        answer(response(body)); await settle();
        assert.equal(settled, true, name);
    }
    slice('function loadSnapshots()', 'function humanizeDescription(');
    await settlesAfterRendering('snapshots', render => {
        context.renderSnapshotTimeline = render; return context.loadSnapshots();
    }, {snapshots: []});
    const loadStatus = loadWorkshopMethod('loadStatus');
    await settlesAfterRendering('raid status', render => {
        raidPanel.render = render; return loadStatus.call(raidPanel);
    }, {raid_status: {}});
    Object.assign(context, {archiveInboxLoaded: false});
    slice('function fetchArchiveTracks(', 'function loadArchiveTracks()');
    await settlesAfterRendering('archive tracks', render => {
        context.renderInboxArchiveTracks = render; return context.fetchArchiveTracks(true);
    }, {tracks: [], total: 0});
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_save_status_is_spoken_once_per_change_through_the_announcer(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    // The status shown at load is not news, and renders that change nothing say nothing.
    assert.deepEqual(announcements, []);
    const said = announcements.length;
    context.renderSaveStatus(); context.markDirty(); context.renderSaveStatus();
    assert.equal(announcements.length, said);
    // Each change is said once, in the words the status line shows.
    state.workingUris = [uri(2), uri(1)]; context.markDirty(); context.renderSaveStatus();
    assert.deepEqual(announcements.slice(said), [element('workshop-save-state').textContent + ' — ' +
        element('workshop-save-message').textContent]);
    assert.match(announcements[announcements.length - 1], /^Draft — /);
    // So is a finalize, and its end, whatever region a confirmation has made inert meanwhile.
    state.workingUris = [uri(1), uri(2)]; state.isFinalizing = true; context.markDirty();
    assert.match(announcements[announcements.length - 1], /^Busy — Playlist saved; finishing/);
    state.isFinalizing = false; context.markDirty(); context.renderSaveStatus();
    assert.match(announcements[announcements.length - 1], /^Ready — /);
    assert.equal(announcements.length, said + 3);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_a_superseded_read_settles_once_the_newest_has_rendered(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    // A raid unlink waits for its status read before returning focus. A newer status read
    // starts meanwhile, and the first reply, now stale, renders nothing: focus moves only once
    // the newer read has rendered the unlinked panel.
    document.body = Object.assign(element('page-body'), {isConnected: true});
    const area = Object.assign(element('raid-link-area'), {isConnected: true, getClientRects: () => [{}]});
    const unlink = Object.assign(element('raid-link-unlink-btn'), {isConnected: true, shown: true});
    unlink.getClientRects = () => (unlink.shown ? [{}] : []);
    unlink.closest = selector => (selector === '[data-panel-return]' ? area : null);
    raidPanel.unlinkRaidPlaylist = loadWorkshopMethod('unlinkRaidPlaylist');
    const loadStatus = loadWorkshopMethod('loadStatus');
    raidPanel.loadStatus = function () { return loadStatus.call(raidPanel); };
    raidPanel.render = () => { unlink.shown = Boolean(raidPanel.status.raid_link); };
    raidPanel.status.raid_link = {raid_playlist_id: 'collection', raid_playlist_name: 'Collection list'};
    let first, second;
    replies.push(response({success: true}), () => new Promise(resolve => {first = resolve;}),
        () => new Promise(resolve => {second = resolve;}));
    document.activeElement = unlink;
    const unlinking = raidPanel.unlinkRaidPlaylist(unlink); await settle();
    const newer = raidPanel.loadStatus(); await settle();
    first(response({raid_status: {raid_link: {raid_playlist_id: 'collection'}}})); await settle();
    assert.equal(document.activeElement, unlink);
    second(response({raid_status: {raid_link: null}})); await unlinking; await newer;
    assert.equal(unlink.shown, false); assert.equal(document.activeElement, area);
    // Every view's read does the same: an older read settles only once the newest rendered.
    async function olderSettlesAfterNewest(name, start, body) {
        let older, newest, rendered = 0, settledAt = null;
        replies.push(() => new Promise(resolve => {older = resolve;}),
            () => new Promise(resolve => {newest = resolve;}));
        const reading = start(() => {rendered++;});
        start(() => {rendered++;});
        reading.then(() => {settledAt = rendered;});
        await settle();
        older(response(body)); await settle();
        assert.equal(settledAt, null, name);
        newest(response(body)); await settle();
        assert.equal(settledAt, 1, name);
    }
    await olderSettlesAfterNewest('raid status', render => {
        raidPanel.render = render; return raidPanel.loadStatus();
    }, {raid_status: {}});
    slice('function loadSnapshots()', 'function humanizeDescription(');
    await olderSettlesAfterNewest('snapshots', render => {
        context.renderSnapshotTimeline = render; return context.loadSnapshots();
    }, {snapshots: []});
    Object.assign(context, {archiveInboxLoaded: false});
    slice('function fetchArchiveTracks(', 'function loadArchiveTracks()');
    await olderSettlesAfterNewest('archive tracks', render => {
        context.renderInboxArchiveTracks = render; return context.fetchArchiveTracks(true);
    }, {tracks: [], total: 0});
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_create_flows_keep_focus_in_the_section_that_shows_what_they_made(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    // Each create or link control sits in the form its section replaces with what was made.
    document.body = Object.assign(element('page-body'), {isConnected: true});
    const area = name => Object.assign(element(name), {isConnected: true, getClientRects: () => [{}]});
    function control(id, section) {
        const el = Object.assign(element(id), {isConnected: true, shown: true});
        el.getClientRects = () => (el.shown ? [{}] : []);
        el.closest = selector => (selector === '[data-panel-return]' ? section : null);
        return el;
    }
    // Creating or linking a raid playlist renders the linked view.
    const linkArea = area('raid-link-area');
    for (const [name, id, reply] of [['createRaidLink', 'raid-create-btn', {raid_playlist_id: 'collection'}],
        ['linkExistingRaid', 'raid-link-btn', {raid_playlist_id: 'other'}]]) {
        raidPanel[name] = loadWorkshopMethod(name);
        const trigger = control(id, linkArea);
        Object.assign(element('raid-link-select'), {value: 'other', selectedIndex: 0,
            options: [{dataset: {name: 'Other list'}, textContent: 'Other list'}]});
        raidPanel.status.raid_link = null;
        raidPanel.render = () => { trigger.shown = !raidPanel.status.raid_link; };
        document.activeElement = trigger;
        replies.push(response({success: true, raid_link: reply}));
        raidPanel[name](trigger); await settle();
        assert.equal(document.activeElement, linkArea, name);
    }
    // Creating or linking an archive renders the paired view.
    Object.assign(context, {loadArchiveTracks() {}});
    slice('// Creating or linking an archive shows the paired view', '// Unlinking never clears staged archive work');
    slice('function linkExistingArchive()', 'function toggleAutoArchive()');
    const archiveArea = area('archive-area');
    Object.assign(element('archive-link-select'), {value: 'old-archive', selectedIndex: 0,
        options: [{dataset: {name: 'Old archive'}, textContent: 'Old archive'}]});
    const archiveFlows = [['createArchivePair', 'archive-create-btn'], ['linkExistingArchive', 'archive-link-btn']];
    for (const [name, id] of archiveFlows) {
        const trigger = control(id, archiveArea);
        context.renderArchivePanel = () => { trigger.shown = !state.archivePair; };
        state.archivePair = null;
        document.activeElement = trigger;
        replies.push(response({success: true, pair: {archive_playlist_id: 'archive'}}));
        context[name](trigger); await settle();
        assert.equal(document.activeElement, archiveArea, name);
    }
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_an_unconfirmed_playlist_creation_reloads_a_fresh_list_of_playlists_to_link(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    // When Spotify's answer to a create is lost the playlist may exist: the page says so and
    // reloads the playlists it offers to link, asking the server to bypass its cached list. Create
    // stays busy until that list has loaded, and the rebuilt list holds no choice, so Link waits for one.
    // Any other failure reloads nothing, and an ordinary load never asks for a refresh.
    const unconfirmed = () => response({success: false, error_code: 'creation_unknown', outcome: 'unknown',
        message: 'We could not confirm whether the playlist was created.'}, 502);
    const refused = () => response({success: false, message: 'Failed to create it'}, 500);
    const listed = () => response({success: true, playlists: [{id: 'other', name: 'Other list', track_count: 3}]});
    let base = calls.length;
    const urls = () => calls.slice(base).map(call => call.url);
    state.archivePair = null;
    // A raid playlist.
    raidPanel.createRaidLink = loadWorkshopMethod('createRaidLink');
    raidPanel.populateRaidLinkSelect = loadWorkshopMethod('populateRaidLinkSelect');
    const create = grouped('raid-create-btn', 'link'), linkRaid = grouped('raid-link-btn', 'link');
    const raidSelect = element('raid-link-select');
    // A rebuilt list holds no choice, as in a browser; a request holds the section's controls, as in the page.
    Object.defineProperty(raidSelect, 'innerHTML', {get: () => '', set() { raidSelect.value = ''; }});
    element('raid-link-no-pair').querySelectorAll = () => [create, raidSelect, linkRaid];
    replies.push(listed());
    raidPanel.populateRaidLinkSelect(); await settle();
    assert.deepEqual(urls(), ['/api/user-playlists']);
    assert.equal(raidPanel._playlistsPopulated, true);
    replies.push(refused());
    raidPanel.createRaidLink(); await settle();
    assert.deepEqual(urls().slice(1), ['/api/playlist/target/raid-link']);
    assert.equal(lastNotice(), 'Failed to create it'); assert.equal(raidPanel._playlistsPopulated, true);
    // A playlist was chosen to link before Create was pressed.
    raidSelect.value = 'other'; context.setWorkshopControlDisabled(linkRaid, false);
    let reloadRaid;
    replies.push(unconfirmed(), () => new Promise(resolve => {reloadRaid = resolve;}));
    raidPanel.createRaidLink(create); await settle();
    assert.deepEqual(urls().slice(2), ['/api/playlist/target/raid-link', '/api/user-playlists?refresh=1']);
    assert.equal(lastNotice(), 'We could not confirm whether the playlist was created.');
    assert.equal(create.getAttribute('aria-disabled'), 'true');  // the list has not loaded yet
    reloadRaid(listed()); await settle();
    assert.equal(create.getAttribute('aria-disabled'), undefined);
    assert.equal(raidPanel._playlistsPopulated, true);
    assert.equal(raidSelect.value, ''); assert.equal(linkRaid.disabled, true);
    // Reloading the list without a create keeps Link in step with the select as well.
    raidSelect.value = 'other'; context.setWorkshopControlDisabled(linkRaid, false);
    replies.push(listed());
    raidPanel.populateRaidLinkSelect(true); await settle();
    assert.equal(raidSelect.value, ''); assert.equal(linkRaid.disabled, true);
    // An archive playlist.
    base = calls.length;
    slice('// Creating or linking an archive shows the paired view', '// Unlinking never clears staged archive work');
    slice('function populateArchiveLinkSelect(', 'function linkExistingArchive()');
    const archive = grouped('archive-create-btn', 'pair'), select = element('archive-link-select');
    const archiveLink = grouped('archive-link-btn', 'pair');
    Object.defineProperty(select, 'innerHTML', {get: () => '', set() { select.value = ''; }});
    let changeListeners = 0;
    const attach = select.addEventListener;
    select.addEventListener = function (type, handler) {
        if (type === 'change') changeListeners++;
        return attach.call(this, type, handler);
    };
    replies.push(listed());
    context.populateArchiveLinkSelect(); await settle();
    assert.deepEqual(urls(), ['/api/user-playlists']);
    assert.equal(context.archiveState.playlistsPopulated, true);
    replies.push(refused());
    context.createArchivePair(archive); await settle();
    assert.deepEqual(urls().slice(1), ['/api/playlist/target/pair']);
    assert.equal(lastNotice(), 'Failed to create it'); assert.equal(context.archiveState.playlistsPopulated, true);
    // A playlist was chosen to link before Create was pressed.
    select.value = 'other'; context.setWorkshopControlDisabled(archiveLink, false);
    let reloadArchive;
    replies.push(unconfirmed(), () => new Promise(resolve => {reloadArchive = resolve;}));
    context.createArchivePair(archive); await settle();
    assert.deepEqual(urls().slice(2), ['/api/playlist/target/pair', '/api/user-playlists?refresh=1']);
    assert.equal(lastNotice(), 'We could not confirm whether the playlist was created.');
    assert.equal(archive.getAttribute('aria-disabled'), 'true');  // the list has not loaded yet
    reloadArchive(listed()); await settle();
    assert.equal(archive.getAttribute('aria-disabled'), undefined);
    assert.equal(context.archiveState.playlistsPopulated, true);
    assert.equal(select.value, ''); assert.equal(archiveLink.disabled, true);
    // Loading the list again did not stack a second listener on the select.
    assert.equal(changeListeners, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def _template_nodes():
    """Every element of the template's markup, in document order."""
    nodes = []

    def walk(node):
        if isinstance(node, str):
            return
        nodes.append(node)
        for child in node["c"]:
            walk(child)

    for top in page_tree(workshop_markup()):
        walk(top)
    return nodes


def _text(node):
    return node if isinstance(node, str) else "".join(_text(child) for child in node["c"])


def _holds(node, target):
    return node is target or any(_holds(child, target) for child in node["c"] if not isinstance(child, str))


def test_every_focus_section_is_a_group_named_by_its_own_heading():
    """Focus that lands on a section says where it is: the section is a group named by a
    heading inside it."""
    nodes = _template_nodes()
    by_id = {dict(node["a"]).get("id"): node for node in nodes}
    sections = [node for node in nodes if "data-panel-return" in dict(node["a"])]
    assert len(sections) == 15
    for section in sections:
        attrs = dict(section["a"])
        assert attrs.get("role") == "group", attrs
        heading = by_id.get(attrs.get("aria-labelledby"))
        assert heading is not None and _holds(section, heading) and _text(heading).strip(), attrs


def test_create_and_link_actions_hand_their_control_to_the_handler():
    """Create and link handlers that find no control by id are handed the one activated, so
    focus can stay in its section."""
    source = TEMPLATE.read_text()
    for action, call in [
        ("wk-025", "raidPanel.createRaidLink(el)"),
        ("wk-028", "raidPanel.linkExistingRaid(el)"),
        ("wk-050", "createArchivePair(el)"),
    ]:
        assert f"'{action}': function (el, event) {{ {call}; }}" in source, action


def test_workshop_speaks_through_the_announcer_except_in_the_save_panel():
    """The Save review and visible routine-read failure announce in their own panels.
    Other Workshop changes use the page announcer, which confirmations never make inert.
    The routine alert appears only after its async read fails."""
    live = sorted(
        dict(node["a"]).get("id", "?")
        for node in _template_nodes()
        if dict(node["a"]).get("role") in {"status", "alert", "log"} or "aria-live" in dict(node["a"])
    )
    assert live == ["?", "schedules-error", "workshop-review-message"]


# Save-status announcements, told apart from the controller's own.
SAVE_WORDS = r"""
    const saveWords = () => announcements.filter(message => /^(Ready|Draft|Busy|Attention) — /.test(message));
"""


def test_a_live_run_adds_no_save_status_words_but_save_work_still_does(node):
    run_node(
        node,
        scenario(r"""
(async () => {
__SAVE_WORDS__
    // A live run's controller announces its start and outcome; the Save status the run
    // changes (Busy, then Ready) is shown, and not said again.
    const collect = await holdRun(liveContext('raid'), () => raidPanel.raidNow());
    assert.equal(element('workshop-save-state').textContent, 'Busy');
    await collect.end();
    assert.equal(element('workshop-save-state').textContent, 'Ready');
    assert.ok(announcements.some(message => message.startsWith('Running: ')));
    assert.deepEqual(saveWords(), []);
    // Save's own work is still said: a commit and its end, then a finalize and its end.
    state.isSaving = true; context.markDirty();
    assert.match(saveWords()[0], /^Busy — Checking Spotify/);
    state.isSaving = false; context.markDirty();
    assert.match(saveWords()[1], /^Ready — /);
    state.isFinalizing = true; context.markDirty();
    assert.match(saveWords()[2], /^Busy — Playlist saved; finishing/);
    state.isFinalizing = false; context.markDirty();
    assert.equal(saveWords().length, 4);
})().catch(e => {console.error(e); process.exitCode = 1;});
""", save_words=SAVE_WORDS),
    )


def test_the_save_status_is_not_said_over_the_open_save_panel(node):
    run_node(
        node,
        scenario(r"""
(async () => {
__SAVE_WORDS__
    const panel = element('workshop-save-panel');
    // While the Save panel shows, its own live message speaks for Save.
    panel.hidden = false;
    state.isFinalizing = true; context.markDirty();
    assert.equal(element('workshop-save-state').textContent, 'Busy');
    assert.deepEqual(saveWords(), []);
    // Suspended by a confirmation, or closed, it says nothing: the status is announced.
    panel.hidden = true;
    state.isFinalizing = false; context.markDirty();
    assert.deepEqual(saveWords(), ['Ready — ' + element('workshop-save-message').textContent]);
})().catch(e => {console.error(e); process.exitCode = 1;});
""", save_words=SAVE_WORDS),
    )


def test_raid_status_reload_is_latest_wins_keeps_the_panel_and_preserves_edits(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    const loadStatus = loadWorkshopMethod('loadStatus');
    const rendered = [];
    Object.assign(raidPanel, {loaded: true, isLoading: false, render() {rendered.push(this.status.tag);}});
    const content = element('raid-panel-content'), hiddenAdds = [];
    content.classList = {add(name) {hiddenAdds.push(name);}, remove() {}, contains() {return false;}, toggle() {}};
    let first, second;
    replies.push(() => new Promise(resolve => {first = resolve;}), () => new Promise(resolve => {second = resolve;}));
    loadStatus.call(raidPanel); loadStatus.call(raidPanel);
    // A reload keeps the shown panel; only the newest response renders.
    assert.deepEqual(hiddenAdds, []);
    second(response({raid_status: {tag: 'newer'}})); await settle();
    first(response({raid_status: {tag: 'older'}})); await settle();
    assert.deepEqual(rendered, ['newer']); assert.equal(raidPanel.status.tag, 'newer');
    assert.equal(raidPanel.isLoading, false);
    // A Drip count typed but not saved survives a re-render; Save stays offered.
    const renderRaidLink = loadWorkshopMethod('renderRaidLink');
    element('raid-drip-toggle').firstElementChild = {classList: {add() {}, remove() {}}};
    const saveCount = element('raid-drip-count-save');
    saveCount.classList = {hidden: true, add(name) {if (name === 'hidden') this.hidden = true;},
        remove(name) {if (name === 'hidden') this.hidden = false;},
        toggle(name, force) {if (name === 'hidden') this.hidden = force;}, contains() {return false;}};
    raidPanel._savedDripCount = null;
    const link = count => ({raid_playlist_id: 'collection', raid_playlist_name: 'Collection', drip_enabled: true,
        drip_count: count});
    renderRaidLink.call(raidPanel, link(3));
    assert.equal(String(element('raid-drip-count').value), '3');
    assert.equal(element('raid-link-unlink-btn').dataset.collectionId, 'collection');
    // The collection's size shows only when the status reply carries it.
    assert.equal(element('raid-link-track-count').hidden, true);
    assert.equal(element('raid-link-track-count').textContent, '');
    renderRaidLink.call(raidPanel, Object.assign(link(3), {track_count: 12}));
    assert.equal(element('raid-link-track-count').hidden, false);
    assert.equal(element('raid-link-track-count').textContent, '12 tracks');
    raidPanel._savedDripCount = null;
    renderRaidLink.call(raidPanel, link(3));
    element('raid-drip-count').value = '7';
    renderRaidLink.call(raidPanel, link(4));
    assert.equal(element('raid-drip-count').value, '7'); assert.equal(raidPanel._savedDripCount, 4);
    assert.equal(saveCount.classList.hidden, false);
    element('raid-drip-count').value = '4';
    renderRaidLink.call(raidPanel, link(5));
    assert.equal(String(element('raid-drip-count').value), '5'); assert.equal(saveCount.classList.hidden, true);
    // A raid count being edited survives: an unchanged list is not rebuilt; a rebuilt one
    // restores the typed value and focus, and saves it when the field blurs.
    context.escapeHtml = String; context.escapeAttr = String;
    const renderSources = loadWorkshopMethod('renderSources');
    raidPanel.updateSourceRaidCount = loadWorkshopMethod('updateSourceRaidCount');
    const list = element('raid-sources-list');
    let inputs = [];
    Object.defineProperty(list, 'innerHTML', {configurable: true, get() {return this._html;}, set(html) {
        this._html = html;
        inputs = [...html.matchAll(/data-source-id="(\d+)" data-original="(\d+)"/g)].map(match => ({
            dataset: {sourceId: match[1], original: match[2], workshopCompat: 'sources'}, value: match[2],
            classList: {add() {}, remove() {}}, focus() {document.activeElement = this;},
            addEventListener(type, handler) {this['on' + type] = handler;}}));
    }});
    list.querySelectorAll = () => inputs;
    const sources = extra => [{id: 1, source_name: 'One', raid_count: 5}, {id: 2, source_name: 'Two', raid_count: 5}]
        .concat(extra || []);
    raidPanel._sourcesSignature = null;
    renderSources.call(raidPanel, sources());
    const typed = inputs[0];
    typed.value = '9'; document.activeElement = typed;
    renderSources.call(raidPanel, sources());
    assert.equal(inputs[0], typed); assert.equal(typed.value, '9');
    renderSources.call(raidPanel, sources([{id: 3, source_name: 'Three', raid_count: 2}]));
    assert.notEqual(inputs[0], typed);
    assert.equal(inputs[0].value, '9'); assert.equal(document.activeElement, inputs[0]);
    assert.equal(inputs[1].value, '5');
    // The saved field's one-second highlight would keep this scenario waiting.
    context.setTimeout = () => 0;
    replies.push(response({success: true}));
    inputs[0].onblur(); await settle();
    const saved = calls.filter(call => call.url.endsWith('/raid-source-count'));
    assert.equal(saved.length, 1); assert.deepEqual(JSON.parse(saved[0].options.body), {source_id: 1, raid_count: 9});
    assert.equal(inputs[0].dataset.original, 9);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_inbox_reloads_queue_never_drop_and_stale_reads_never_bring_rows_back(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    slice('function visiblePendingTracks(', 'function renderPendingRaids()');
    Object.assign(pendingRaidsState, {loaded: false, isLoading: false, reloadQueued: false, revision: 0});
    const reads = [];
    let answer;
    context.workshopRequest = url => {
        reads.push(url);
        return reads.length === 1 ? new Promise(resolve => {answer = resolve;})
            : Promise.resolve(response({tracks: [{id: 7}]}));
    };
    // Requests while one is loading mark exactly one more run; none is dropped.
    context.loadPendingRaids(); context.loadPendingRaids(); context.loadPendingRaids();
    assert.equal(reads.length, 1); assert.equal(pendingRaidsState.reloadQueued, true);
    answer(response({tracks: [{id: 1}]})); await settle();
    assert.equal(reads.length, 2); assert.deepEqual(plain(pendingRaidsState.tracks), [{id: 7}]);
    assert.equal(pendingRaidsState.isLoading, false);
    // A dismissal confirmed during a read makes it stale: the page reads again.
    reads.length = 0;
    context.loadPendingRaids();
    pendingRaidsState.revision += 1;
    answer(response({tracks: [{id: 8}, {id: 9}]})); await settle();
    assert.equal(reads.length, 2); assert.deepEqual(plain(pendingRaidsState.tracks), [{id: 7}]);
    // The page-load badge read filters staged and recovering rows like the list, and a list
    // read that has started since wins.
    const before = listeners.DOMContentLoaded.length;
    slice("document.addEventListener('DOMContentLoaded', function() {\n    // Fetch pending raid count", '</script>');
    const badge = listeners.DOMContentLoaded[before];
    Object.assign(pendingRaidsState, {loaded: false, isLoading: false, stagedPromotions: [{id: 2, track_uri: uri(2)}]});
    state.sourceRecovery = [{kind: 'raids', resource: 'collection', status: 'failed', body: {track_ids: [3]}}];
    context.workshopRequest = async () => response({tracks: [{id: 1}, {id: 2}, {id: 3}]});
    badge(); await settle();
    assert.deepEqual(plain(pendingRaidsState.tracks), [{id: 1}]);
    pendingRaidsState.tracks = [{id: 5}];
    badge(); await settle();
    assert.deepEqual(plain(pendingRaidsState.tracks), [{id: 5}]);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_archive_list_reads_are_latest_wins_and_hold_an_unknown_append_out(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    Object.assign(context, {archiveInboxLoaded: false});
    slice('function fetchArchiveTracks(', 'function loadArchiveTracks()');
    const answers = [];
    context.workshopRequest = () => new Promise(resolve => {answers.push(resolve);});
    const older = context.fetchArchiveTracks(true), newer = context.fetchArchiveTracks(true);
    // An archive append whose outcome is unknown keeps its tracks out of the list, so they
    // cannot be restored twice; a failed one, proven to have added nothing, does not.
    state.sourceRecovery = [{kind: 'archive', resource: 'archive', status: 'unknown', body: {track_uris: [uri(8)]}},
        {kind: 'archive', resource: 'archive', status: 'failed', body: {track_uris: [uri(9)]}}];
    answers[1](response({tracks: [{uri: uri(9)}, {uri: uri(8)}, {uri: uri(6)}], total: 3}));
    await newer;
    assert.deepEqual(plain(state.archiveTracks), [{uri: uri(6)}, {uri: uri(9)}]);
    // The older read answers last and changes nothing.
    answers[0](response({tracks: [{uri: uri(1)}], total: 1}));
    await older;
    assert.deepEqual(plain(state.archiveTracks), [{uri: uri(6)}, {uri: uri(9)}]);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_a_pairing_that_moves_under_archive_work_keeps_the_work_and_says_so_once(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    Object.assign(context, {renderArchivePanel() {}, loadArchiveTracks() {}, populateArchiveLinkSelect() {}});
    Object.assign(context.archiveState, {isLoaded: true, pairNotice: null});
    slice('function loadArchivePairInfo()', 'function renderArchivePanel()');
    const notice = element('archive-pair-notice');
    notice.classList = {hidden: true, toggle(name, force) {if (name === 'hidden') this.hidden = force;},
        add() {}, remove() {}, contains() {return false;}};
    let written = 0, words = '';
    Object.defineProperty(notice, 'textContent', {configurable: true, get() {return words;},
        set(value) {written++; words = value;}});
    const heard = announcements.length;
    const pairReply = id => response({paired: true, pair: {archive_playlist_id: id}});
    // With nothing staged, a changed pairing simply shows.
    replies.push(pairReply('moved'));
    context.loadArchivePairInfo(); await settle();
    assert.equal(state.archivePair.archive_playlist_id, 'moved'); assert.equal(notice.classList.hidden, true);
    // Staged restorations belong to the pairing they were staged against: when it moves,
    // they are kept and one notice says so.
    state.stagedRestorations = [{uri: uri(4)}];
    replies.push(pairReply('elsewhere'));
    context.loadArchivePairInfo(); await settle();
    assert.deepEqual(plain(state.stagedRestorations), [{uri: uri(4)}]);
    assert.equal(state.archivePair.archive_playlist_id, 'elsewhere');
    assert.equal(notice.classList.hidden, false);
    assert.match(notice.textContent, /archive pairing changed after you staged archive tracks/);
    assert.match(notice.textContent, /kept here/);
    const text = notice.textContent, writes = written;
    // It is said once, through the page's announcer.
    assert.deepEqual(announcements.slice(heard), [text]);
    replies.push(pairReply('elsewhere'));
    context.loadArchivePairInfo(); await settle();
    // Read again with the same words, it is neither written nor said again.
    assert.equal(notice.textContent, text); assert.equal(written, writes);
    assert.equal(announcements.length, heard + 1);
    // The notice goes once the staged work is gone.
    state.stagedRestorations = []; context.renderArchiveNotice();
    assert.equal(notice.classList.hidden, true);
    // A pairing read sees removal under queued archive tracks.
    state.archiveQueue = [uri(9)];
    replies.push(response({paired: false, pair: null}));
    context.loadArchivePairInfo(); await settle();
    assert.equal(notice.classList.hidden, false); assert.match(notice.textContent, /was removed/);
    assert.equal(announcements[announcements.length - 1], notice.textContent);
    assert.deepEqual(plain(state.archiveQueue), [uri(9)]);
    state.archiveQueue = []; context.renderArchiveNotice();
    // An open Save review that named an archive is captured work as well.
    state.reviewMode = 'review'; state.review = {sources: [{kind: 'archive', resource: 'elsewhere'}]};
    replies.push(pairReply('third'));
    context.loadArchivePairInfo(); await settle();
    assert.equal(notice.classList.hidden, false);
    state.reviewMode = null; state.review = null; context.renderArchiveNotice();
    // Latest wins: an older pairing read that answers last never replaces a newer one.
    let older;
    replies.push(() => new Promise(resolve => {older = resolve;}), pairReply('newest'));
    context.loadArchivePairInfo(); context.loadArchivePairInfo(); await settle();
    older(pairReply('stale')); await settle();
    assert.equal(state.archivePair.archive_playlist_id, 'newest');
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


# A native dialog call: confirm(...), window.alert(...), prompt(...).
NATIVE_DIALOG = re.compile(r"(?<![\w$])(?:confirm|alert|prompt)\s*\(")


def test_workshop_uses_no_native_dialogs():
    text = workshop_markup()
    assert not NATIVE_DIALOG.findall(text)
    assert "snapshot-delete-modal" not in text
    # The guard sees the forms it exists to refuse, and none of the shared names.
    assert NATIVE_DIALOG.search("if (!confirm('Remove?')) return;")
    assert NATIVE_DIALOG.search("window.alert ('x')")
    assert not NATIVE_DIALOG.search(
        "AppConfirm.ask({}); confirmManualSnapshot(); askWorkshopConfirm({})"
    )


GROUP_NAMES = {
    "view",
    "pause",
    "draft",
    "save",
    "locks",
    "link",
    "drip-gate",
    "drip-count",
    "sources",
    "pair",
    "pair-unlink",
    "auto-archive",
    "dismiss",
    "routine",
    "create",
}
CONTROL = re.compile(r"<(button|input|select|textarea|a)\b[^>]*>", re.S)


def test_every_static_workshop_control_declares_one_group_or_a_live_key():
    # The tool panels' partial is checked where the template includes it.
    text = workshop_markup()
    markup = text[: text.index('<script src="https://cdn.jsdelivr.net/npm/sortablejs')]
    hub = markup.index("<!-- Empty State — no playlist selected -->")
    markup = markup[:hub] + markup[markup.index("{% endif %}", hub) :]
    # The routine Run controls are reviewed live actions keyed when their routine renders.
    keyed_when_rendered = set()
    for match in CONTROL.finditer(markup):
        tag = match.group(0)
        groups = re.findall(r'data-workshop-compat="([^"]+)"', tag)
        if "data-live-operation-key" in tag or any(
            f'id="{name}"' in tag for name in keyed_when_rendered
        ):
            assert not groups, tag
            continue
        assert len(groups) == 1 and groups[0] in GROUP_NAMES, tag
    # History's saved-draft field and Save write a saved draft (create); Try again only reads.
    tags = [match.group(0) for match in CONTROL.finditer(markup)]
    for control, group in (("saved-draft-name", "create"), ("saved-draft-save-btn", "create")):
        tag = next(tag for tag in tags if f'id="{control}"' in tag)
        assert f'data-workshop-compat="{group}"' in tag, control
    reload = re.search(r'<button[^>]*data-action-click="wk-draft-reload"[^>]*>', markup).group(0)
    assert 'data-workshop-compat="view"' in reload
    # Shared link controls retain static compatibility groups; exact routine controls are
    # rendered and keyed by RoutineManagement rather than this partial.
    for control in (
        "raid-link-unlink-btn",
        "archive-unlink-btn",
    ):
        assert f'id="{control}"' in markup, control
    assert '<div id="workshop-compat-reasons" class="sr-only"></div>' in markup


def test_duplicate_collect_routines_have_distinct_management_and_write_targets(node, db_app):
    """Each collect routine has its own management controls and write target, addressed by exact ID."""
    from tests.test_routine_management_runtime import row

    html = render_page(
        db_app, "schedules.html", path="/schedules",
        routines=[row(5, "target", "raid"), row(8, "target", "raid"), row(7, "target", "rotate")],
        playlists=[{"id": "target", "name": "Target"}],
    )
    run_page(node, html, r"""
const reply = data => ({ok: true, status: 200, json: async () => data});
const page = loadPage(TREE, {path: '/schedules', route(url) {
    if (url.startsWith('/api/schedules/latest-results')) return reply({success: true, results: {}});
    if (url.endsWith('/routines/context')) return reply({success: true, capabilities: {}});
}});
await settle();
const card = id => page.$('.schedule-card[data-schedule-id="' + id + '"]');
assert.equal(page.$$('.schedule-card').length, 3);
assert.equal(card(5).querySelector('.routine-edit').getAttribute('href'),
    '/playlist/target/routines/new?schedule_id=5&from=routines');
assert.equal(card(8).querySelector('.routine-edit').getAttribute('href'),
    '/playlist/target/routines/new?schedule_id=8&from=routines');
assert.equal(card(5).querySelector('.sched-edit-panel'), null);
assert.equal(page.calls.filter(call => call.method !== 'GET').length, 0);
page.replies.push(page.reply({success: true, schedule_id: 8, schedule_revision: 'revision-8b',
    schedule: {id: 8, target_playlist_id: 'target', target_playlist_name: 'Target',
        job_type: 'raid', is_enabled: false}, timing: {text: 'Every day'},
    registration: {status: 'paused'}, state: 'Paused'}));
card(8).querySelector('.routine-toggle').click(); await settle();
const states = page.calls.filter(call => call.url.includes('/state'));
assert.deepEqual(states.map(call => call.url), ['/api/routines/8/state']);
assert.deepEqual(JSON.parse(states[0].body), {desired_enabled: false, expected_revision: 'revision-8'});
assert.match(page.text(card(8)), /Paused/);
assert.match(page.text(card(5)), /Active/);
page.answer = false;
card(5).querySelector('.routine-delete').click(); await settle();
assert.equal(page.calls.filter(call => call.url === '/api/schedules/5').length, 0);
page.answer = true;
page.replies.push(page.reply({success: true}));
card(5).querySelector('.routine-delete').click(); await settle();
assert.equal(page.calls.filter(call => call.url === '/api/schedules/5').length, 1);
assert.equal(card(5), null);
assert.ok(card(8));
""")


def test_busy_exact_routine_blocks_only_its_own_edit_and_delete(node):
    run_node(
        node,
        scenario(r"""
(async () => {
    const controls = controlsForEveryGroup();
    const duplicate = grouped('control-routine-8', 'routine', {scheduleId: '8'});
    raidPanel.status.schedule = {id: 5, job_type: 'raid'};
    const held = await holdRun(routineContext(5, 'raid'), () => startExactRoutine(5));
    assert.equal(barred(controls['routine:5']), true);
    assert.equal(controls['routine:5'].disabled, true);
    assert.equal(barred(duplicate), false);
    assert.equal(duplicate.disabled, false);
    assert.equal(barred(controls.pause), false);
    await held.end();
    assert.equal(barred(controls['routine:5']), false);
    assert.equal(controls['routine:5'].disabled, false);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )


def test_workshop_exact_routine_adapter_holds_navigation_and_keeps_pause_available(node):
    """The routine manager's Workshop request adapter enforces the write and guard boundary."""
    run_node(
        node,
        scenario(r"""
(async () => {
    const revision = state.configRevision;
    let finish;
    replies.push(() => new Promise(resolve => {finish = resolve;}));
    const pending = context.workshopRequest('/api/schedules/8', {method: 'DELETE'}, {group: 'routine:8'});
    await tick();
    assert.equal(state.pendingWrites, 1);
    assert.equal(await context.navigatePlaylist('next'), false);
    assert.deepEqual(assigned, []);
    finish(response({success: true})); await pending;
    assert.equal(state.pendingWrites, 0);
    assert.ok(state.configRevision > revision);
    raidPanel.status.schedule = {id: 5, job_type: 'raid'};
    const held = await holdRun(routineContext(5, 'raid'), () => startExactRoutine(5));
    await assert.rejects(context.workshopRequest('/api/schedules/5', {method: 'DELETE'},
        {group: 'routine:5'}), /Unavailable while this routine runs/);
    replies.push(response({success: true}));
    assert.equal((await context.workshopRequest('/api/routines/5/state', {method: 'PUT'},
        {group: 'pause'})).ok, true);
    replies.push(response({success: true}));
    assert.equal((await context.workshopRequest('/api/schedules/8', {method: 'DELETE'},
        {group: 'routine:8'})).ok, true);
    await held.end();
    state.isSaving = true;
    await assert.rejects(context.workshopRequest('/api/schedules/8', {method: 'DELETE'},
        {group: 'routine:8'}), /while your draft saves to Spotify/);
    replies.push(response({success: true}));
    assert.equal((await context.workshopRequest('/api/routines/8/state', {method: 'PUT'},
        {group: 'pause'})).ok, true);
    state.isSaving = false;
})().catch(e => {console.error(e); process.exitCode = 1;});
"""),
    )
