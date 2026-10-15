"""Queued page writes wait for their owned result and never repeat an admitted write."""

import shutil
from pathlib import Path

import pytest

from tests.node_runtime import run_scenario
from tests.test_workshop_save_runtime import CLIENT as WORKSHOP_CLIENT

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is needed for queued operation page checks")
    return executable


HARNESS = """
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const calls = [], replies = [];
const window = {fetch: async (url, options) => {
    calls.push({url, options});
    const body = replies.shift();
    if (!body) throw new Error('No reply');
    return {ok: true, json: async () => body};
}};
const context = {window, AbortController, setTimeout, clearTimeout, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync('shuffify/static/js/operation-queue.js', 'utf8'), context);
const operation = (state, result = null, id = 17) => ({id, kind: 'workshop_commit', state, targets: ['playlist'],
    started_at: null, completed_at: null, progress: {}, result,
    recovery: {available: false, action: null, reason: null}});
const accepted = {success: true, operation_id: 17, operation: operation('queued')};
"""


def test_poll_waits_through_running_and_compensation_for_the_same_operation(node):
    run_scenario(node, HARNESS + """
(async () => {
    replies.push({success: true, operation: operation('running')},
        {success: true, operation: operation('compensating')},
        {success: true, operation: operation('rolled_back',
            {success: false, message: 'Restored', target_status: 'not_written'})});
    const settled = await window.OperationQueue.poll(accepted, {pollMs: 0});
    assert.equal(settled.ok, false);
    assert.equal(settled.data.message, 'Restored');
    assert.equal(calls.length, 3);
    assert(calls.every(call => call.url === '/api/operations/17' && !call.options.method
        && call.options.cache === 'no-store'));
})().catch(error => {console.error(error); process.exit(1);});
""", ROOT)


def test_foreign_or_unavailable_status_never_becomes_success_or_repeats_a_write(node):
    run_scenario(node, HARNESS + """
(async () => {
    replies.push({success: true, operation: operation('succeeded', {success: true}, 99)});
    await assert.rejects(window.OperationQueue.poll(accepted, {pollMs: 0}), error => error.operationId === 17);
    assert.equal(calls.length, 1);
    const controller = new AbortController(); controller.abort();
    await assert.rejects(window.OperationQueue.poll(accepted, {signal: controller.signal}),
        error => error.operationId === 17);
    assert.equal(calls.length, 1);
})().catch(error => {console.error(error); process.exit(1);});
""", ROOT)


def test_workshop_submit_keeps_original_draft_until_its_queued_result_is_confirmed(node):
    run_scenario(node, WORKSHOP_CLIENT + """
vm.runInContext(fs.readFileSync('shuffify/static/js/operation-queue.js', 'utf8'), context);
const polling = window.OperationQueue;
window.OperationQueue = {requestKey: () => 'example-key',
    poll: (body, options) => polling.poll(body, {...options, pollMs: 0})};
(async () => {
    const desired = ['b', 'a', 'a'];
    const op = (state, result = null) => ({id: 17, state, result});
    replies.push(response({success: true, operation_id: 17, operation: op('queued')}, 202),
        response({success: true, operation: op('running')}),
        response({success: true, operation: op('succeeded',
            {success: true, target_status: 'saved', saved_track_uris: desired})}));
    const result = await client.submit({baselineTrackUris: ['a', 'a', 'b'], trackUris: desired});
    assert.equal(result.success, true);
    assert.deepEqual(plain(result.saved_track_uris), desired);
    assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
    assert.equal(calls[0].options.headers['Idempotency-Key'], 'example-key');
    assert(calls.slice(1).every(call => call.url === '/api/operations/17'));
})().catch(error => {console.error(error); process.exit(1);});
""", ROOT)
