"""Reviewed live results: one dispatch, honest classification and named, compact rendering."""

import pytest

from tests.test_playlist_operations_runtime import CLIENT, run_node
from tests.test_playlist_operations_runtime import node as node
from tests.test_reviewed_executor_actions_runtime import EXECUTOR_CONTEXTS
from tests.test_reviewed_live_actions_runtime import REVIEW_FIXTURE

RESULTS = (
    CLIENT
    + REVIEW_FIXTURE
    + EXECUTOR_CONTEXTS
    + """
let refreshed = 0;
client.configure({getInputRevision: () => 0, afterComplete: () => {refreshed++;}});
// Review an executor action, then answer its single Run with `reply`.
async function ranWith(reply, kind = 'drip', context) {
    replies.push(replyWith(context || executorContext(kind)));
    const review = client.prepare({kind, targetId: 'target', trigger: element('run')}); await tick(); await tick();
    runReview(); const handle = await review;
    replies.push(reply);
    return client.run(handle);
}
// A verified Drip also proves its required inbox and run-history bookkeeping.
const done = dto => {
    dto.operation.bookkeeping = dto.operation.bookkeeping.concat([{kind: 'pending_promotion', status: 'complete'},
        {kind: 'execution_record', status: 'complete'}]);
    return dto;
};
const visibleText = row => row.children.filter(child => child.tagName !== 'details').map(rendered).join(' ');
"""
)


def test_busy_rejects_a_second_review_and_a_handle_dispatches_once(node):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    let finish;
    replies.push(replyWith(executorContext('drip')));
    const review = client.prepare({kind: 'drip', targetId: 'target', trigger: element('run')});
    await tick(); await tick();
    runReview(); const handle = await review;
    replies.push(() => new Promise(resolve => {finish = resolve;}));
    const first = client.run(handle); await tick();
    assert.equal(client.isBusy(), true);
    assert.equal(await client.prepare({kind: 'drip', targetId: 'target', trigger: element('run')}), null);
    assert.equal(await client.run(handle), null); assert.equal(calls.length, 2);
    finish(response(done(outcome()))); const result = await first;
    assert.equal(result.complete, true); assert.equal(refreshed, 1); assert.equal(client.isBusy(), false);
    assert.deepEqual(JSON.parse(calls[1].options.body), {reviewed_scope: SCOPE});
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize("mode", ["lost", "timeout", "malformed", "false_success"])
def test_unknown_never_replays_or_refreshes_and_clears_busy(node, mode):
    run_node(
        node,
        RESULTS
        + f"""
(async () => {{
    client.configure({{getInputRevision: () => 0, timeoutMs: 5,
        afterComplete: () => {{refreshed++;}}}});
    const mode = '{mode}';
    const reply = {{lost: () => {{throw new Error('lost response');}}, timeout: () => new Promise(() => {{}}),
        malformed: response({{success: true}}),
        false_success: response(done(outcome('complete', [resource('target', 'unknown')])))}}[mode];
    const result = await ranWith(reply);
    assert.equal(result.aggregate, 'unknown'); assert.equal(result.complete, false);
    assert.equal(result.blocked, true); assert.equal(client.isBusy(), false);
    assert.equal(await client.prepare({{kind: 'drip', targetId: 'target', trigger: element('run')}}), null);
    assert.equal(calls.length, 2); assert.equal(refreshed, 0); assert.equal(client.hasAttention(), true);
    let warned = false; listeners.beforeunload[0]({{preventDefault() {{warned = true;}}}});
    assert.equal(warned, true);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_named_partial_composite_and_exact_child_execution_identity_survive_non2xx(
    node,
):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    const data = outcome('unknown', [resource('collection'), resource('target', 'unknown')]);
    delete data.execution_id; delete data.started_at; delete data.execution_state;
    data.operation.kind = 'raid_and_drip';
    data.operation.resources[1].desired_track_uris = [uri(1), uri(1), uri(2)];
    data.operation.steps = [{step: 'raid', execution_id: 8, status: 'success', execution_state: 'recorded'},
        {step: 'drip', execution_id: 9, status: 'failed', execution_state: 'recorded'}];
    data.operation.bookkeeping = [{step: 'raid', kind: 'pending_staging', status: 'complete'}];
    const record = await ranWith(response(data, 502), 'raid_and_drip');
    assert.deepEqual(plain(record.response), data);
    assert.deepEqual(plain(record.operation.resources[1].desired_track_uris), [uri(1), uri(1), uri(2)]);
    const text = rendered(element('live-operation-results'));
    for (const expected of ['collection name', 'Complete — verified', 'target name',
        'Unknown — effect not established', 'Execution 8', 'Execution 9', 'Review inbox: saved']) {
        assert.equal(text.includes(expected), true, expected);
    }
    assert.equal(record.blocked, true);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_known_csrf_rejection_is_known_not_started_and_needs_only_a_fresh_review(node):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    const data = outcome('partial', [], {execution_id: null, started_at: null,
        execution_state: 'not_started', message: 'Session verification failed. This attempt applied no changes.'});
    data.operation.error_code = 'session_verification_expired';
    const result = await ranWith(response(data, 400));
    assert.equal(result.aggregate, 'partial'); assert.equal(result.noEffect, true); assert.equal(result.blocked, false);
    assert.equal(result.operation.error_code, 'session_verification_expired');
    assert.equal(result.errorCode, undefined); assert.equal(result.response.execution_id, null);
    assert.match(rendered(element('live-operation-results')), /Execution was not started/);
    assert.match(result.message, /This attempt did not run/);
    assert.equal(result.message.includes('unknown'), false);
    replies.push(replyWith(executorContext('drip')));
    const again = client.prepare({kind: 'drip', targetId: 'target', trigger: element('run')});
    await tick(); await tick();
    assert.equal(element('live-operation-heading').textContent, 'Review: Add collected tracks now');
    window.AppPanels.requestClose('escape'); await again;
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_proven_recoverable_rejection_allows_only_a_new_reviewed_run(node):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    const data = outcome('partial', [resource('target', 'failed', {dispatched: true, affected_count: 0})]);
    data.operation.retryable = true;
    data.operation.bookkeeping = [{step: 'drip', kind: 'execution_record', status: 'complete', error_code: null}];
    const failed = await ranWith(response(data, 502));
    assert.equal(failed.blocked, false); assert.equal(failed.noEffect, true);
    assert.match(failed.message, /This attempt applied no playlist changes/);
    assert.match(failed.message, /A new review will check current conditions again/);
    assert.equal(failed.response.execution_id, 917);
    assert.match(rendered(element('live-operation-results')), /Run history: saved/);
    const complete = await ranWith(response(done(outcome())));
    assert.equal(complete.complete, true);
    assert.equal(calls.filter(call => call.url.endsWith('/drip-now')).length, 2);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_controls_stay_disabled_while_busy_and_after_an_unknown_result(node):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    // Pages key each live control statically; the controller never stamps triggers.
    const button = element('run'); button.dataset.liveOperationKey = 'drip:target'; let savedDisabled;
    client.configure({getInputRevision: () => 0, onBusy: busy => {
        if (busy) {savedDisabled = button.disabled; button.disabled = true;}
        else button.disabled = savedDisabled;
    }});
    await ranWith(response(done(outcome())));
    assert.equal(button.disabled, false);
    await ranWith(() => {throw new Error('lost');});
    assert.equal(button.disabled, true);
    button.disabled = false; client.syncControls(); assert.equal(button.disabled, true);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_latest_busy_and_attention_result_first_with_older_results_and_stable_ids(node):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    await ranWith(response(done(outcome())), 'drip', executorContext('drip', {label: 'First run'}));
    await ranWith(response(done(outcome())), 'drip', executorContext('drip', {label: 'Second run'}));
    let finish;
    element('live-operation-results').parentElement = {scrollTop: 480};
    const latest = ranWith(() => new Promise(resolve => {finish = resolve;}), 'drip',
        executorContext('drip', {label: 'Latest run'}));
    await tick(); await tick(); await tick();
    const list = element('live-operation-results');
    assert.equal(list.parentElement.scrollTop, 0);
    assert.deepEqual(list.children.map(row => row.id), ['live-operation-2', 'live-operation-1', 'live-operation-0']);
    assert.match(rendered(list.children[0]), /Latest run.*Running/);
    assert.match(rendered(list.children[1]), /Second run.*Complete/);
    finish(response(outcome('unknown', [resource('target', 'unknown')]), 502)); await latest;
    assert.match(rendered(list.children[0]), /Latest run.*Attention/);
    assert.equal(client.getRecords()[0].intent.label, 'First run');
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_compact_results_keep_effects_and_failed_app_state_visible_with_native_run_details(
    node,
):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    const data = outcome('partial');
    data.operation.bookkeeping = [
        {kind: 'execution_record', status: 'complete'}, {kind: 'pending_promotion', status: 'failed'}];
    await ranWith(response(data, 502));
    const row = element('live-operation-results').children[0];
    const details = row.children.filter(child => child.tagName === 'details');
    assert.equal(details.length, 1); assert.equal(details[0].children[0].textContent, 'Run details');
    const visible = visibleText(row);
    for (const value of ['Add collected tracks now', 'Attention', 'target name', 'Complete — verified',
        'Affected tracks: 2', 'Review inbox status: not saved by this attempt']) {
        assert.equal(visible.includes(value), true, value);
    }
    for (const value of ['Execution 917', 'complete playlist order', 'Run history: saved']) {
        assert.equal(visible.includes(value), false, value);
        assert.equal(rendered(details[0]).includes(value), true, value);
    }
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_verified_composite_restoration_retains_named_effects_and_stays_in_attention(node):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    const data = outcome('partial', [resource('collection', 'restored', {step: 'raid'}),
        resource('target', 'restored')]);
    data.operation.kind = 'raid_and_drip';
    data.operation.bookkeeping = [
        {step: 'raid', kind: 'pending_stage', status: 'restored', error_code: null},
        {step: 'drip', kind: 'pending_promotion', status: 'restored', error_code: null},
        {step: 'drip', kind: 'execution_record', status: 'complete', error_code: null}];
    data.operation.steps = [{step: 'raid', status: 'complete'}, {step: 'drip', status: 'failed'}];
    const result = await ranWith(response(data, 502), 'raid_and_drip');
    assert.equal(result.aggregate, 'partial'); assert.equal(result.complete, false);
    // Every effect is known (a verified restoration included), so nothing stays claimed; the run stays in
    // Attention until the page is left.
    assert.deepEqual(plain(result.claims), []); assert.equal(result.blocked, false);
    assert.equal(result.attention, true); assert.equal(result.settled, false);
    const visible = visibleText(element('live-operation-results').children[0]);
    for (const value of ['Attention — this action did not fully complete', 'collection name', 'target name',
        'Restored — baseline verified', 'Review inbox: restored previous app state — verified']) {
        assert.equal(visible.includes(value), true, value);
    }
    // Every fact is known, so the copy says what happened instead of warning against a repeat.
    assert.match(result.message, /did not fully complete.*fresh review/);
    // Another run starts only from a fresh review of the current state.
    replies.push(replyWith(executorContext('raid_and_drip')));
    const again = client.prepare({kind: 'raid_and_drip', targetId: 'target', trigger: element('run')});
    await tick(); await tick();
    assert.equal(element('live-operation-heading').textContent, 'Review: Collect and add tracks now');
    await window.AppPanels.requestClose('escape'); assert.equal(await again, null);
    assert.equal(calls.filter(call => call.url.endsWith('/raid-and-drip')).length, 1);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize("case", ["unknown_receipt", "applied_inbox", "status_alias"])
def test_history_receipt_exemption_does_not_hide_unknown_or_applied_app_work(
    node, case
):
    run_node(
        node,
        RESULTS
        + f"""
(async () => {{
    const mode = '{case}';
    const data = outcome(mode === 'unknown_receipt' ? 'unknown' : 'partial',
        [resource('target', 'failed', {{dispatched: true, affected_count: 0}})]);
    data.operation.bookkeeping = [{{step: 'drip', kind: 'execution_record',
        status: mode === 'unknown_receipt' ? 'unknown' : 'complete'}}];
    if (mode !== 'unknown_receipt') data.operation.bookkeeping.push({{step: 'drip',
        kind: mode === 'applied_inbox' ? 'pending_stage' : 'execution_status', status: 'complete'}});
    // Even a retryable flag cannot authorize replay of unresolved or applied work.
    data.operation.retryable = true;
    const result = await ranWith(response(data, 502));
    assert.equal(result.complete, false); assert.equal(result.blocked, true); assert.equal(result.noEffect, false);
    assert.equal(result.message.includes('applied no playlist changes'), false);
    if (mode === 'unknown_receipt') {{
        assert.equal(result.aggregate, 'unknown');
        assert.match(rendered(element('live-operation-results')), /Run history: could not be verified/);
    }}
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_executor_wait_budget_allows_a_long_run_and_still_has_a_finite_deadline(node):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    const timers = [];
    const realTimeout = context.setTimeout;
    context.setTimeout = (callback, delay) => {timers.push(delay); return realTimeout(() => {}, 0);};
    context.clearTimeout = () => {};
    let finish;
    const run = ranWith(() => new Promise(resolve => {finish = resolve;}));
    await tick(); await tick(); await tick();
    assert.deepEqual(timers, [30000, 300000]);
    finish(response(done(outcome()))); assert.equal((await run).complete, true);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


@pytest.mark.parametrize(
    "status,label",
    [
        ("not_started", "did not run"),
        ("unknown", "outcome unknown"),
        ("restored", "restored — verified"),
        ("partial", "partly completed"),
    ],
)
def test_composite_incomplete_step_summary_preserves_its_actual_certainty(
    node, status, label
):
    run_node(
        node,
        RESULTS
        + f"""
(async () => {{
    const data = outcome('{"unknown" if status == "unknown" else "partial"}');
    data.operation.steps = [{{step: 'drip', status: '{status}'}}];
    const record = await ranWith(response(data, 502));
    const visible = visibleText(element('live-operation-results').children[0]);
    assert.equal(visible.includes('Add collected tracks · {label}'), true, visible);
    assert.equal(record.operation.steps[0].status, '{status}'); assert.equal(record.complete, false);
}})().catch(e => {{console.error(e); process.exitCode = 1;}});
""",
    )


def test_mixed_discovery_read_failure_preserves_healthy_effects_and_names_failed_ids_safely(
    node,
):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    const data = outcome('partial', [resource('collection', 'complete', {step: 'raid'})]);
    data.operation.kind = 'raid';
    data.operation.bookkeeping = [{step: 'raid', kind: 'pending_stage', status: 'complete'},
        {step: 'raid', kind: 'source_resolution', status: 'failed', error_code: 'source_resolution_failed',
            failed_sources: [
                {source_id: 7, source_playlist_id: 'unavailable123', status: 'failed',
                    error_code: 'source_resolution_failed'},
                {source_id: 8, source_playlist_id: '', status: 'failed', error_code: 'source_resolution_incomplete'},
                {source_id: 9, source_playlist_id: 'javascript:alert(1)', status: 'failed',
                    error_code: 'source_resolution_failed', error_message: 'DO NOT DISPLAY UPSTREAM EXCEPTION'}]}];
    const result = await ranWith(response(data, 502), 'raid');
    const row = element('live-operation-results').children[0];
    const visible = visibleText(row);
    assert.equal(result.aggregate, 'partial'); assert.equal(result.complete, false);
    assert.deepEqual(plain(result.response), data);
    assert.match(visible, /collection name.*Complete — verified/);
    assert.match(visible, /Some discovery sources could not be read/);
    assert.match(visible, /Discovery source ID: 8/);
    assert.equal(visible.includes('DO NOT DISPLAY'), false);
    assert.equal(row.children.some(child => child.href === 'https://open.spotify.com/playlist/unavailable123'), true);
    assert.equal(row.children.some(child => child.href && child.href.includes('javascript')), false);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_privacy_non2xx_keeps_its_facts_without_execution_or_track_counts(node):
    run_node(
        node,
        RESULTS
        + """
(async () => {
    replies.push(contextReply('privacy'));
    const review = client.prepare({kind: 'privacy', targetId: 'target', body: {public: false}, trigger: element('p')});
    await tick(); await tick(); runReview(); const handle = await review;
    const data = {success: false, operation: {kind: 'privacy', aggregate_status: 'unknown', retryable: false,
        resources: [resource('target', 'unknown', {step: 'privacy', verification: 'metadata',
            baseline_metadata: {public: true}, desired_metadata: {public: false}})], bookkeeping: []}};
    replies.push(response(data, 502));
    const record = await client.run(handle);
    assert.deepEqual(plain(record.response), data); assert.equal(Object.isFrozen(record.intent), true);
    const text = rendered(element('live-operation-results'));
    assert.match(text, /Change playlist visibility/); assert.doesNotMatch(text, /Execution|Affected tracks/);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )
