"""Run the shipped playlist workspace module: occurrences, moves, focus and tool context.

The fixture models only what the module touches: rows found by attribute inside a list,
focus that a disabled control refuses, and scroll offsets. Browser checks cover real layout
and focus scrolling. The draft footer's space is AppPanels.reserveFooter's
(tests/test_app_panel_runtime.py).
"""

import shutil
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
    run_scenario(node, FIXTURE + "\n(async () => {\n" + body
                 + "\n})().catch(error => { console.error(error); process.exitCode = 1; });", REPO_ROOT)


FIXTURE = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shuffify/static/js/playlist-workspace.js', 'utf8');
const A = 'spotify:track:a', B = 'spotify:track:b', C = 'spotify:track:c', D = 'spotify:track:d';
// Lists made in the script's own realm compare by value once copied out.
const own = value => JSON.parse(JSON.stringify(value));
const keys = list => Array.from(list, item => item.key);
// One compound selector: tag, .class, [attr], [attr="value"] or [attr=value].
const SELECTOR = /^(\w+)?(?:\.([\w-]+))?(?:\[([\w-]+)(?:="([^"]*)"|=([\w-]+))?\])?$/;
function fixture({panels = true} = {}) {
    const said = [];
    const document = {activeElement: null};
    class Element {
        constructor(tag, attrs = {}) {
            this.tagName = tag.toUpperCase(); this.attrs = {...attrs}; this.children = [];
            this.disabled = false; this.scrollTop = 0;
        }
        append(...children) { this.children.push(...children); return this; }
        matches(selector) {
            // An empty selector is a SyntaxError in a browser; the fixture refuses it too.
            const match = selector && selector.match(SELECTOR);
            assert.ok(match, 'the fixture cannot match ' + selector);
            const [, tag, name, attr, quoted, bare] = match, value = quoted ?? bare;
            return (!tag || this.tagName === tag.toUpperCase()) &&
                (!name || (this.attrs.class || '').split(' ').includes(name)) &&
                (!attr || (attr in this.attrs && (value === undefined || this.attrs[attr] === value)));
        }
        querySelectorAll(selector) {
            return this.children.flatMap(child => [
                ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        // A disabled control does not take focus, as in a browser.
        focus() { if (!this.disabled) document.activeElement = this; }
    }
    const window = {};
    if (panels) window.AppPanels = {announce: message => said.push(message)};
    vm.runInNewContext(source, {window, document});
    const element = (tag, attrs) => new Element(tag, attrs);
    return {api: window.PlaylistWorkspace, window, document, element, said};
}
"""


def test_the_module_exposes_the_contract_api_and_never_reuses_a_key(node):
    run_node(node, """
const f = fixture(), api = f.api;
assert.deepEqual(Object.keys(api).sort(), ['announceMove', 'focusOccurrence', 'move', 'moveKey', 'occurrences',
    'remap', 'rememberView', 'remove', 'restoreView', 'uris']);
// Each copy of a repeated song is its own occurrence.
const list = api.occurrences([A, B, A]);
assert.deepEqual(own(list), [{key: 'o1', uri: A}, {key: 'o2', uri: B}, {key: 'o3', uri: A}]);
// Keys are unique for the page's life: a later list, or a copy added after a removal, gets a new one.
assert.deepEqual(keys(api.occurrences([A])), ['o4']);
assert.deepEqual(keys(api.remap(api.remove(list, 'o3'), [A, B, A])), ['o1', 'o2', 'o5']);
assert.deepEqual(own(api.occurrences()), []);
""")


def test_duplicates_keep_distinct_keys_through_moves_and_removal(node):
    run_node(node, """
const f = fixture(), api = f.api;
const list = api.occurrences([A, B, A, C]);
// A move carries the occurrence's key, so the two copies of A stay apart.
const moved = api.move(list, 2, 0);
assert.deepEqual(keys(moved), ['o3', 'o1', 'o2', 'o4']);
assert.deepEqual(own(api.uris(moved)), [A, A, B, C]);
const placed = api.moveKey(moved, 'o1', 4);
assert.deepEqual(keys(placed), ['o3', 'o2', 'o4', 'o1']);
// Removing one copy of a repeated song removes exactly that occurrence, whichever copy it is.
assert.deepEqual(keys(api.remove(placed, 'o1')), ['o3', 'o2', 'o4']);
assert.deepEqual(keys(api.remove(placed, 'o3')), ['o2', 'o4', 'o1']);
assert.deepEqual(own(api.uris(api.remove(placed, 'o3'))), [B, C, A]);
// Pure: the inputs stay as they were, and every call returns a new list, an unchanged one too.
assert.deepEqual(keys(list), ['o1', 'o2', 'o3', 'o4']);
assert.deepEqual(keys(moved), ['o3', 'o1', 'o2', 'o4']);
for (const result of [api.remove(list, 'o9'), api.move(list, 1, 1), api.moveKey(list, 'o9', 1),
        api.moveKey(list, 'o1', NaN)]) {
    assert.notEqual(result, list); assert.deepEqual(keys(result), keys(list));
}
""")


def test_remap_keeps_keys_through_a_shuffle_and_keys_only_new_occurrences(node):
    run_node(node, """
const f = fixture(), api = f.api;
const list = api.occurrences([A, B, A, C, A]);
// A preview returns plain URIs: each URI's keys follow it, in their earlier order.
const shuffled = api.remap(list, [C, A, A, B, A]);
assert.deepEqual(own(shuffled), [{key: 'o4', uri: C}, {key: 'o1', uri: A}, {key: 'o3', uri: A},
    {key: 'o2', uri: B}, {key: 'o5', uri: A}]);
assert.deepEqual(keys(api.remap(shuffled, api.uris(shuffled))), keys(shuffled));
// Only occurrences beyond a URI's earlier count get keys: a fourth A and a new D.
const added = api.remap(shuffled, [C, A, A, B, A, A, D]);
assert.deepEqual(keys(added), ['o4', 'o1', 'o3', 'o2', 'o5', 'o6', 'o7']);
// Fewer copies keep the earliest keys; a URI that left and returns is a new occurrence.
const fewer = api.remap(added, [A, D]);
assert.deepEqual(keys(fewer), ['o1', 'o7']);
assert.deepEqual(keys(api.remap(fewer, [B, A])), ['o8', 'o1']);
assert.deepEqual(own(api.remap(null, [A])), [{key: 'o9', uri: A}]);
assert.deepEqual(own(api.remap(list, [])), []);
// The earlier list is read, never changed.
assert.deepEqual(keys(list), ['o1', 'o2', 'o3', 'o4', 'o5']);
""")


def test_moves_stop_at_the_ends_and_positions_clamp(node):
    run_node(node, """
const f = fixture(), api = f.api;
const list = api.occurrences([A, B, C]);
// Move up on the first row and Move down on the last change nothing; inside, they swap.
assert.deepEqual(keys(api.move(list, 0, -1)), ['o1', 'o2', 'o3']);
assert.deepEqual(keys(api.move(list, 2, 3)), ['o1', 'o2', 'o3']);
assert.deepEqual(keys(api.move(list, 0, 1)), ['o2', 'o1', 'o3']);
assert.deepEqual(keys(api.move(list, 2, 1)), ['o1', 'o3', 'o2']);
// toIndex is the item's index afterwards (SortableJS's newIndex), clamped to the list.
assert.deepEqual(keys(api.move(list, 0, 2)), ['o2', 'o3', 'o1']);
assert.deepEqual(keys(api.move(list, 0, 99)), ['o2', 'o3', 'o1']);
// An unknown fromIndex (findIndex's -1, NaN, past the end) or a NaN toIndex moves nothing.
assert.deepEqual(keys(api.move(list, -1, 0)), ['o1', 'o2', 'o3']);
assert.deepEqual(keys(api.move(list, NaN, 1)), ['o1', 'o2', 'o3']);
assert.deepEqual(keys(api.move(list, 3, 0)), ['o1', 'o2', 'o3']);
assert.deepEqual(keys(api.move(list, 2, NaN)), ['o1', 'o2', 'o3']);
// Move to position is 1-based and clamped to 1..N.
assert.deepEqual(keys(api.moveKey(list, 'o3', 1)), ['o3', 'o1', 'o2']);
assert.deepEqual(keys(api.moveKey(list, 'o1', 2)), ['o2', 'o1', 'o3']);
assert.deepEqual(keys(api.moveKey(list, 'o1', 3)), ['o2', 'o3', 'o1']);
assert.deepEqual(keys(api.moveKey(list, 'o3', 0)), ['o3', 'o1', 'o2']);
assert.deepEqual(keys(api.moveKey(list, 'o3', -4)), ['o3', 'o1', 'o2']);
assert.deepEqual(keys(api.moveKey(list, 'o1', 40)), ['o2', 'o3', 'o1']);
// An empty position field (NaN), an unparsed string or an unknown key moves nothing.
for (const [key, position] of [['o1', NaN], ['o3', ''], ['o1', '3'], ['o9', 2]]) {
    assert.deepEqual(keys(api.moveKey(list, key, position)), ['o1', 'o2', 'o3']);
}
assert.deepEqual(keys(list), ['o1', 'o2', 'o3']);
""")


def test_uris_round_trip_the_serialized_order(node):
    run_node(node, """
const f = fixture(), api = f.api;
const order = [A, B, A, C, B];
const list = api.occurrences(order);
assert.deepEqual(own(api.uris(list)), order);
assert.deepEqual(own(api.uris(api.remap(list, [B, A, C, B, A]))), [B, A, C, B, A]);
assert.deepEqual(own(api.uris(api.move(list, 4, 0))), [B, A, B, A, C]);
assert.deepEqual(own(api.uris([])), []);
""")


def test_announce_move_speaks_through_app_panels_only_when_present(node):
    run_node(node, """
const f = fixture(), api = f.api;
assert.equal(api.announceMove('Midnight City', 3), 'Moved Midnight City to position 3.');
assert.deepEqual(f.said, ['Moved Midnight City to position 3.']);
assert.equal(api.announceMove('', 2), 'Moved track to position 2.');
// Without the shared announcer the words are still returned and nothing breaks.
const quiet = fixture({panels: false});
assert.equal(quiet.api.announceMove('Midnight City', 1), 'Moved Midnight City to position 1.');
assert.deepEqual(quiet.said, []);
// The announcer is looked up for each move, not when the script loads.
quiet.window.AppPanels = {announce: message => quiet.said.push(message)};
quiet.api.announceMove('Intro', 12);
assert.deepEqual(quiet.said, ['Moved Intro to position 12.']);
""")


def test_focus_follows_the_moved_occurrence_not_its_duplicate(node):
    run_node(node, """
const f = fixture(), api = f.api;
let list = api.occurrences([A, B, A]);
const tracks = f.element('ol', {id: 'track-list'});
// The Workshop's rows, rebuilt in list order after each move as a re-render does.
function render(items) {
    tracks.children = items.map(item => f.element('li', {class: 'track-item', 'data-occurrence': item.key,
        'data-uri': item.uri}).append(f.element('button', {class: 'track-actions-btn'}),
        f.element('button', {'data-move': 'up'})));
}
render(list);
// The second copy of A takes focus, not the first row with the same URI.
assert.equal(api.focusOccurrence(tracks, 'o3', '.track-actions-btn'), true);
assert.equal(f.document.activeElement, tracks.children[2].children[0]);
// After a move the same occurrence's control has focus at its new position.
list = api.moveKey(list, 'o3', 1); render(list);
assert.equal(tracks.children[0].attrs['data-occurrence'], 'o3');
assert.equal(api.focusOccurrence(tracks, 'o3', '[data-move="up"]'), true);
assert.equal(f.document.activeElement, tracks.children[0].children[1]);
// No such row, no such control, or a control that cannot take focus: false, and focus stays.
const focused = f.document.activeElement;
tracks.children[1].children[0].disabled = true;
for (const [container, key, selector] of [[tracks, 'o9', '.track-actions-btn'], [tracks, 'o1', '.missing'],
        [tracks, 'o1', '.track-actions-btn'], [tracks, 'o1', ''], [tracks, 'o1', undefined],
        [null, 'o1', '.track-actions-btn']]) {
    assert.equal(api.focusOccurrence(container, key, selector), false);
}
assert.equal(f.document.activeElement, focused);
""")


def test_view_memory_keeps_each_tool_body_scroll_position(node):
    run_node(node, """
const f = fixture(), api = f.api;
const inbox = f.element('div'), archive = f.element('div');
// Nothing remembered yet: the body stays where it is.
inbox.scrollTop = 40;
assert.equal(api.restoreView('inbox', inbox), false); assert.equal(inbox.scrollTop, 40);
inbox.scrollTop = 240; api.rememberView('inbox', inbox);
archive.scrollTop = 30; api.rememberView('archive', archive);
// A hidden body loses its scroll; each tool gets its own position back on reopening.
inbox.scrollTop = 0; archive.scrollTop = 0;
assert.equal(api.restoreView('inbox', inbox), true); assert.equal(inbox.scrollTop, 240);
assert.equal(api.restoreView('archive', archive), true); assert.equal(archive.scrollTop, 30);
// A later visit replaces the position, the top included; a missing element is ignored.
archive.scrollTop = 0; api.rememberView('archive', archive); archive.scrollTop = 55;
assert.equal(api.restoreView('archive', archive), true); assert.equal(archive.scrollTop, 0);
api.rememberView('inbox', null);
assert.equal(api.restoreView('inbox', null), false);
inbox.scrollTop = 0; api.restoreView('inbox', inbox); assert.equal(inbox.scrollTop, 240);
""")
