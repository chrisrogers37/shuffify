"""Exercise shipped presentation scripts with Node's VM and tiny DOM fixtures.

No browser/DOM package is needed. These tests execute the actual source, rather
than a Python copy of the consent or fetch behavior.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.node_runtime import run_scenario

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_DIR = REPO_ROOT / "shuffify/static/js"


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is needed for the shipped JavaScript runtime checks")
    return executable


def run_node(node, body):
    run_scenario(node, body, REPO_ROOT)


@pytest.mark.parametrize("source", sorted(JS_DIR.glob("*.js")), ids=lambda p: p.name)
def test_shipped_scripts_parse(node, source):
    result = subprocess.run([node, "--check", source], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


CONSENT_FIXTURE = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shuffify/static/js/landing.js', 'utf8');
function fixture({checked = false, readyState = 'complete', missing = ''} = {}) {
    const changes = {}, documentEvents = {}, windowEvents = {};
    const checkbox = {checked, addEventListener: (event, callback) => {
        changes[event] = callback;
    }};
    const button = {disabled: false}, caption = {textContent: ''};
    const elements = {'legal-consent': checkbox, 'login-button': button,
                      'cta-subtext': caption};
    delete elements[missing];
    const document = {readyState, getElementById: (id) => elements[id],
        addEventListener: (event, callback, options) => {
            documentEvents[event] = {callback, options};
        }};
    const window = {addEventListener: (event, callback) => {
        windowEvents[event] = callback;
    }};
    vm.runInNewContext(source, {document, window});
    return {checkbox, button, caption, changes, documentEvents, windowEvents};
}
"""


def test_consent_initial_change_and_restored_page_state(node):
    run_node(node, CONSENT_FIXTURE + """
const f = fixture();
assert.equal(f.button.disabled, true);
assert.equal(f.caption.textContent, 'Free · Approve & Connect');
f.checkbox.checked = true;
f.changes.change();
assert.equal(f.button.disabled, false);
assert.equal(f.caption.textContent, 'Free · Ready to Start');
f.checkbox.checked = false;
f.windowEvents.pageshow();
assert.equal(f.button.disabled, true);
assert.equal(f.caption.textContent, 'Free · Approve & Connect');
const restored = fixture({checked: true});
assert.equal(restored.button.disabled, false);
assert.equal(restored.caption.textContent, 'Free · Ready to Start');
""")


def test_consent_waits_for_dom_and_tolerates_optional_elements(node):
    run_node(node, CONSENT_FIXTURE + """
const waiting = fixture({readyState: 'loading'});
assert.deepEqual(Object.keys(waiting.changes), []);
assert.equal(waiting.documentEvents.DOMContentLoaded.options.once, true);
waiting.documentEvents.DOMContentLoaded.callback();
assert.equal(waiting.button.disabled, true);
assert.equal(typeof waiting.changes.change, 'function');
for (const missing of ['legal-consent', 'login-button']) {
    const absent = fixture({missing});
    assert.deepEqual(Object.keys(absent.changes), []);
    assert.deepEqual(Object.keys(absent.windowEvents), []);
}
const noCaption = fixture({missing: 'cta-subtext'});
noCaption.checkbox.checked = true;
noCaption.changes.change();
assert.equal(noCaption.button.disabled, false);
""")


FETCH_FIXTURE = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shuffify/static/js/base.js', 'utf8');
function fixture(withMeta = true) {
    const calls = [], response = Promise.resolve({ok: true});
    const original = function(url, opts) {
        calls.push({url, opts, receiver: this});
        return response;
    };
    const window = {fetch: original};
    const document = {querySelector: () => withMeta ? {
        getAttribute: () => 'fixture-csrf-token'
    } : null, addEventListener: () => {}};
    vm.runInNewContext(source, {window, document, Headers});
    return {window, calls, response, original};
}
"""


def test_fetch_csrf_injects_mutations_and_preserves_headers_and_return_value(node):
    run_node(node, FETCH_FIXTURE + """
const f = fixture();
for (const method of ['POST', 'put', 'PATCH', 'DELETE']) {
    const opts = {method, headers: {'Content-Type': 'application/json'}};
    assert.equal(f.window.fetch('/action', opts), f.response);
    const call = f.calls.at(-1);
    assert.equal(call.receiver, f.window);
    assert.equal(call.url, '/action');
    assert.equal(call.opts.headers['X-CSRFToken'], 'fixture-csrf-token');
    assert.equal(call.opts.headers['Content-Type'], 'application/json');
}
for (const key of ['X-CSRFToken', 'x-csrftoken', 'X-CsRfToKeN']) {
    const headers = {[key]: 'explicit-token', Accept: 'application/json'};
    f.window.fetch('/action', {method: 'POST', headers});
    assert.deepEqual(headers, {[key]: 'explicit-token', Accept: 'application/json'});
}
const native = new Headers({'Content-Type': 'application/json'});
f.window.fetch('/action', {method: 'POST', headers: native});
assert.equal(native.get('X-CSRFToken'), 'fixture-csrf-token');
assert.equal(native.get('Content-Type'), 'application/json');
const explicit = new Headers({'x-csrftoken': 'native-explicit'});
f.window.fetch('/action', {method: 'POST', headers: explicit});
assert.equal(explicit.get('X-CSRFToken'), 'native-explicit');
f.window.fetch('/action', {method: 'POST'});
assert.equal(f.calls.at(-1).opts.headers['X-CSRFToken'], 'fixture-csrf-token');
""")


def test_fetch_csrf_leaves_safe_methods_and_absent_meta_alone(node):
    run_node(node, FETCH_FIXTURE + """
const f = fixture();
for (const method of ['GET', 'head', 'OPTIONS']) {
    const opts = {method, headers: {Accept: 'application/json'}};
    const before = JSON.stringify(opts);
    f.window.fetch('/read', opts);
    assert.equal(JSON.stringify(opts), before);
}
f.window.fetch('/read');
assert.equal(f.calls.at(-1).opts.headers, undefined);
const absent = fixture(false);
assert.equal(absent.window.fetch, absent.original);
""")


@pytest.mark.parametrize("mutation,expected", [
    ("values.spacing.semantic.control = 'missing'", "Unknown spacing alias"),
    ("values.fonts.roles.ui.family = []", "Invalid font family"),
    ("values.fonts.roles.ui.source = '../fonts/missing.woff2'", "font asset"),
    ("values.fonts.roles.ui.source = '../../outside.woff2'", "font asset"),
    ("values.fonts.roles.ui.source = 'https://example.com/font.woff2'", "font asset"),
])
def test_adapter_rejects_bad_spacing_and_font_references(node, mutation, expected):
    run_node(node, f"""
const assert = require('node:assert/strict');
const adapter = require('./assets/design');
const values = structuredClone(require('./assets/design/tokens.json'));
{mutation};
assert.throws(() => adapter.validateTokens(values),
              new RegExp({json.dumps(expected)}));
""")


def test_authenticated_feedback_uses_text_and_semantic_status(node):
    run_node(node, """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const children = [], timers = [], window = {};
const document = {
    getElementById: () => ({appendChild: element => children.push(element)}),
    createElement: () => ({className: '', textContent: '', classes: [],
        attributes: {}, removed: false,
        setAttribute(name, value) { this.attributes[name] = value; },
        get classList() { return {add: value => this.classes.push(value)}; },
        remove() { this.removed = true; }})
};
vm.runInNewContext(fs.readFileSync('shuffify/static/js/app-feedback.js', 'utf8'),
    {window, document, setTimeout: callback => timers.push(callback)});
const error = window.showNotification('<img src=x onerror=alert(1)>', 'error');
assert.equal(error.textContent, '<img src=x onerror=alert(1)>');
assert.equal(error.innerHTML, undefined);
assert.equal(error.attributes.role, 'alert');
assert.deepEqual(error.classes, ['sh-toast-error']);
const success = window.showNotification('Saved', 'success');
assert.equal(success.attributes.role, 'status');
// A toast whose words the page's announcer already said is shown, not read again.
const shown = window.showNotification('Nothing was sent.', 'info', {spoken: false});
assert.equal(shown.attributes['aria-hidden'], 'true'); assert.equal(shown.attributes.role, undefined);
assert.equal(children.length, 3);
timers.shift()();
assert.equal(error.classes.at(-1), 'hiding');
timers.at(-1)();
assert.equal(error.removed, true);
""")
