"""The inbox consumes exact DB receipts while retaining uncertain queued dismissals."""

from tests.node_runtime import run_scenario
from tests.test_operation_queue_runtime import HARNESS
from tests.test_operation_queue_runtime import node as node
from tests.test_workshop_live_runtime import WORKSHOP
from tests.test_workshop_save_runtime import ROOT


def test_dismissal_queue_reuses_one_operation_even_when_source_cleanup_failed(node):
    run_scenario(
        node,
        HARNESS
        + r"""
(async () => {
    let posts = 0;
    window.fetch = async (url, options = {}) => {
        if (options.method === 'POST') {
            posts++; return {ok: true, status: 202, json: async () => accepted};
        }
        return {ok: true, json: async () => ({success: true, operation: operation('failed',
            {success: false, database_status: 'complete', dismissed_ids: [1], dismissed_count: 1,
             source_status: 'failed', message: 'Inbox dismissed; source cleanup failed.'})})};
    };
    const options = {method: 'POST', body: '{"track_ids":[1]}'};
    const first = await window.OperationQueue.dismiss('/dismiss', options, {pollMs: 0});
    assert.equal(first.data.dismissed_count, 1); assert.equal(first.operationId, 17);
    const second = await window.OperationQueue.dismiss('/dismiss', options, {pollMs: 0});
    assert.equal(second.data.dismissed_count, 1); assert.equal(posts, 1);
})().catch(error => {console.error(error);process.exit(1);});
""",
        ROOT,
    )


def test_workshop_retains_confirmed_dismissal_even_if_optional_source_cleanup_failed(node):
    run_scenario(
        node,
        WORKSHOP
        + r"""
vm.runInContext(fs.readFileSync('shuffify/static/js/operation-queue.js', 'utf8'), context);
const queue = window.OperationQueue;
window.OperationQueue = {...queue, dismiss: (url, init, limits) => queue.dismiss(url, init, {...limits, pollMs: 0})};
slice('function promotePendingRaid(', 'function promoteAllPendingRaids(');
(async () => {
    pendingRaidsState.revision = 0;
    pendingRaidsState.tracks = [{id: 1, track_uri: uri(4)}, {id: 2, track_uri: uri(5)}];
    replies.push(response({success: true, operation_id: 17, operation: {id: 17, state: 'queued'}}, 202),
        response({success: true, operation: {id: 17, state: 'failed', result: {
            success: false, database_status: 'complete', dismissed_count: 1, dismissed_ids: [1],
            source_status: 'failed', source_removed_count: 0, message: 'Inbox dismissed; collection needs review.'}}}));
    const completed = await context.sendPendingDismissal([1, 2]);
    assert.equal(completed.count, 1); assert.match(completed.warning, /collection needs review/);
    assert.deepEqual(plain(pendingRaidsState.tracks.map(row => row.id)), [2]);
    assert.equal(pendingRaidsState.revision, 1); assert.equal(pendingRaidsState.dismissing.size, 0);
    assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
    assert.equal(calls.filter(call => call.url === '/api/operations/17').length, 1);
})().catch(error => {console.error(error);process.exit(1);});
""",
        ROOT,
    )


def test_unknown_dismissal_keeps_rows_out_of_staging_and_never_reposts(node):
    run_scenario(
        node,
        WORKSHOP
        + r"""
vm.runInContext(fs.readFileSync('shuffify/static/js/operation-queue.js', 'utf8'), context);
const queue = window.OperationQueue;
window.OperationQueue = {...queue, dismiss: (url, init, limits) => queue.dismiss(url, init, {...limits, pollMs: 0})};
slice('function promotePendingRaid(', 'function promoteAllPendingRaids(');
(async () => {
    pendingRaidsState.tracks = [{id: 1, track_uri: uri(4)}];
    replies.push(response({success: true, operation_id: 17, operation: {id: 17, state: 'queued'}}, 202),
        () => {throw new Error('status lost');});
    await assert.rejects(context.sendPendingDismissal([1]), error => error.operationId === 17);
    assert.equal(pendingRaidsState.dismissing.has(1), true);
    context.promotePendingRaid(1, pendingRaidsState.tracks[0]);
    assert.deepEqual(plain(pendingRaidsState.stagedPromotions), []);
    replies.push(() => {throw new Error('status lost again');});
    await assert.rejects(context.sendPendingDismissal([1]), error => error.operationId === 17);
    assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
    assert.equal(calls.filter(call => call.url === '/api/operations/17').length, 2);
})().catch(error => {console.error(error);process.exit(1);});
""",
        ROOT,
    )


def test_unconfirmed_required_receipt_keeps_rows_held_and_reuses_its_accepted_id(node):
    run_scenario(
        node,
        WORKSHOP
        + r"""
vm.runInContext(fs.readFileSync('shuffify/static/js/operation-queue.js', 'utf8'), context);
const queue = window.OperationQueue;
window.OperationQueue = {...queue, dismiss: (url, init, limits) => queue.dismiss(url, init, {...limits, pollMs: 0})};
slice('function promotePendingRaid(', 'function promoteAllPendingRaids(');
(async () => {
    pendingRaidsState.tracks = [{id: 1, track_uri: uri(4)}];
    const terminal = () => response({success: true, operation: {id: 17, state: 'needs_attention', result: {
        success: false, database_status: 'unknown', dismissed_count: null, dismissed_ids: null,
        source_status: 'not_started', message: 'The dismissal receipt could not be confirmed.'}}});
    replies.push(response({success: true, operation_id: 17, operation: {id: 17, state: 'queued'}}, 202), terminal());
    await assert.rejects(context.sendPendingDismissal([1]), error => error.operationId === 17);
    assert.equal(pendingRaidsState.dismissing.has(1), true);
    replies.push(terminal());
    await assert.rejects(context.sendPendingDismissal([1]), error => error.operationId === 17);
    assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
    assert.equal(pendingRaidsState.tracks.length, 1);
})().catch(error => {console.error(error);process.exit(1);});
""",
        ROOT,
    )
