"""Exercise the local landing demo's shipped JavaScript and rendered fallback."""

import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.node_runtime import run_scenario

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is needed for the shipped JavaScript runtime checks")
    return executable


def run_node(node, body):
    run_scenario(node, DEMO_FIXTURE + body, REPO_ROOT)


DEMO_FIXTURE = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shuffify/static/js/landing.js', 'utf8');
function fixture({missing = '', reduced = false, animation = 'pending',
                  readyState = 'complete', mutate = () => {}} = {}) {
    const animations = [], documentEvents = {}, mediaEvents = {};
    let document;
    function element(attrs = {}, textContent = '') {
        return {attrs, textContent, hidden: false, disabled: false, children: [],
            events: {}, parent: null,
            getAttribute(name) { return this.attrs[name] ?? null; },
            setAttribute(name, value) { this.attrs[name] = String(value); },
            addEventListener(name, callback) { this.events[name] = callback; },
            click() { this.events.click?.({preventDefault() {}}); },
            focus() { document.activeElement = this; },
            appendChild(child) {
                if (child.parent) {
                    child.parent.children.splice(child.parent.children.indexOf(child), 1);
                }
                this.children.push(child); child.parent = this;
                return child;
            },
            querySelector(selector) { return this.parts?.[selector] ?? null; },
            querySelectorAll() { return this.children; },
            getBoundingClientRect() {
                return {left: 0, top: this.parent.children.indexOf(this) * 64};
            },
        };
    }
    const region = element({'data-demo-state': 'original'});
    const list = element({start: '753'});
    const shuffle = element(), undo = element({'aria-disabled': 'true'});
    const controls = element(); controls.hidden = true;
    const caption = element({}, 'Buried favorites · positions 753–757');
    const badge = element({}, 'Original order'), status = element();
    const rows = [3, 1, 5, 2, 4].map((position, index) => {
        const row = element({'data-demo-track': '',
            'data-demo-original-position': String(753 + index),
            'data-demo-shuffled-position': String(position)});
        row.parts = {'.sh-landing-row-number': element({}, String(753 + index)),
            '.sh-landing-track-origin': element({}, 'Originally #' + (753 + index))};
        if (animation !== 'absent') row.animate = (frames, options) => {
            if (animation === 'throws' || (animation === 'partial-throws' && index === 1)) {
                throw new Error('animation unavailable');
            }
            let resolve, reject;
            const result = {frames, options, cancelled: false,
                finished: new Promise((yes, no) => {resolve = yes; reject = no;}),
                finish() { resolve(); },
                cancel() { this.cancelled = true; reject(new Error('cancelled')); },
                reject() { reject(new Error('interrupted')); },
            };
            animations.push(result);
            return result;
        };
        list.appendChild(row);
        return row;
    });
    const elements = {'shuffle-demo': region, 'shuffle-track-list': list,
        'shuffle-demo-controls': controls, 'demo-shuffle': shuffle,
        'demo-undo': undo, 'shuffle-demo-caption': caption,
        'shuffle-demo-badge': badge, 'shuffle-demo-status': status};
    delete elements[missing];
    document = {readyState, activeElement: null,
        getElementById: (id) => elements[id],
        addEventListener: (event, callback, options) => {
            documentEvents[event] = {callback, options};
        }};
    const media = {matches: reduced,
        addEventListener: (event, callback) => {mediaEvents[event] = callback;},
        addListener: (callback) => {mediaEvents.change = callback;}};
    const forbiddenNetwork = () => {throw new Error('demo attempted network access');};
    const window = {matchMedia: () => media, addEventListener() {},
        getComputedStyle: () => ({getPropertyValue: (name) => ({
            '--sh-motion-reorder': '720ms',
            '--sh-motion-ease': 'cubic-bezier(0.2, 0.8, 0.2, 1)',
        })[name] || ''}), fetch: forbiddenNetwork};
    mutate({rows, list, region});
    vm.runInNewContext(source, {document, window, fetch: forbiddenNetwork});
    return {rows, list, region, shuffle, undo, controls, caption, badge, status,
        document, documentEvents, animations,
        reduce(value) {media.matches = value; mediaEvents.change?.({matches: value});},
        async settle(reject = false) {
            animations.forEach((item) => reject ? item.reject() : item.finish());
            await new Promise((resolve) => setImmediate(resolve));
        }};
}
function assertOrder(f, expected) {
    assert.deepEqual(f.list.children.map((row) => f.rows.indexOf(row)), expected);
}
"""


def test_demo_restores_exact_original_nodes_labels_and_focus(node):
    run_node(node, """
(async () => {
    const f = fixture({reduced: true});
    assert.equal(f.controls.hidden, false);
    assert.equal(f.status.textContent, '');
    assert.equal(f.undo.getAttribute('aria-disabled'), 'true');
    f.undo.click();
    assertOrder(f, [0, 1, 2, 3, 4]);
    f.shuffle.focus(); f.shuffle.click();
    assertOrder(f, [1, 3, 0, 4, 2]);
    assert.equal(f.document.activeElement, f.shuffle);
    assert.equal(f.shuffle.disabled, false);
    assert.equal(f.region.getAttribute('data-demo-state'), 'shuffled');
    assert.equal(f.list.getAttribute('start'), '1');
    assert.equal(f.caption.textContent, 'A fresh order · positions 1–5');
    assert.equal(f.badge.textContent, 'Reordered');
    assert.equal(f.shuffle.getAttribute('aria-disabled'), 'true');
    assert.equal(f.undo.getAttribute('aria-disabled'), 'false');
    assert.match(f.status.textContent, /reordered.*1–5.*Undo/i);
    assert.deepEqual(f.list.children.map((row) =>
        row.parts['.sh-landing-row-number'].textContent), ['01', '02', '03', '04', '05']);
    f.rows.forEach((row, index) => assert.equal(
        row.parts['.sh-landing-track-origin'].textContent, 'Previously #' + (753 + index)));
    f.undo.focus(); f.undo.click();
    assertOrder(f, [0, 1, 2, 3, 4]);
    assert.equal(f.document.activeElement, f.undo);
    assert.equal(f.undo.disabled, false);
    assert.equal(f.region.getAttribute('data-demo-state'), 'original');
    assert.equal(f.list.getAttribute('start'), '753');
    assert.equal(f.caption.textContent, 'Buried favorites · positions 753–757');
    assert.equal(f.badge.textContent, 'Original order');
    assert.match(f.status.textContent, /restored.*753–757/i);
    f.rows.forEach((row, index) => {
        assert.equal(row.parts['.sh-landing-row-number'].textContent, String(753 + index));
        assert.equal(row.parts['.sh-landing-track-origin'].textContent,
                     'Originally #' + (753 + index));
    });
    f.undo.click();
    assertOrder(f, [0, 1, 2, 3, 4]);
    f.shuffle.click();
    assertOrder(f, [1, 3, 0, 4, 2]);
    assert.equal(f.animations.length, 0);
})().catch((error) => {console.error(error); process.exitCode = 1;});
""")


def test_demo_busy_commands_are_ignored_and_motion_uses_core_tokens(node):
    run_node(node, """
(async () => {
    const f = fixture();
    f.shuffle.focus(); f.shuffle.click();
    assertOrder(f, [1, 3, 0, 4, 2]);
    assert.equal(f.list.getAttribute('aria-busy'), 'true');
    assert.equal(f.shuffle.getAttribute('aria-disabled'), 'true');
    assert.equal(f.undo.getAttribute('aria-disabled'), 'true');
    assert.equal(f.document.activeElement, f.shuffle);
    const count = f.animations.length;
    assert.ok(count > 0);
    for (let index = 0; index < 10; index++) {f.shuffle.click(); f.undo.click();}
    assertOrder(f, [1, 3, 0, 4, 2]);
    assert.equal(f.animations.length, count);
    f.animations.forEach(({frames, options}) => {
        assert.equal(options.duration, 720);
        assert.equal(options.easing, 'cubic-bezier(0.2, 0.8, 0.2, 1)');
        assert.notEqual(frames[0].transform, frames.at(-1).transform);
    });
    await f.settle();
    assert.equal(f.list.getAttribute('aria-busy'), 'false');
    assert.equal(f.undo.getAttribute('aria-disabled'), 'false');
    f.undo.focus(); f.undo.click();
    assertOrder(f, [0, 1, 2, 3, 4]);
    assert.equal(f.list.getAttribute('aria-busy'), 'true');
    f.shuffle.click();
    assertOrder(f, [0, 1, 2, 3, 4]);
    await f.settle();
    assert.equal(f.shuffle.getAttribute('aria-disabled'), 'false');
    assert.equal(f.undo.getAttribute('aria-disabled'), 'true');
    assert.equal(f.document.activeElement, f.undo);
})().catch((error) => {console.error(error); process.exitCode = 1;});
""")


@pytest.mark.parametrize("animation", ["absent", "throws", "partial-throws", "rejects"])
def test_demo_animation_failures_keep_committed_state_and_allow_undo(node, animation):
    run_node(node, f"""
(async () => {{
    const f = fixture({{animation: '{animation}'}});
    f.shuffle.click();
    await f.settle(true);
    assertOrder(f, [1, 3, 0, 4, 2]);
    assert.equal(f.list.getAttribute('aria-busy'), 'false');
    assert.equal(f.undo.getAttribute('aria-disabled'), 'false');
    f.undo.click();
    await f.settle(true);
    assertOrder(f, [0, 1, 2, 3, 4]);
    assert.equal(f.list.getAttribute('aria-busy'), 'false');
    assert.equal(f.shuffle.getAttribute('aria-disabled'), 'false');
}})().catch((error) => {{console.error(error); process.exitCode = 1;}});
""")


def test_demo_reduced_motion_change_settles_without_stale_completion(node):
    run_node(node, """
(async () => {
    const f = fixture();
    f.shuffle.click();
    assert.ok(f.animations.length > 0);
    const previous = f.animations.slice();
    f.reduce(true);
    assert.equal(f.list.getAttribute('aria-busy'), 'false');
    assert.equal(f.undo.getAttribute('aria-disabled'), 'false');
    assert.ok(previous.every((item) => item.cancelled));
    f.undo.click();
    assertOrder(f, [0, 1, 2, 3, 4]);
    assert.equal(f.animations.length, previous.length);
    assert.equal(f.region.getAttribute('data-demo-state'), 'original');
    assert.match(f.status.textContent, /restored/i);
    f.reduce(false); f.shuffle.click();
    assert.equal(f.list.getAttribute('aria-busy'), 'true');
    assert.ok(f.animations.length > previous.length);
    previous.forEach((item) => item.reject());
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(f.list.getAttribute('aria-busy'), 'true');
    assert.equal(f.undo.getAttribute('aria-disabled'), 'true');
    await f.settle();
    assert.equal(f.undo.getAttribute('aria-disabled'), 'false');
})().catch((error) => {console.error(error); process.exitCode = 1;});
""")


def test_demo_waits_for_dom_and_exits_on_missing_or_invalid_markup(node):
    run_node(node, """
const waiting = fixture({readyState: 'loading', reduced: true});
assert.equal(waiting.controls.hidden, true);
assert.equal(waiting.documentEvents.DOMContentLoaded.options.once, true);
waiting.documentEvents.DOMContentLoaded.callback();
assert.equal(waiting.controls.hidden, false);
for (const missing of ['shuffle-demo', 'shuffle-track-list', 'shuffle-demo-controls',
    'demo-shuffle', 'demo-undo', 'shuffle-demo-caption', 'shuffle-demo-badge',
    'shuffle-demo-status']) {
    const absent = fixture({missing});
    assert.equal(absent.controls.hidden, true);
    assert.equal(absent.shuffle.events.click, undefined);
}
for (const mutate of [
    ({rows}) => rows[0].setAttribute('data-demo-shuffled-position', '0'),
    ({rows}) => rows[0].setAttribute('data-demo-shuffled-position', '1'),
    ({rows}) => rows[0].setAttribute('data-demo-shuffled-position', 'three'),
    ({rows}) => rows[0].setAttribute('data-demo-original-position', '754'),
    ({rows}) => rows[0].setAttribute('data-demo-original-position', '753.5'),
    ({rows}) => delete rows[0].parts['.sh-landing-row-number'],
    ({rows}) => delete rows[0].parts['.sh-landing-track-origin'],
    ({list}) => list.children.pop(),
]) {
    const invalid = fixture({mutate});
    assert.equal(invalid.controls.hidden, true);
    assert.equal(invalid.shuffle.events.click, undefined);
}
""")


class DemoMarkup(HTMLParser):
    """Collect only IDs and row attributes needed by the progressive demo."""

    def __init__(self):
        super().__init__()
        self.elements = {}
        self.rows = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.elements[attrs["id"]] = (tag, attrs)
        if "data-demo-track" in attrs:
            self.rows.append((tag, attrs))

    def handle_data(self, data):
        self.text.append(data)


def test_rendered_landing_has_complete_non_js_sample_and_accessible_controls(db_app):
    with db_app.test_client() as client:
        response = client.get("/")
    markup = DemoMarkup()
    markup.feed(response.get_data(as_text=True))
    region = markup.elements["shuffle-demo"][1]
    assert region["data-demo-state"] == "original"
    assert region.get("aria-hidden") != "true"
    assert markup.elements["shuffle-track-list"][0] == "ol"
    assert len(markup.rows) == 5
    assert all(tag == "li" for tag, _ in markup.rows)
    assert [attrs["data-demo-original-position"] for _, attrs in markup.rows] == [
        "753", "754", "755", "756", "757",
    ]
    assert sorted(attrs["data-demo-shuffled-position"] for _, attrs in markup.rows) == [
        "1", "2", "3", "4", "5",
    ]
    assert "hidden" in markup.elements["shuffle-demo-controls"][1]
    for identifier in ("demo-shuffle", "demo-undo"):
        tag, attrs = markup.elements[identifier]
        assert tag == "button"
        assert attrs["type"] == "button"
        assert attrs["aria-controls"] == "shuffle-track-list"
        assert "disabled" not in attrs
    assert markup.elements["demo-undo"][1]["aria-disabled"] == "true"
    status = markup.elements["shuffle-demo-status"][1]
    assert status["role"] == "status"
    assert status["aria-live"] == "polite"
    assert status["aria-atomic"] == "true"
    text = " ".join(markup.text)
    assert "Buried favorites · positions 753–757" in text
    assert "Original order" in text
    assert all(f"Originally #{position}" in text for position in range(753, 758))
    assert markup.elements["legal-consent"][1]["name"] == "legal_consent"
    assert "required" in markup.elements["legal-consent"][1]
