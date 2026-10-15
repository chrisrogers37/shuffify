"""The shipped creation callers keep draft/unknown state while an operation settles."""

import pytest

from tests.node_runtime import run_scenario
from tests.test_playlist_setup_runtime import node as node
from tests.test_playlist_setup_runtime import run, setup_context, setup_page
from tests.test_workshop_live_runtime import WORKSHOP
from tests.test_workshop_save_runtime import ROOT


@pytest.mark.parametrize("state,privacy", [("succeeded", True), ("needs_attention", False)])
def test_setup_creation_polls_and_stages_the_known_identity_only_for_later_save(node, db_app, state, privacy):
    page = setup_page(db_app)
    run(
        node,
        page,
        r"""
    await settle();
    const queue = page.window.OperationQueue;
    page.window.OperationQueue = {...queue,
        asset: (url, init, limits) => queue.asset(url, init, {...limits, pollMs: 0})};
    const operation = (state, result = null) => ({id: 17, state, result});
    replies.push(page.reply({success: true, operation_id: 17, operation: operation('queued')}, 202),
        page.reply({success: true, operation: operation('running')}),
        page.reply({success: true, operation: operation('__STATE__', {success: __SUCCESS__, outcome: 'created',
            playlist: {id: 'made17', name: 'Morning Archive'}, privacy_confirmed: __PRIVACY__})}));
    type($('setup-archive-create-name'), 'Morning Archive');
    press($('setup-archive-create')); await new Promise(resolve => setTimeout(resolve, 15)); await settle();
    assert.equal(mutations().length, 1);
    assert.equal(calls.filter(call => call.url === '/api/operations/17').length, 2);
    assert(calls.find(call => call.method === 'POST').init.headers['Idempotency-Key']);
    assert.deepEqual(pendingLabels(), ['Archive: use Morning Archive']);
    assert.equal(calls.some(call => call.method === 'PUT'), false);
    assert.equal(text($('setup-archive-create-status')).includes('private'), __WARNING__);
""".replace("__STATE__", state)
        .replace("__SUCCESS__", str(state == "succeeded").lower())
        .replace("__PRIVACY__", str(privacy).lower())
        .replace("__WARNING__", str(not privacy).lower()),
        setup_context(),
    )


def test_setup_lost_operation_status_keeps_accepted_id_and_blocks_another_post(node, db_app):
    page = setup_page(db_app)
    run(
        node,
        page,
        r"""
    await settle();
    const queue = page.window.OperationQueue;
    page.window.OperationQueue = {...queue,
        asset: (url, init, limits) => queue.asset(url, init, {...limits, pollMs: 0})};
    replies.push(page.reply({success: true, operation_id: 17, operation: {id: 17, state: 'queued'}}, 202),
        () => {throw new Error('status response lost');});
    type($('setup-archive-create-name'), 'Morning Archive');
    press($('setup-archive-create')); await new Promise(resolve => setTimeout(resolve, 15)); await settle();
    assert.equal(disabled($('setup-archive-create')), true);
    assert.equal(text($('setup-archive-create-status')).includes('17'), true);
    press($('setup-archive-create')); await settle();
    assert.equal(mutations().length, 1); assert.deepEqual(pendingLabels(), []);
""",
    )


@pytest.mark.parametrize("path", ["/pair", "/raid-link"])
def test_workshop_creation_holds_boundary_through_poll_and_does_not_repeat_unknown_post(node, path):
    run_scenario(
        node,
        WORKSHOP
        + r"""
vm.runInContext(fs.readFileSync('shuffify/static/js/operation-queue.js', 'utf8'), context);
const queue = window.OperationQueue;
window.OperationQueue = {...queue, asset: (url, options, limits) => queue.asset(url, options, {...limits, pollMs: 0})};
(async () => {
    replies.push(response({success: true, operation_id: 17, operation: {id: 17, state: 'queued'}}, 202),
        () => {throw new Error('status unavailable');});
    const request = () => context.workshopRequest('/api/playlist/target__PATH__',
        {method: 'POST', body: '{"create_new":true}'}, {group: 'pair'});
    await assert.rejects(request(), error => error.operationId === 17 && error.code === 'creation_unknown');
    assert.equal(state.pendingWrites, 0);
    replies.push(() => {throw new Error('status unavailable again');});
    await assert.rejects(request(), error => error.operationId === 17);
    assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
    assert.equal(calls.filter(call => call.url === '/api/operations/17').length, 2);
})().catch(error => {console.error(error); process.exit(1);});
""".replace("__PATH__", path),
        ROOT,
    )
