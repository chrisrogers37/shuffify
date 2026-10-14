"""Live results are heard, summarized and labelled."""

import re
from pathlib import Path

from tests.test_playlist_operations_runtime import node as node
from tests.test_playlist_operations_runtime import run_node
from tests.test_reviewed_executor_actions_runtime import EXECUTOR

ACCESSIBILITY = (
    EXECUTOR
    + """
const said = [];
Object.defineProperty(element('live-operation-announcer'), 'textContent',
    {get() { return said[said.length - 1] || ''; }, set(value) { said.push(value); }});
const all = node => [node, ...node.children.flatMap(all)];
const labelled = (root, attribute) => all(root).filter(el => el.attrs && el.attrs[attribute] !== undefined);
const unknownDrip = () => response(executorResult('drip', {target: 'unknown', collection: 'complete'}), 502);
"""
)


def test_a_run_announces_its_start_and_each_playlist_outcome_once(node):
    run_node(
        node,
        ACCESSIBILITY
        + """
(async () => {
    const drip = await reviewedRun({kind: 'drip', targetId: 'target', trigger: element('trigger')},
        executorContext('drip'), unknownDrip());
    // The review once, the start once, then the target, the collection, the review inbox and
    // the run history, each named separately.
    assert.deepEqual(said, ['Review ready: Add collected tracks now, Target name. Nothing is sent until you run it.',
        'Running: Add collected tracks now, Target name.',
        'Attention: Add collected tracks now, Target name. target name: could not be established; ' +
        'collection name: verified; review inbox: up to date; run history: saved. Do not repeat it.']);
    // Re-renders say nothing; a finished check is announced once.
    client.syncControls(); await client.reconcile(drip.operationId);
    assert.equal(said.length, 4);
    assert.ok(said[3].startsWith('Checked Add collected tracks now, Target name: ' +
        'Current tracks could not be checked.'));
    const verified = executorResult('drip', {}, [{kind: 'pending_promotion', status: 'complete'},
        {kind: 'execution_record', status: 'complete'}]);
    await reviewedRun({kind: 'drip', targetId: 'other', trigger: element('trigger')},
        executorContext('drip', {target: {id: 'other', name: 'Other'}, resources: [res('other', 'target', 'write',
            'track_membership'), res('collection', 'collection', 'write', 'track_membership'),
            res('other', 'inbox', 'write', 'inbox_state')]}), response(verified));
    // A complete run names each playlist and fact too.
    assert.equal(said[said.length - 1], 'Complete: Add collected tracks now, Other. target name: verified; ' +
        'collection name: verified; review inbox: up to date; run history: saved.');
    // A lost reply says the outcome is unknown and warns against repeating it.
    await reviewedRun({kind: 'raid', targetId: 'third', trigger: element('trigger')},
        executorContext('raid', {target: {id: 'third', name: 'Third'}, resources: [res('third', 'target', 'read'),
            res('collection3', 'collection', 'write', 'track_membership'), res('third', 'inbox', 'write',
                'inbox_state')]}), () => {throw new Error('lost');});
    assert.equal(said[said.length - 1],
        'Attention: Collect for review, Third. The outcome could not be established; do not repeat it.');
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_attention_is_summarized_above_the_results_and_named_in_the_footer(node):
    run_node(
        node,
        ACCESSIBILITY
        + """
(async () => {
    const drip = await reviewedRun({kind: 'drip', targetId: 'target', trigger: element('trigger')},
        executorContext('drip'), unknownDrip());
    const summary = element('live-operation-summary');
    assert.equal(summary.hidden, false);
    assert.match(rendered(summary), /Needs attention: .*Add collected tracks now, Target name/);
    const link = all(summary).find(el => el.dataset && el.dataset.liveRow);
    assert.equal(link.href, '#live-operation-' + drip.operationId);
    assert.equal(element('live-operation-footer-status').textContent,
        'Some results need attention; they stay on this page until you leave.');
    assert.equal(element('live-operation-footer-status').hidden, false);
    // Repeated controls carry their row's name.
    const results = element('live-operation-results');
    assert.match(rendered(results), /Run details : Add collected tracks now, Target name/);
    const check = labelled(results, 'data-live-check')[0];
    assert.equal(check.attrs['aria-label'], 'Check current playlist for Add collected tracks now, Target name');
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_a_refusal_is_heard_and_keeps_focus_on_the_heading_and_labels_follow_the_mode(
    node,
):
    run_node(
        node,
        ACCESSIBILITY
        + """
(async () => {
    let focused = null;
    element('live-operation-heading').focus = () => {focused = 'heading';};
    element('live-operation-back').dataset.resultsLabel = 'Back to Workshop';
    replies.push(replyWith(executorContext('drip')));
    const refused = await prepared({kind: 'drip', targetId: 'target', trigger: element('trigger')});
    // During a review both close controls read Cancel.
    assert.equal(element('live-operation-back').textContent, 'Cancel');
    assert.equal(element('live-operation-close').textContent, 'Cancel');
    page.busy = true; focused = null; runReview();
    assert.equal(await refused.done, null);
    assert.equal(focused, 'heading');
    assert.match(said[said.length - 1], /still running/);
    assert.equal(element('live-operation-back').textContent, 'Back to Workshop');
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_reviewing_results_from_the_leave_dialog_returns_to_the_navigation_trigger(
    node,
):
    run_node(
        node,
        ACCESSIBILITY
        + """
(async () => {
    await reviewedRun({kind: 'drip', targetId: 'target', trigger: element('trigger')},
        executorContext('drip'), unknownDrip());
    const settings = element('settings-link');
    const decision = window.AppNavigation.request({kind: 'link', url: '/settings', element: settings}); await tick();
    assert.equal(panels[panels.length - 1].id, 'live-operation-leave');
    element('live-operation-leave-review').click();
    assert.equal(await decision, false); await tick();
    const opened = panels[panels.length - 1];
    assert.equal(opened.id, 'live-operation-panel'); assert.equal(opened.options.trigger, settings);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_unverified_history_and_a_view_that_did_not_update_are_announced(node):
    run_node(
        node,
        ACCESSIBILITY
        + r"""
(async () => {
    const other = (id, name) => executorContext('drip', {target: {id, name}, resources: [
        res(id, 'target', 'write', 'track_membership'), res(id + 'collection', 'collection', 'write',
            'track_membership'), res(id, 'inbox', 'write', 'inbox_state')]});
    // Every playlist verified, but the run's own record is unknown: the announcement says which fact.
    await reviewedRun({kind: 'drip', targetId: 'target', trigger: element('trigger')}, executorContext('drip'),
        response(executorResult('drip', {}, [{kind: 'pending_promotion', status: 'complete'},
            {kind: 'execution_record', status: 'unknown'}])));
    assert.equal(said[said.length - 1], 'Attention: Add collected tracks now, Target name. target name: verified; ' +
        'collection name: verified; review inbox: up to date; run history: could not be verified. Do not repeat it.');
    // A required fact the reply left out could not be verified either.
    await reviewedRun({kind: 'drip', targetId: 'second', trigger: element('trigger')}, other('second', 'Second'),
        response(executorResult('drip', {}, [{kind: 'pending_promotion', status: 'complete'}])));
    assert.match(said[said.length - 1],
        /^Attention: Add collected tracks now, Second\. .*run history: could not be verified\. Do not repeat it\.$/);
    // Verified, but this view could not update: not announced as complete.
    client.configure({getInputRevision: () => revision, getContext: () => page, navigationState: () => nav,
        afterComplete: () => {throw new Error('view');}});
    await reviewedRun({kind: 'drip', targetId: 'third', trigger: element('trigger')}, other('third', 'Third'),
        response(executorResult('drip', {}, [{kind: 'pending_promotion', status: 'complete'},
            {kind: 'execution_record', status: 'complete'}])));
    assert.match(said[said.length - 1], /^Attention: Add collected tracks now, Third\. .*run history: saved\. /);
    assert.match(said[said.length - 1], /This view could not update; check Spotify before another run\.$/);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_review_states_are_heard_once_and_the_summary_waits_out_a_review(node):
    run_node(
        node,
        ACCESSIBILITY
        + r"""
(async () => {
    const drip = await reviewedRun({kind: 'drip', targetId: 'target', trigger: element('trigger')},
        executorContext('drip'), unknownDrip());
    const summary = element('live-operation-summary');
    assert.equal(summary.hidden, false);
    // A review of another playlist hides the summary, whose links lead to hidden rows, and
    // its ready state is heard once, however often the view renders.
    replies.push(replyWith(executorContext('drip', {target: {id: 'other', name: 'Other'}, resources: [
        res('other', 'target', 'write', 'track_membership'), res('collection2', 'collection', 'write',
            'track_membership'), res('other', 'inbox', 'write', 'inbox_state')]})));
    const before = said.length;
    const review = await prepared({kind: 'drip', targetId: 'other', trigger: element('trigger')});
    assert.equal(summary.hidden, true);
    await client.reconcile(drip.operationId); await client.reconcile(drip.operationId);
    assert.deepEqual(said.slice(before).filter(text => text.startsWith('Review')),
        ['Review ready: Add collected tracks now, Other. Nothing is sent until you run it.']);
    window.AppPanels.requestClose('escape'); await review.done;
    assert.equal(summary.hidden, false);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_a_summary_link_keeps_focus_when_the_summary_changes(node):
    run_node(
        node,
        ACCESSIBILITY
        + r"""
(async () => {
    let active = null;
    Object.defineProperty(document, 'activeElement', {get() { return active; }, configurable: true});
    const make = document.createElement;
    document.createElement = tag => Object.assign(make(tag), {focus() { active = this; }});
    const summary = element('live-operation-summary');
    summary.contains = el => all(summary).includes(el);
    summary.querySelector = selector => {
        const id = /data-live-row="(\d+)"/.exec(selector);
        return id ? all(summary).find(el => el.dataset && el.dataset.liveRow === id[1]) || null : null;
    };
    element('live-operation-heading').focus = () => { active = element('live-operation-heading'); };
    const first = await reviewedRun({kind: 'drip', targetId: 'target', trigger: element('trigger')},
        executorContext('drip'), unknownDrip());
    const linkFor = id => all(summary).find(el => el.dataset && el.dataset.liveRow === String(id));
    const link = linkFor(first.operationId);
    // A second run starts (a run moves focus to the heading); while it runs the user goes back
    // to the first result's summary link.
    replies.push(replyWith(executorContext('raid', {target: {id: 'third', name: 'Third'}, resources: [
        res('third', 'target', 'read'), res('collection3', 'collection', 'write', 'track_membership'),
        res('third', 'inbox', 'write', 'inbox_state')]})));
    const review = await prepared({kind: 'raid', targetId: 'third', trigger: element('trigger')}); runReview();
    const handle = await review.done;
    let lose; replies.push(() => new Promise((resolve, reject) => {lose = reject;}));
    const running = client.run(handle); await tick(); await tick();
    active = link;
    // A running result is not listed, so the summary and its focused link are left alone.
    assert.equal(linkFor(first.operationId), link);
    lose(new Error('lost')); await running;
    // The finished run needs attention too: the summary is rebuilt, and focus moves to the
    // rebuilt link for the same row instead of being lost.
    const rebuilt = linkFor(first.operationId);
    assert.notEqual(rebuilt, link);
    assert.equal(active, rebuilt);
    // A re-render that changes nothing leaves the summary, and focus, alone.
    await client.reconcile(first.operationId);
    assert.equal(linkFor(first.operationId), rebuilt); assert.equal(active, rebuilt);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_the_announcer_is_the_one_live_region_of_the_results_view():
    """The status line and the reload notice are visible text; the announcer reads them."""
    macros = (Path(__file__).resolve().parent.parent / "shuffify/templates/macros/app_ui.html").read_text()
    status = re.search(r'<p id="live-operation-status"[^>]*>', macros)[0]
    reload_message = re.search(r'<p id="\{\{ prefix \}\}-reload-message"[^>]*>', macros)[0]
    for tag in (status, reload_message):
        assert "role=" not in tag and "aria-live" not in tag, tag
    announcer = re.search(r'<div id="live-operation-announcer"[^>]*>', macros)[0]
    assert 'aria-live="polite"' in announcer and "data-panel-live" in announcer


def test_no_effect_empty_unknown_and_lock_outcomes_are_announced_as_they_are(node):
    run_node(
        node,
        ACCESSIBILITY
        + r"""
(async () => {
    const other = (id, name) => executorContext('drip', {target: {id, name}, resources: [
        res(id, 'target', 'write', 'track_membership'), res(id + 'collection', 'collection', 'write',
            'track_membership'), res(id, 'inbox', 'write', 'inbox_state')]});
    // A refusal that provably changed nothing says what its row says, not "could not be verified".
    const refused = {success: false, error_code: 'review_changed', message: 'Changed.', operation: {kind: 'drip',
        aggregate_status: 'partial', retryable: false, error_code: 'review_changed', resources: [], bookkeeping: []}};
    await reviewedRun({kind: 'drip', targetId: 'target', trigger: element('trigger')}, executorContext('drip'),
        response(refused, 409));
    assert.equal(said[said.length - 1], 'Not applied: Add collected tracks now, Target name. Nothing was sent ' +
        'because the playlist, its saved history or the reviewed action changed. Review it again to continue.');
    // An unknown reply that lists no playlist still names each one the run was to write, and warns.
    const empty = {success: false, operation: {kind: 'drip', aggregate_status: 'unknown', retryable: false,
        resources: [], bookkeeping: []}};
    await reviewedRun({kind: 'drip', targetId: 'second', trigger: element('trigger')}, other('second', 'Second'),
        response(empty, 502));
    assert.equal(said[said.length - 1], 'Attention: Add collected tracks now, Second. second name: could not be ' +
        'established; secondcollection name: could not be established; review inbox: could not be verified; ' +
        'run history: could not be verified. Do not repeat it.');
    // An unconfirmed playlist lock is named.
    await reviewedRun({kind: 'drip', targetId: 'third', trigger: element('trigger')}, other('third', 'Third'),
        response(executorResult('drip', {}, [{kind: 'pending_promotion', status: 'complete'},
            {kind: 'execution_record', status: 'complete'}, {kind: 'coordination', status: 'unknown'}]), 502));
    assert.match(said[said.length - 1], /playlist lock: could not be verified\. Do not repeat it\.$/);
    // A second review failure after Load review again is heard too, with its reason.
    replies.push(response({success: false, message: 'Spotify is busy.'}, 503));
    await prepared({kind: 'drip', targetId: 'fourth', trigger: element('trigger')});
    replies.push(response({success: false, message: 'Spotify is busy.'}, 503));
    runReview(); await tick(); await tick();
    const failure = 'Review unavailable: Spotify is busy. Nothing was sent.';
    assert.equal(said.filter(text => text.startsWith(failure)).length, 2);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_a_finished_check_and_a_view_update_are_announced(node):
    run_node(
        node,
        ACCESSIBILITY
        + r"""
(async () => {
    let failView = true, updates = 0, finishUpdate = null;
    client.configure({getInputRevision: () => revision, getContext: () => page, navigationState: () => nav,
        afterObservation: () => {
            if (failView) throw new Error('view');
            updates += 1;
            return new Promise(resolve => { finishUpdate = resolve; });
        }});
    const lostPrivacy = {success: false, operation: {kind: 'privacy', aggregate_status: 'unknown', bookkeeping: [],
        resources: [resource('target', 'unknown', {step: 'privacy', verification: 'metadata',
            desired_metadata: {public: false}})]}};
    const record = await reviewedRun({kind: 'privacy', targetId: 'target', body: {public: false},
        trigger: element('trigger')}, reviewContext('privacy'), response(lostPrivacy, 502));
    const match = {success: true, inspection_only: true, public: false, operation: {kind: 'privacy',
        aggregate_status: 'complete', retryable: false, error_code: null, bookkeeping: [],
        resources: [resource('target', 'not_required', {step: 'privacy', dispatched: false, affected_count: 0,
            verification: 'metadata', current_metadata: {public: false}, desired_metadata: {public: false}})]}};
    replies.push(response(match));
    await client.reconcile(record.operationId);
    // The match is heard with the view that could not update; it still needs attention.
    assert.match(said[said.length - 1], /^Checked Make playlist private now, .*: Current visibility/);
    assert.match(said[said.length - 1], /this view could not update\.$/);
    // Updating the view clears the last result needing attention, and says so. A second
    // activation while it runs does nothing: one update, one announcement.
    failView = false;
    const update = {dataset: {liveRefresh: String(record.operationId)}};
    const before = said.length;
    element('live-operation-results').click({target: {closest: () => update}}); await tick();
    const button = all(element('live-operation-results')).find(el => el.attrs && el.attrs['data-live-refresh']);
    assert.equal(button.textContent, 'Updating…'); assert.equal(button.attrs['aria-disabled'], 'true');
    element('live-operation-results').click({target: {closest: () => update}}); await tick();
    assert.equal(updates, 1);
    finishUpdate(); await tick(); await tick();
    assert.equal(updates, 1); assert.equal(said.length, before + 1);
    assert.match(said[said.length - 1], /^View updated: Make playlist private now, .*\. No results need attention\.$/);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )
