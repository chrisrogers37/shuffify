"""Creation polling retains uncertain intent and never repeats a provider-creating POST."""

from pathlib import Path

from tests.node_runtime import run_scenario
from tests.test_operation_queue_runtime import HARNESS
from tests.test_operation_queue_runtime import node as node

ROOT = Path(__file__).resolve().parents[1]


def test_asset_waits_for_one_accepted_operation_and_db200_remains_immediate(node):
    run_scenario(
        node,
        HARNESS
        + """
(async () => {
    window.crypto = {randomUUID: () => 'asset-key'};
    let posts = 0;
    window.fetch = async (url, options = {}) => {
        calls.push({url, options});
        if (options.method === 'POST') {
            posts += 1;
            assert.equal(options.headers['Idempotency-Key'], 'asset-key');
            return {ok: true, status: 202, json: async () => accepted};
        }
        return {ok: true, json: async () => ({success: true,
            operation: operation('succeeded', {success: true, outcome: 'created', playlist: {id: 'made'}})})};
    };
    const result = await window.OperationQueue.asset('/create', {method: 'POST', body: '{}'}, {pollMs: 0});
    assert.equal(result.ok, true); assert.equal(result.operationId, 17); assert.equal(result.data.playlist.id, 'made');
    assert.equal(posts, 1); assert.equal(calls.length, 2); assert.equal(calls[1].url, '/api/operations/17');
    window.fetch = async () => ({ok: true, status: 200, json: async () => ({success: true, stored: true})});
    const stored = await window.OperationQueue.asset('/local', {method: 'POST', body: '{}'});
    assert.equal(stored.data.stored, true); assert.equal(stored.status, 200);
})().catch(error => {console.error(error); process.exit(1);});
""",
        ROOT,
    )


def test_unknown_accepted_and_lost_initial_replies_retain_ids_and_never_post_again(node):
    run_scenario(
        node,
        HARNESS
        + """
(async () => {
    let posts = 0;
    window.fetch = async (url, options = {}) => {
        if (options.method === 'POST') { posts += 1; return {ok: true, status: 202, json: async () => accepted}; }
        throw new Error('status unavailable');
    };
    const first = await window.OperationQueue.asset('/create', {method: 'POST', body: '{}'}, {pollMs: 0});
    assert.equal(first.answered, false); assert.equal(first.operationId, 17);
    const second = await window.OperationQueue.asset('/create', {method: 'POST', body: '{}'}, {pollMs: 0});
    assert.equal(second.operationId, 17); assert.equal(posts, 1);
    window.fetch = async () => {posts += 1; throw new Error('initial reply lost');};
    const unknown = await window.OperationQueue.asset('/other-create', {method: 'POST', body: '{}'});
    assert.equal(unknown.answered, false);
    await window.OperationQueue.asset('/other-create', {method: 'POST', body: '{}'});
    assert.equal(posts, 2);
})().catch(error => {console.error(error); process.exit(1);});
""",
        ROOT,
    )


def test_known_orphan_identity_is_kept_and_another_press_only_reads_same_operation(node):
    run_scenario(
        node,
        HARNESS
        + """
(async () => {
    let posts = 0, gets = 0;
    window.fetch = async (url, options = {}) => {
        if (options.method === 'POST') {posts += 1; return {ok: true, status: 202, json: async () => accepted};}
        gets += 1;
        return {ok: true, json: async () => ({success: true, operation: operation('needs_attention',
            {success: false, outcome: 'created', playlist: {id: 'known-orphan', name: 'Known'},
                privacy_confirmed: false, message: 'Privacy needs review.'})})};
    };
    const result = await window.OperationQueue.asset('/create', {method: 'POST', body: '{}'}, {pollMs: 0});
    assert.equal(result.ok, false); assert.equal(result.data.playlist.id, 'known-orphan');
    assert.equal(result.operationId, 17);
    await window.OperationQueue.asset('/create', {method: 'POST', body: '{}'}, {pollMs: 0});
    assert.equal(posts, 1); assert.equal(gets, 2);
})().catch(error => {console.error(error); process.exit(1);});
""",
        ROOT,
    )
