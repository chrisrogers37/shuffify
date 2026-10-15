"""Shared Node harness for the live-operation controller, plus its handle-only run check."""

import shutil
from pathlib import Path

import pytest

from tests.node_runtime import run_scenario

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is needed for immediate-operation runtime checks")
    return executable


def run_node(node, script):
    run_scenario(node, script, ROOT)


CLIENT = """
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const calls = [], replies = [], listeners = {}, panels = [], elements = new Map();
function element(id) {
    if (!elements.has(id)) elements.set(id, {id, children: [], dataset: {}, attrs: {}, disabled: false,
        textContent: '', className: '', classList: {contains() {return false;}, add() {}, remove() {}, toggle() {}},
        appendChild(child) {this.children.push(child);}, replaceChildren() {this.children = [];},
        setAttribute(name, value) {this.attrs[name] = value;}, hasAttribute(name) {return name in this.attrs;},
        getAttribute(name) {return this.attrs[name];}, removeAttribute(name) {delete this.attrs[name];},
        addEventListener(type, handler) {this[type] = handler;}});
    return elements.get(id);
}
const document = {getElementById: element,
    createElement: tag => Object.assign(element(tag + '-' + elements.size), {tagName: tag}),
    querySelectorAll: () => [...elements.values()].filter(el => el.dataset.liveOperationKey),
    querySelector: () => null,
    addEventListener(type, handler) {(listeners[type] ||= []).push(handler);}};
const window = {document, addEventListener(type, handler) {(listeners[type] ||= []).push(handler);},
    fetch: async (url, options) => {calls.push({url, options}); const reply = replies.shift();
        if (!reply) throw new Error('No reply'); return typeof reply === 'function' ? reply() : reply;},
    AppPanels: {open: async (id, options) => {panels.push({id, options}); return true;}}};
const context = {window, document, AbortController, FormData, setTimeout, clearTimeout, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync('shuffify/static/js/playlist-operations.js', 'utf8'), context);
const client = window.PlaylistOperations;
const plain = value => JSON.parse(JSON.stringify(value));
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data,
    headers: {get: () => 'application/json'}, clone() { return this; }, arrayBuffer: async () => new ArrayBuffer(0)});
const resource = (id, status = 'complete', extra = {}) => Object.assign({resource: id, resource_name: id + ' name',
    step: 'drip', mutation_type: 'track_membership', status, dispatched: true,
    affected_count: status === 'complete' ? 2 : null, retryable: false, verification: 'ordered'}, extra);
const outcome = (status = 'complete', resources = [resource('target')], extra = {}) => Object.assign({
    success: status === 'complete', execution_id: 917, started_at: '2026-10-11T15:45:00Z',
    execution_state: 'recorded', operation: {kind: 'drip', aggregate_status: status,
        retryable: false, resources, bookkeeping: []}}, extra);
const rendered = node => [node.textContent, ...node.children.map(rendered)].join(' ');
const tick = () => new Promise(resolve => setImmediate(resolve));
"""


def test_run_accepts_only_prepared_handles_and_needs_no_mode_flag(node):
    run_node(
        node,
        CLIENT
        + """
(async () => {
    client.configure({});
    const descriptor = {key: 'drip:target', url: '/api/playlist/target/drip-now', label: 'Drip now',
        targetId: 'target', targetName: 'Target name', body: {}, trigger: element('run')};
    assert.equal(await client.run(descriptor), null);
    assert.equal(await client.run(Object.freeze({kind: 'drip', targetId: 'target'})), null);
    assert.equal(calls.length, 0); assert.equal(client.getRecords().length, 0);
    assert.equal('hasBlocked' in client, false); assert.equal('canRefreshPage' in client, false);
    replies.push(response({success: false, error_code: 'authorization_unavailable', message: 'Sign in again.'}, 409));
    client.prepare({kind: 'drip', targetId: 'target', trigger: element('run')}); await tick(); await tick();
    assert.equal(calls.length, 1); assert.equal(calls[0].url, '/api/playlist/target/live-context');
    assert.deepEqual(JSON.parse(calls[0].options.body), {kind: 'drip'});
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )
