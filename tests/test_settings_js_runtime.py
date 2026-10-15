"""Run the shipped Settings enhancement against its submission boundaries."""

import shutil
from pathlib import Path

import pytest

from tests.node_runtime import run_scenario

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is needed for the Settings runtime checks")
    return executable


def run_node(node, body):
    run_scenario(node, FIXTURE + body, ROOT)


FIXTURE = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shuffify/static/js/settings.js', 'utf8');
function fixture({valid = true, absent = false, supported = true} = {}) {
    const events = {}, limitEvents = {}, calls = [], notifications = [];
    const attributes = {};
    // Focus as a browser keeps it: disabling the fields takes it from a field in them.
    const body = {}, doc = {body, activeElement: body};
    const button = {disabled: false, textContent: 'Save settings', attrs: {},
        setAttribute(name, value) { this.attrs[name] = String(value); },
        removeAttribute(name) { delete this.attrs[name]; },
        focus() { doc.activeElement = this; }};
    const field = {focus() { doc.activeElement = this; }};
    const fields = {off: false, get disabled() { return this.off; },
        set disabled(value) {
            this.off = value;
            if (value && doc.activeElement === field) doc.activeElement = body;
        }};
    const error = {hidden: true, textContent: '', focused: false,
                   focus() { this.focused = true; doc.activeElement = this; }};
    const limitError = {hidden: true, textContent: ''};
    const limit = {validity: {valid}, attributes: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(name, callback) { limitEvents[name] = callback; }};
    const form = {action: '/settings', dataset: {apiUrl: '/api/settings'}, valid,
        querySelector: () => button,
        reportValidity() { return this.valid; },
        addEventListener(name, callback) { events[name] = callback; },
        setAttribute(name, value) { attributes[name] = value; },
        removeAttribute(name) { delete attributes[name]; }};
    const elements = {'settings-form': absent ? null : form,
        'settings-fields': fields, 'settings-error': error,
        'max_snapshots_per_playlist': limit,
        'max_snapshots_per_playlist-error': limitError};
    let resolveResponse, rejectResponse;
    function fetch(url, options) {
        calls.push({url, options});
        return new Promise((resolve, reject) => {
            resolveResponse = resolve; rejectResponse = reject;
        });
    }
    function FormData(value) {
        assert.equal(value, form);
        assert.equal(fields.disabled, false, 'snapshot before disabling fields');
        this.csrf = 'fixture-csrf';
    }
    const window = {fetch: supported ? fetch : null, FormData,
        showNotification(...args) { notifications.push(args); }};
    doc.getElementById = id => elements[id];
    vm.runInNewContext(source, {document: doc, window, fetch, FormData});
    let prevented = 0;
    const submit = () => events.submit({preventDefault() { prevented += 1; }});
    const finish = (data, ok = true) => resolveResponse({ok, json: async () => data});
    return {form, fields, error, limit, limitError, button, field, doc, calls, notifications,
            events, limitEvents, attributes, submit, finish,
            reject: error => rejectResponse(error),
            malformed: () => resolveResponse({ok: false, json: async () => {
                throw new Error('HTML response');
            }})};
}
"""


def test_success_has_one_request_and_restores_editable_fields(node):
    run_node(node, """
(async () => {
    const f = fixture();
    f.button.focus();
    const pending = f.submit();
    assert.equal(f.fields.disabled, true);
    // Save is marked busy, never disabled, so it keeps focus while it saves.
    assert.equal(f.button.attrs['aria-disabled'], 'true'); assert.equal(f.button.disabled, false);
    assert.equal(f.doc.activeElement, f.button);
    assert.equal(f.attributes['aria-busy'], 'true');
    await f.submit();
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0].url, '/api/settings');
    assert.equal(f.calls[0].options.method, 'POST');
    assert.equal(f.calls[0].options.headers['X-Requested-With'], 'XMLHttpRequest');
    assert.equal(f.calls[0].options.body.csrf, 'fixture-csrf');
    f.finish({success: true, message: 'Saved'});
    await pending;
    assert.deepEqual(f.notifications, [['Saved', 'success']]);
    assert.equal(f.error.hidden, true);
    assert.equal(f.fields.disabled, false);
    assert.equal(f.button.attrs['aria-disabled'], undefined); assert.equal(f.button.disabled, false);
    assert.equal(f.button.textContent, 'Save settings');
    assert.equal(f.attributes['aria-busy'], undefined);
    assert.equal(f.doc.activeElement, f.button);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_a_field_saved_with_enter_gets_focus_back_once_saved(node):
    run_node(node, """
(async () => {
    const f = fixture();
    f.field.focus();
    const pending = f.submit();
    // Disabling the fields while saving takes focus from the field ...
    assert.equal(f.doc.activeElement, f.doc.body);
    f.finish({success: true, message: 'Saved'});
    await pending;
    // ... and it returns there, never left on the page.
    assert.equal(f.doc.activeElement, f.field);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_server_error_is_persistent_text_and_can_be_retried(node):
    run_node(node, """
(async () => {
    const f = fixture();
    const pending = f.submit();
    f.finish({success: false, message: '<b>Choose a valid value</b>'}, false);
    await pending;
    assert.equal(f.error.hidden, false);
    assert.equal(f.error.textContent, '<b>Choose a valid value</b>');
    assert.equal(f.error.innerHTML, undefined);
    assert.equal(f.error.focused, true); assert.equal(f.doc.activeElement, f.error);
    assert.equal(f.fields.disabled, false);
    assert.deepEqual(f.notifications, []);
    const retry = f.submit();
    assert.equal(f.error.hidden, true);
    assert.equal(f.calls.length, 2);
    f.finish({success: true});
    await retry;
    assert.equal(f.error.hidden, true);
    assert.deepEqual(f.notifications, [['Settings saved.', 'success']]);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_transport_and_non_json_failures_do_not_claim_saved(node):
    run_node(node, """
(async () => {
    for (const failure of ['reject', 'malformed']) {
        const f = fixture();
        const pending = f.submit();
        f[failure](new Error('Connection lost'));
        await pending;
        assert.equal(f.error.hidden, false);
        assert.ok(f.error.textContent);
        assert.equal(f.button.attrs['aria-disabled'], undefined); assert.equal(f.button.disabled, false);
        assert.equal(f.fields.disabled, false);
        assert.deepEqual(f.notifications, []);
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_native_validation_and_absent_enhancement_issue_no_requests(node):
    run_node(node, """
(async () => {
    const f = fixture({valid: false});
    f.limitEvents.invalid();
    assert.equal(f.limit.attributes['aria-invalid'], 'true');
    assert.equal(f.limitError.hidden, false);
    await f.submit();
    assert.equal(f.calls.length, 0);
    f.limit.validity.valid = true;
    f.limitEvents.input();
    assert.equal(f.limit.attributes['aria-invalid'], 'false');
    assert.equal(f.limitError.hidden, true);
    assert.deepEqual(Object.keys(fixture({absent: true}).events), []);
    assert.deepEqual(Object.keys(fixture({supported: false}).events), []);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
