"""Library rows, Organize mode and results in place, on the rendered page with its scripts.

Favorite, hide and show move their row from the state the server returns;
Organize shows those controls and each row's order controls, whose moves keep focus on
the control used, are announced once and save the order Favorites-then-All playlists;
reset asks the shared confirmation; refresh and reset reload through the
shared guard, and a declined reload leaves one persistent control; a verified Restore
shows its track count; focus never jumps on a result. The page's real scripts run over
its real markup (see tests/page_runtime.py); the network and the shared confirmation
are test doubles.
"""

import re
from collections import Counter
from pathlib import Path

import pytest

from tests.page_runtime import page_tree, pref, render_page, run_page
from tests.test_playlist_operations_runtime import node as node  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def playlist(playlist_id, name, tracks, public=True):
    return {
        "id": playlist_id,
        "name": name,
        "images": [],
        "public": public,
        "items": {"total": tracks},
        "external_urls": {
            "spotify": "https://open.spotify.com/playlist/" + playlist_id
        },
    }


ALGORITHMS = [
    {
        "name": "Basic",
        "class_name": "BasicShuffle",
        "description": "Random order",
        "parameters": {
            "keep_first": {
                "type": "integer",
                "default": 0,
                "min": 0,
                "description": "Tracks to keep",
            }
        },
    }
]
REGULAR = [
    playlist("mix", "Morning Mix", 40),
    playlist("focus", "Focus Flow", 12),
    playlist("road", "Road Trip", 1, public=False),
]
FAVORITES = [playlist("fav", "Old Favorite", 8)]
HIDDEN = [playlist("gone", "Hidden Gem", 5), playlist("quiet", "Quiet Hours", 3)]
PREFERENCES = {
    "fav": pref(pinned=True),
    "gone": pref(pinned=True, hidden=True),
    "quiet": pref(hidden=True),
}


def library_page(
    db_app, regular=REGULAR, favorites=FAVORITES, hidden=HIDDEN, preferences=PREFERENCES
):
    return render_page(
        db_app,
        "dashboard.html",
        path="/",
        user={"id": "fixture", "display_name": "Fixture", "images": []},
        playlists=regular,
        favorite_playlists=favorites,
        hidden_playlists=hidden,
        algorithms=ALGORITHMS,
        dashboard={},
        preferences=preferences,
        shuffle_defaults={"algorithm": "BasicShuffle", "params": {"keep_first": 2}},
        playlist_states={"mix": {"current_index": 1}},
    )


@pytest.fixture
def library(db_app):
    return library_page(db_app)


SCENARIO = r"""
loadPage(TREE, {path: '/'});
const {calls, replies, notices, navigations, asked} = page;
const tile = id => page.$('.card-tile[data-playlist-id="' + id + '"]');
const inTile = (id, selector) => tile(id).querySelector(selector);
const grid = id => page.$$('#' + id + ' .card-tile').map(element => element.dataset.playlistId);
const summaryOf = id => inTile(id, '.library-playlist-actions > summary');
const shown = element => Boolean(element) && element.getClientRects().length > 0;
// Activates a control the way a person can: only while it is shown.
function press(element) {
    assert.ok(shown(element), 'not shown: ' + (element && (element.id || element.className)));
    element.click();
}
// Opens a row's Playlist actions, which hold every Spotify change for it.
function openActions(id) {
    if (!inTile(id, '.library-playlist-actions').open) press(summaryOf(id));
}
const organize = () => press(page.byId('organize-btn'));
const announced = () => page.text(page.byId('live-operation-announcer'));
const orders = () => calls.filter(call => call.url === '/api/playlist-preferences/order');
const ordered = index => JSON.parse(orders().at(index).body).playlist_ids;
const saved = () => page.reply({success: true, message: 'Saved order for 4 playlists', count: 4});
// A row's place: its position field, and whether Move up and Move down mark an end.
const place = id => [inTile(id, '.library-position').value,
    inTile(id, '.library-move-up').getAttribute('aria-disabled'),
    inTile(id, '.library-move-down').getAttribute('aria-disabled')];
const toggled = (field, value) => page.reply(Object.assign({success: true, message: 'Saved.'}, {[field]: value}));
// A pointer drag of one row onto another, start to end.
function drag(from, to) {
    const data = {data: {}, effectAllowed: '', dropEffect: '', setData(type, value) { this.data[type] = value; }};
    tile(from).dispatchEvent(page.event('dragstart', {bubbles: true, dataTransfer: data}));
    tile(to).dispatchEvent(page.event('dragover', {bubbles: true, cancelable: true, dataTransfer: data}));
    tile(to).dispatchEvent(page.event('drop', {bubbles: true, cancelable: true, dataTransfer: data}));
    tile(from).dispatchEvent(page.event('dragend', {bubbles: true, dataTransfer: data}));
}
const SCOPE = '1'.repeat(64), BEFORE = '2'.repeat(64), HISTORY = '3'.repeat(64);
const uri = n => 'spotify:track:' + String(n).padStart(22, '0');
function context(kind, id, name) {
    const undo = kind === 'undo';
    return {version: 1, kind, target: {id, name}, label: undo ? 'Restore previous saved version now' : 'Shuffle now',
        resources: [{resource: id, resource_name: name, role: 'target', domain: 'spotify', access: 'write',
            mutation_type: 'track_order', field: null}],
        required_bookkeeping: ['session_history'],
        review_binding: {version: 1, scope: SCOPE, before_order: undo ? BEFORE : null, history: undo ? HISTORY : null},
        current: undo ? {track_count: 2, track_uris: [uri(1), uri(2)], loaded_at: '2026-10-12T08:00:00Z'}
            : {track_count: 40, loaded_at: '2026-10-12T08:00:00Z'},
        action: undo ? {current_index: 1, selected_index: 0, desired_track_uris: [uri(2), uri(2), uri(1)],
            summary: {added_count: 1, removed_count: 0, order_changed: true}}
            : {algorithm: {id: 'BasicShuffle', name: 'Basic'}, params: {keep_first: 2}}};
}
// Opens a review from a row control and waits until it has loaded.
async function review(control, kind, id, name) {
    replies.push(page.reply({success: true, context: context(kind, id, name)}));
    press(control); await settle();
}
const runReview = () => page.byId('live-operation-run').click();
async function lostShuffle() {
    openActions('mix');
    await review(inTile('mix', '.library-quick-shuffle'), 'shuffle', 'mix', 'Morning Mix');
    replies.push(() => { throw new Error('lost response'); });
    runReview(); await settle();
}
"""


def test_the_header_names_the_page_and_its_controls_with_plain_welcome_text(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    const heading = page.$('h1');
    assert.equal(page.text(heading), 'Your playlists');
    const header = heading.closest('header');
    // Refresh, Organize and the live action results, in that order.
    assert.deepEqual(header.querySelectorAll('button').map(button => button.id),
        ['refresh-playlists-btn', 'organize-btn', 'live-operation-open']);
    assert.equal(page.text(page.byId('organize-btn')), 'Organize');
    assert.equal(page.byId('live-operation-open').hidden, true);
    // The welcome is plain text under the header: no card, picture or metric tiles.
    const intro = header.querySelector('p.sh-library-intro');
    assert.match(page.text(intro), /^Welcome, Fixture! Open a playlist to arrange its tracks/);
    assert.deepEqual(header.querySelectorAll('img, .sh-surface, .sh-badge'), []);
    // Favorites, All playlists and the Hidden disclosure, each heading its own list.
    assert.equal(page.text(page.byId('favorites-heading')), 'Favorites');
    assert.equal(page.text(page.byId('playlists-heading')), 'All playlists');
    assert.equal(page.text(page.byId('playlists-count')), '3');
    const toggle = page.byId('hidden-toggle-btn');
    assert.equal(toggle.parentElement.localName, 'h2');
    assert.equal(page.text(toggle), 'Hidden 2');
    assert.deepEqual([toggle.getAttribute('aria-expanded'), toggle.getAttribute('aria-controls')],
        ['false', 'hidden-grid-wrapper']);
    assert.equal(page.byId('playlists-empty').hidden, true);
""",
    )


def test_rows_are_named_list_items_with_open_playlist_spotify_and_one_actions_disclosure(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    // Each section is an ordered list of rows, in the order the server placed them.
    assert.deepEqual(grid('favorites-grid'), ['fav']);
    assert.deepEqual(grid('playlist-grid'), ['mix', 'focus', 'road']);
    assert.deepEqual(grid('hidden-grid'), ['gone', 'quiet']);
    ['favorites-grid', 'playlist-grid', 'hidden-grid'].forEach(id => {
        assert.equal(page.byId(id).localName, 'ol'); assert.equal(page.byId(id).getAttribute('role'), 'list');
        page.byId(id).children.forEach(row => {
            assert.equal(row.localName, 'li');
            assert.equal(row.matches('.card-tile.sh-library-row[data-panel-return]'), true);
        });
    });
    const row = tile('mix');
    // A list item named by its title, so focus that lands on it says where it is.
    assert.equal(row.hasAttribute('role'), false);
    assert.equal(page.text(page.byId(row.getAttribute('aria-labelledby'))), 'Morning Mix');
    assert.deepEqual([row.dataset.playlistName, row.dataset.pinned, row.dataset.hidden, row.dataset.section],
        ['Morning Mix', 'false', 'false', 'regular']);
    const gone = tile('gone');
    assert.deepEqual([gone.dataset.pinned, gone.dataset.hidden, gone.dataset.section], ['true', 'true', 'hidden']);
    // At rest a row shows its artwork and Open playlist (both open Workshop), its count, its
    // Spotify link (a new tab, and named so) and the Playlist actions disclosure: nothing
    // waits for a hover.
    const art = inTile('mix', '.sh-library-row-art'), open = inTile('mix', '.library-open-playlist');
    const spotify = inTile('mix', '.sh-spotify-link');
    assert.deepEqual([art.getAttribute('href'), art.getAttribute('tabindex'), art.getAttribute('aria-label')],
        ['/workshop/mix', '-1', 'Open playlist: Morning Mix']);
    assert.ok(art.querySelector('.sh-library-cover-placeholder'));
    assert.deepEqual([page.text(open), open.getAttribute('href'), open.getAttribute('aria-label')],
        ['Open playlist', '/workshop/mix', 'Open playlist: Morning Mix']);
    assert.deepEqual([spotify.getAttribute('href'), spotify.getAttribute('target'), spotify.getAttribute('rel'),
        spotify.getAttribute('aria-label'), spotify.querySelector('img').getAttribute('alt')],
        ['https://open.spotify.com/playlist/mix', '_blank', 'noopener noreferrer',
            'Open Morning Mix on Spotify (new tab)', 'Spotify']);
    assert.equal(page.text(inTile('mix', '.library-track-count')), '40 tracks');
    assert.equal(page.text(inTile('road', '.library-track-count')), '1 track');
    const actions = inTile('mix', '.library-playlist-actions');
    assert.equal(actions.localName, 'details'); assert.equal(actions.open, false);
    assert.equal(page.text(summaryOf('mix')), 'Playlist actions');
    assert.equal(summaryOf('mix').getAttribute('aria-label'), 'Playlist actions: Morning Mix');
    // The controls a person can reach, each named by its first class.
    const reachable = () => row.querySelectorAll('a[href], button, summary, input:not([type="hidden"]), select')
        .filter(shown).map(control => control.className.split(' ')[0]);
    assert.deepEqual(reachable(), ['sh-library-row-art', 'library-open-playlist', 'sh-spotify-link',
        'sh-library-quiet-control']);
    // Opened, it holds the quick shuffle, Restore while history allows it, and visibility.
    press(summaryOf('mix'));
    const quick = inTile('mix', '.library-quick-shuffle'), restore = inTile('mix', '.library-undo');
    const visibility = inTile('mix', '.visibility-btn');
    assert.deepEqual(reachable(), ['sh-library-row-art', 'library-open-playlist', 'sh-spotify-link',
        'sh-library-quiet-control', 'library-quick-shuffle', 'library-undo', 'library-shuffle-summary',
        'visibility-btn']);
    assert.equal(page.text(quick), 'Shuffle now'); assert.equal(quick.dataset.liveOperationKey, 'shuffle:mix');
    assert.equal(page.text(restore), 'Restore previous saved version now');
    assert.equal(restore.dataset.liveOperationKey, 'undo:mix');
    openActions('focus');
    assert.equal(shown(inTile('focus', '.library-undo')), false);
    // Public or private is a Spotify change, and says so.
    assert.equal(page.text(visibility), 'Make private now');
    assert.equal(visibility.dataset.liveOperationKey, 'privacy:mix');
    assert.match(page.text(page.byId(visibility.getAttribute('aria-describedby'))), /a Spotify change/);
    openActions('road');
    assert.equal(page.text(inTile('road', '.visibility-btn')), 'Make public now');
    // The preference controls wait for Organize.
    assert.equal(shown(inTile('mix', '.library-organize')), false);
    assert.deepEqual(writes(), []);
""",
    )


def test_organize_shows_preference_controls_and_says_they_save_immediately(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    const rows = page.$$('.card-tile');
    const organizing = () => rows.map(row => shown(row.querySelector('.library-organize')));
    assert.deepEqual(organizing(), rows.map(() => false));
    assert.equal(page.byId('organize-note').hidden, true);
    assert.deepEqual(rows.map(row => row.draggable), rows.map(() => false));
    organize();
    assert.equal(page.text(page.byId('organize-btn')), 'Done');
    const note = page.byId('organize-note');
    assert.equal(shown(note), true);
    assert.match(page.text(note), /save your Library preferences immediately/);
    assert.match(page.text(note), /Your playlists on Spotify do not change/);
    assert.equal(page.text(page.$('[data-action-click="dash-004"]')), 'Reset preferences');
    // Favorites and All playlists rows can be favorited, hidden and moved within their section.
    for (const id of ['fav', 'mix', 'focus', 'road']) {
        ['.fav-btn', '.hide-btn', '.library-move-up', '.library-move-down', '.library-position', '.library-move-to']
            .forEach(selector => assert.equal(shown(inTile(id, selector)), true, id + selector));
        assert.equal(shown(inTile(id, '.unhide-btn')), false);
        assert.equal(tile(id).draggable, true);
    }
    assert.deepEqual([page.text(inTile('fav', '.fav-btn')), inTile('fav', '.fav-btn').getAttribute('aria-label')],
        ['Unfavorite', 'Unfavorite: Old Favorite']);
    assert.deepEqual([page.text(inTile('mix', '.fav-btn')), inTile('mix', '.fav-btn').getAttribute('aria-label')],
        ['Favorite', 'Favorite: Morning Mix']);
    assert.deepEqual(['.hide-btn', '.library-move-up', '.library-move-down', '.library-move-to']
        .map(selector => [page.text(inTile('mix', selector)), inTile('mix', selector).getAttribute('aria-label')]),
        [['Hide', 'Hide: Morning Mix'], ['Move up', 'Move up: Morning Mix'], ['Move down', 'Move down: Morning Mix'],
            ['Move to position', 'Move to position: Morning Mix']]);
    // Each place is 1…N in the row's own section.
    const field = inTile('focus', '.library-position');
    assert.equal(page.text(page.$('label[for="' + field.id + '"]')), 'Position of Focus Flow');
    assert.deepEqual([field.type, field.value, field.min, field.max], ['number', '2', '1', '3']);
    assert.equal(page.text(page.byId(field.getAttribute('aria-describedby'))), 'of 3');
    assert.deepEqual([place('mix'), place('focus'), place('road'), place('fav')],
        [['1', 'true', null], ['2', null, null], ['3', null, 'true'], ['1', 'true', 'true']]);
    // Hidden rows can be favorited and shown, but keep no order and never drag.
    press(page.byId('hidden-toggle-btn'));
    for (const id of ['gone', 'quiet']) {
        assert.equal(shown(inTile(id, '.fav-btn')), true); assert.equal(shown(inTile(id, '.unhide-btn')), true);
        assert.equal(shown(inTile(id, '.hide-btn')), false); assert.equal(shown(inTile(id, '.library-order')), false);
        assert.equal(tile(id).draggable, false);
    }
    const show = inTile('quiet', '.unhide-btn');
    assert.deepEqual([page.text(show), show.getAttribute('aria-label')], ['Show', 'Show: Quiet Hours']);
    // Done hides them again and folds the Hidden list away.
    organize();
    assert.equal(page.text(page.byId('organize-btn')), 'Organize');
    assert.equal(note.hidden, true);
    assert.deepEqual(organizing(), rows.map(() => false));
    assert.deepEqual(rows.map(row => row.draggable), rows.map(() => false));
    assert.equal(page.byId('hidden-grid-wrapper').classList.contains('hidden'), true);
    assert.equal(page.byId('hidden-toggle-btn').getAttribute('aria-expanded'), 'false');
    assert.deepEqual(writes(), []);
""",
    )


def test_keyboard_moves_keep_focus_announce_once_and_save_favorites_then_all(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    const down = inTile('mix', '.library-move-down');
    down.focus();
    replies.push(saved());
    press(down); await settle();
    assert.deepEqual(grid('playlist-grid'), ['focus', 'mix', 'road']);
    // Moving a row takes focus out of it; the control that moved it gets it back.
    assert.equal(page.document.activeElement, down);
    await heard();
    assert.equal(announced(), 'Moved Morning Mix to position 2 in All playlists.');
    // The order path drag uses: Favorites, then All playlists, never Hidden.
    assert.equal(orders().length, 1);
    assert.equal(orders()[0].method, 'POST');
    assert.deepEqual({...orders()[0].init.headers},
        {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'});
    assert.deepEqual(JSON.parse(orders()[0].body), {playlist_ids: ['fav', 'focus', 'mix', 'road']});
    // The move was announced, so its saved order is shown without being read out again.
    assert.deepEqual(notices.at(-1), {message: 'Order saved', type: 'success', spoken: false});
    // Every place in the section is shown again.
    assert.deepEqual([place('focus'), place('mix'), place('road')],
        [['1', 'true', null], ['2', null, null], ['3', null, 'true']]);
    const up = inTile('mix', '.library-move-up');
    up.focus(); replies.push(saved());
    press(up); await settle(); await heard();
    assert.deepEqual(grid('playlist-grid'), ['mix', 'focus', 'road']);
    assert.equal(page.document.activeElement, up);
    assert.equal(announced(), 'Moved Morning Mix to position 1 in All playlists.');
    assert.deepEqual(ordered(1), ['fav', 'mix', 'focus', 'road']);
    assert.deepEqual(place('mix'), ['1', 'true', null]);
    // Favorites keeps its own order: a move there never crosses into All playlists.
    replies.push(toggled('is_pinned', true));
    press(inTile('road', '.fav-btn')); await settle();
    assert.deepEqual(grid('favorites-grid'), ['fav', 'road']);
    const roadUp = inTile('road', '.library-move-up');
    roadUp.focus(); replies.push(saved());
    press(roadUp); await settle(); await heard();
    assert.deepEqual(grid('favorites-grid'), ['road', 'fav']);
    assert.deepEqual(grid('playlist-grid'), ['mix', 'focus']);
    assert.equal(page.document.activeElement, roadUp);
    assert.equal(announced(), 'Moved Road Trip to position 1 in Favorites.');
    assert.deepEqual(ordered(2), ['road', 'fav', 'mix', 'focus']);
    assert.equal(orders().length, 3); assert.deepEqual(navigations, []); assert.deepEqual(page.errors, []);
""",
    )


def test_moves_hold_to_the_section_ends_and_move_to_position_reads_a_whole_number(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    // A move to where the row already is sends nothing and says so; focus stays put.
    const up = inTile('mix', '.library-move-up');
    up.focus(); press(up); await settle(); await heard();
    assert.equal(announced(), 'Morning Mix is already at position 1 in All playlists.');
    assert.equal(page.document.activeElement, up);
    press(inTile('road', '.library-move-down')); await settle(); await heard();
    assert.equal(announced(), 'Road Trip is already at position 3 in All playlists.');
    assert.deepEqual(orders(), []); assert.deepEqual(grid('playlist-grid'), ['mix', 'focus', 'road']);
    // Move to position holds the entry to 1…N, and the field shows where the row went.
    const field = inTile('mix', '.library-position'), moveTo = inTile('mix', '.library-move-to');
    field.value = '99'; moveTo.focus(); replies.push(saved());
    press(moveTo); await settle(); await heard();
    assert.deepEqual(grid('playlist-grid'), ['focus', 'road', 'mix']);
    assert.equal(announced(), 'Moved Morning Mix to position 3 in All playlists.');
    assert.equal(page.document.activeElement, moveTo); assert.equal(field.value, '3');
    field.value = '0'; replies.push(saved());
    press(moveTo); await settle(); await heard();
    assert.deepEqual(grid('playlist-grid'), ['mix', 'focus', 'road']);
    assert.equal(announced(), 'Moved Morning Mix to position 1 in All playlists.');
    field.value = '-4';
    press(moveTo); await settle(); await heard();
    assert.equal(announced(), 'Morning Mix is already at position 1 in All playlists.');
    assert.equal(field.value, '1'); assert.equal(orders().length, 2);
    // A decimal entry is taken as its whole number.
    field.value = '2.7'; replies.push(saved());
    press(moveTo); await settle(); await heard();
    assert.deepEqual(grid('playlist-grid'), ['focus', 'mix', 'road']);
    // An empty entry moves nothing: the field is marked and focused, and the range is said.
    field.value = '  ';
    press(moveTo); await settle(); await heard();
    assert.equal(orders().length, 3); assert.deepEqual(grid('playlist-grid'), ['focus', 'mix', 'road']);
    assert.equal(field.getAttribute('aria-invalid'), 'true'); assert.equal(page.document.activeElement, field);
    assert.equal(announced(), 'Enter a position from 1 to 3.');
    // A readable entry clears the mark, even one that changes nothing.
    field.value = '2';
    press(moveTo); await settle(); await heard();
    assert.equal(field.hasAttribute('aria-invalid'), false); assert.equal(orders().length, 3);
    assert.equal(announced(), 'Morning Mix is already at position 2 in All playlists.');
    // Enter in the field moves the row and keeps focus there; arrows stay the field's own.
    field.value = '3';
    const arrow = page.key(field, 'ArrowUp');
    assert.equal(arrow.defaultPrevented, false); assert.equal(orders().length, 3);
    replies.push(saved());
    const enter = page.key(field, 'Enter'); await settle(); await heard();
    assert.equal(enter.defaultPrevented, true);
    assert.deepEqual(grid('playlist-grid'), ['focus', 'road', 'mix']);
    assert.equal(page.document.activeElement, field); assert.equal(field.value, '3');
    assert.equal(field.hasAttribute('aria-invalid'), false);
    assert.equal(announced(), 'Moved Morning Mix to position 3 in All playlists.');
    assert.deepEqual(ordered(-1), ['fav', 'focus', 'road', 'mix']);
    assert.deepEqual(page.errors, []);
""",
    )


def test_quick_moves_save_the_latest_order_once_and_only_its_outcome_is_reported(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    let release; replies.push(() => new Promise(resolve => { release = resolve; }));
    const down = inTile('mix', '.library-move-down');
    press(down); await settle();
    press(down); await settle();
    // The page shows both moves at once; the second save waits for the first.
    assert.deepEqual(grid('playlist-grid'), ['focus', 'road', 'mix']);
    assert.equal(orders().length, 1); assert.deepEqual(ordered(0), ['fav', 'focus', 'mix', 'road']);
    // The queued save sends the order shown when it starts, so a failure it supersedes is not reported.
    replies.push(saved());
    release(page.reply({success: false, message: 'Failed to save playlist order'}, 500)); await settle();
    assert.equal(orders().length, 2); assert.deepEqual(ordered(1), ['fav', 'focus', 'road', 'mix']);
    const about = () => notices.filter(notice => /order/i.test(notice.message));
    assert.deepEqual(about(), [{message: 'Order saved', type: 'success', spoken: false}]);
    // The latest save failing is read out.
    replies.push(page.reply({success: false, message: 'Failed to save playlist order'}, 500));
    press(inTile('mix', '.library-move-up')); await settle();
    assert.deepEqual(notices.at(-1), {message: 'Failed to save playlist order', type: 'error'});
    replies.push(page.reply({success: false}, 400));
    press(inTile('mix', '.library-move-up')); await settle(); await heard();
    assert.deepEqual(notices.at(-1), {message: 'Failed to save order', type: 'error'});
    // Without a readable reply the order may still have been saved: nothing claims it failed,
    // and the persistent notice says it is unconfirmed, with the reload that shows it, and is read out.
    const unconfirmed = 'Your new Library order could not be confirmed.';
    const notice = page.byId('library-reload-notice'), toasts = notices.length;
    assert.equal(notice.hidden, true);
    replies.push(() => { throw new TypeError('Failed to fetch'); });
    press(inTile('mix', '.library-move-down')); await settle(); await heard();
    assert.equal(notices.length, toasts); assert.equal(notice.hidden, false);
    assert.equal(page.text(page.byId('library-reload-message')), unconfirmed);
    assert.equal(page.text(page.byId('library-reload-btn')), 'Reload to show the latest');
    // The reply came in the move's own task here, so both are read as one message.
    assert.equal(announced(), 'Moved Morning Mix to position 2 in All playlists. ' + unconfirmed);
    // So is a reply that is not JSON (an error page): read out again, and listed once.
    replies.push({ok: false, status: 502, json: async () => { throw new SyntaxError('Unexpected token <'); }});
    press(inTile('mix', '.library-move-down')); await settle(); await heard();
    assert.equal(notices.length, toasts);
    assert.equal(page.text(page.byId('library-reload-message')), unconfirmed);
    assert.equal(announced(), 'Moved Morning Mix to position 3 in All playlists. ' + unconfirmed);
    assert.equal(orders().length, 6); assert.deepEqual(ordered(5), ['fav', 'focus', 'road', 'mix']);
    assert.deepEqual(navigations, []);
""",
    )


def test_a_move_shows_only_the_places_it_changes_by_keyboard_or_drop(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    // Entries typed but not sent: Road Trip's, below the move, and Focus Flow's, inside it.
    const road = inTile('road', '.library-position'), focus = inTile('focus', '.library-position');
    road.value = '1'; focus.value = '3';
    const down = inTile('mix', '.library-move-down');
    down.focus(); replies.push(saved());
    press(down); await settle(); await heard();
    assert.deepEqual(grid('playlist-grid'), ['focus', 'mix', 'road']);
    assert.equal(announced(), 'Moved Morning Mix to position 2 in All playlists.');
    // The rows between the old and the new place show where they are now; Road Trip did
    // not move, so its entry stays as typed.
    assert.deepEqual([place('focus'), place('mix')], [['1', 'true', null], ['2', null, null]]);
    assert.deepEqual(place('road'), ['1', null, 'true']);
    assert.equal(page.document.activeElement, down);
    // A drop takes the same path: Focus Flow dropped on Morning Mix takes its place, and
    // Road Trip still keeps its entry.
    replies.push(saved());
    drag('focus', 'mix'); await settle(); await heard();
    assert.deepEqual(grid('playlist-grid'), ['mix', 'focus', 'road']);
    assert.equal(announced(), 'Moved Focus Flow to position 2 in All playlists.');
    assert.deepEqual([place('mix'), place('focus'), place('road')],
        [['1', 'true', null], ['2', null, null], ['1', null, 'true']]);
    assert.deepEqual([ordered(0), ordered(1)], [['fav', 'focus', 'mix', 'road'], ['fav', 'mix', 'focus', 'road']]);
    // Sending Road Trip's entry moves it there.
    const moveTo = inTile('road', '.library-move-to');
    moveTo.focus(); replies.push(saved());
    press(moveTo); await settle(); await heard();
    assert.deepEqual(grid('playlist-grid'), ['road', 'mix', 'focus']);
    assert.deepEqual([place('road'), place('mix'), place('focus')],
        [['1', 'true', null], ['2', null, null], ['3', null, 'true']]);
    assert.deepEqual(page.errors, []);
""",
    )


def test_every_place_stays_current_as_rows_change_section(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    // Favoriting moves a row out of All playlists, so both sections show their places again.
    replies.push(toggled('is_pinned', true));
    press(inTile('focus', '.fav-btn')); await settle();
    assert.deepEqual([place('fav'), place('focus'), place('mix'), place('road')],
        [['1', 'true', null], ['2', null, 'true'], ['1', 'true', null], ['2', null, 'true']]);
    const range = id => [inTile(id, '.library-position').max, page.text(inTile(id, '.library-position-range'))];
    assert.deepEqual(['fav', 'focus', 'mix', 'road'].map(range),
        [['2', 'of 2'], ['2', 'of 2'], ['2', 'of 2'], ['2', 'of 2']]);
    assert.deepEqual([page.text(page.byId('favorites-count')), page.text(page.byId('playlists-count'))], ['2', '2']);
    // A field being typed in keeps its entry while another row's change is shown.
    const field = inTile('road', '.library-position');
    field.focus(); field.value = '5';
    replies.push(toggled('is_hidden', true));
    press(inTile('mix', '.hide-btn')); await settle();
    assert.deepEqual([field.value, ...range('road')], ['5', '1', 'of 1']);
    assert.deepEqual(place('road').slice(1), ['true', 'true']);
    assert.equal(page.document.activeElement, field);
    // A hidden row has no order controls; shown again, it takes its place in the order.
    assert.equal(inTile('mix', '.library-order').hidden, true);
    press(page.byId('hidden-toggle-btn'));
    replies.push(toggled('is_hidden', false));
    press(inTile('quiet', '.unhide-btn')); await settle();
    assert.deepEqual(grid('playlist-grid'), ['quiet', 'road']);
    assert.equal(inTile('quiet', '.library-order').hidden, false);
    assert.deepEqual(place('quiet'), ['1', 'true', null]);
    assert.equal(inTile('road', '.library-position').max, '2');
    assert.deepEqual(orders(), []);
""",
    )


def test_a_pointer_drop_takes_the_same_order_path_within_its_section(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    // Outside Organize a drag moves nothing.
    drag('road', 'mix'); await settle();
    assert.deepEqual(grid('playlist-grid'), ['mix', 'focus', 'road']); assert.deepEqual(orders(), []);
    organize();
    replies.push(saved());
    drag('road', 'mix'); await settle(); await heard();
    assert.deepEqual(grid('playlist-grid'), ['road', 'mix', 'focus']);
    assert.equal(announced(), 'Moved Road Trip to position 1 in All playlists.');
    assert.deepEqual(ordered(0), ['fav', 'road', 'mix', 'focus']);
    assert.deepEqual(place('road'), ['1', 'true', null]);
    assert.equal(tile('road').classList.contains('sh-library-dragging'), false);
    // Dropped on a row below, it goes after that row.
    replies.push(saved());
    drag('road', 'focus'); await settle(); await heard();
    assert.deepEqual(grid('playlist-grid'), ['mix', 'focus', 'road']);
    assert.equal(announced(), 'Moved Road Trip to position 3 in All playlists.');
    // Focus on a control inside the dragged row stays there.
    const roadUp = inTile('road', '.library-move-up'); roadUp.focus();
    replies.push(saved());
    drag('road', 'mix'); await settle(); await heard();
    assert.deepEqual(grid('playlist-grid'), ['road', 'mix', 'focus']);
    assert.equal(page.document.activeElement, roadUp);
    // A row never moves into another section by drag, and leaves no highlight behind.
    drag('fav', 'mix'); await settle();
    assert.deepEqual(grid('favorites-grid'), ['fav']); assert.equal(orders().length, 3);
    assert.deepEqual(page.$$('.drag-over'), []);
""",
    )


def test_tiles_render_their_saved_state_and_empty_sections_stay_in_the_page(
    node, db_app
):
    html = library_page(db_app, favorites=[], hidden=[], preferences={})
    run_page(
        node,
        html,
        SCENARIO
        + r"""
    // Sections exist while empty so a row can move in; they are hidden until one does.
    assert.equal(page.byId('favorites-section').hidden, true); assert.deepEqual(grid('favorites-grid'), []);
    assert.equal(page.byId('hidden-playlists-section').hidden, true); assert.deepEqual(grid('hidden-grid'), []);
    assert.equal(page.text(page.byId('playlists-count')), '3');
    organize();
    replies.push(toggled('is_pinned', true));
    press(inTile('focus', '.fav-btn')); await settle();
    assert.equal(page.byId('favorites-section').hidden, false); assert.deepEqual(grid('favorites-grid'), ['focus']);
    assert.equal(page.text(page.byId('favorites-count')), '1');
    assert.equal(page.text(page.byId('playlists-count')), '2');
    replies.push(toggled('is_hidden', true));
    press(inTile('road', '.hide-btn')); await settle();
    assert.equal(page.byId('hidden-playlists-section').hidden, false); assert.deepEqual(grid('hidden-grid'), ['road']);
    assert.equal(page.text(page.byId('hidden-count')), '1'); assert.equal(page.text(page.byId('playlists-count')), '1');
    assert.deepEqual(navigations, []); assert.deepEqual(page.errors, []);
""",
    )


def test_an_empty_all_playlists_says_why(node, db_app):
    html = library_page(
        db_app,
        regular=[playlist("mix", "Morning Mix", 40)],
        favorites=[],
        hidden=[],
        preferences={},
    )
    run_page(
        node,
        html,
        SCENARIO
        + r"""
    const empty = page.byId('playlists-empty');
    assert.equal(empty.hidden, true);
    organize();
    replies.push(toggled('is_pinned', true));
    press(inTile('mix', '.fav-btn')); await settle();
    assert.equal(shown(empty), true);
    assert.equal(page.text(empty), 'No other playlists: the rest are in Favorites or Hidden.');
    assert.equal(page.text(page.byId('playlists-count')), '0');
    replies.push(toggled('is_pinned', false));
    press(inTile('mix', '.fav-btn')); await settle();
    assert.equal(empty.hidden, true); assert.deepEqual(grid('playlist-grid'), ['mix']);
""",
    )


def test_a_library_with_no_playlists_says_how_to_load_them(node, db_app):
    html = library_page(db_app, regular=[], favorites=[], hidden=[], preferences={})
    run_page(
        node,
        html,
        SCENARIO
        + r"""
    const empty = page.byId('playlists-empty');
    assert.equal(shown(empty), true);
    assert.equal(page.text(empty), 'No playlists yet. Use Refresh to load your playlists from Spotify.');
    assert.equal(page.text(page.byId('playlists-count')), '0');
    assert.deepEqual(page.errors, []);
""",
    )


def test_favorite_moves_its_tile_in_place_keeping_focus_and_open_results(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    // A pinned row that is hidden keeps its saved favorite on every control.
    assert.equal(tile('gone').dataset.pinned, 'true');
    assert.equal(inTile('gone', '.fav-btn').getAttribute('aria-label'), 'Unfavorite: Hidden Gem');
    // Results retained on this page stay open and untouched by the change.
    await lostShuffle();
    assert.equal(page.byId('live-operation-panel').hidden, false);
    const records = page.window.PlaylistOperations.getRecords().length;
    organize();
    const star = inTile('focus', '.fav-btn');
    star.focus();
    replies.push(toggled('is_pinned', true));
    press(star); await settle();
    assert.equal(calls.at(-1).url, '/api/playlist-preferences/focus/toggle-pinned');
    assert.equal(calls.at(-1).method, 'POST');
    assert.deepEqual(grid('favorites-grid'), ['fav', 'focus']);
    assert.deepEqual(grid('playlist-grid'), ['mix', 'road']);
    assert.equal(tile('focus').dataset.pinned, 'true'); assert.equal(tile('focus').dataset.section, 'favorites');
    assert.equal(page.text(star), 'Unfavorite');
    assert.equal(star.getAttribute('aria-label'), 'Unfavorite: Focus Flow');
    tile('focus').querySelectorAll('.library-star').forEach(icon => {
        assert.equal(icon.classList.contains('sh-library-favorite'), true);
        assert.equal(icon.classList.contains('sh-text-muted'), false);
        assert.equal(icon.getAttribute('fill'), 'currentColor');
    });
    assert.equal(page.text(page.byId('favorites-count')), '2');
    // The moved control keeps focus (a move takes it out, so it is given back), focused
    // normally: the browser scrolls to it only when it is out of view.
    assert.equal(page.document.activeElement, star); assert.equal(Boolean(page.focusOptions.preventScroll), false);
    assert.equal(page.byId('live-operation-panel').hidden, false);
    assert.equal(page.window.PlaylistOperations.getRecords().length, records);
    assert.deepEqual(notices.at(-1), {message: 'Added to Favorites', type: 'success'});
    // Leaving Favorites leads All playlists; the last one out hides the empty section.
    replies.push(toggled('is_pinned', false));
    press(inTile('fav', '.fav-btn')); await settle();
    assert.deepEqual(grid('playlist-grid'), ['fav', 'mix', 'road']);
    assert.equal(inTile('fav', '.fav-btn').getAttribute('aria-label'), 'Favorite: Old Favorite');
    assert.equal(inTile('fav', '.library-star').getAttribute('fill'), 'none');
    replies.push(toggled('is_pinned', false));
    press(star); await settle();
    assert.deepEqual(grid('favorites-grid'), []); assert.equal(page.byId('favorites-section').hidden, true);
    assert.equal(page.text(page.byId('favorites-count')), '0');
    assert.deepEqual(notices.at(-1), {message: 'Removed from Favorites', type: 'success'});
    assert.deepEqual(navigations, []);
""",
    )


def test_hide_and_unhide_swap_controls_and_focus_stays_on_a_tile_that_is_still_shown(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    const hide = inTile('mix', '.hide-btn');
    hide.focus();
    replies.push(toggled('is_hidden', true));
    press(hide); await settle();
    assert.equal(calls.at(-1).url, '/api/playlist-preferences/mix/toggle-hidden');
    assert.deepEqual(grid('hidden-grid'), ['gone', 'quiet', 'mix']);
    assert.deepEqual(grid('playlist-grid'), ['focus', 'road']);
    assert.equal(tile('mix').dataset.hidden, 'true'); assert.equal(tile('mix').dataset.section, 'hidden');
    assert.equal(inTile('mix', '.hide-btn').hidden, true); assert.equal(inTile('mix', '.unhide-btn').hidden, false);
    assert.equal(inTile('mix', '.library-order').hidden, true);
    assert.equal(page.text(page.byId('hidden-count')), '3');
    // The Hidden list is collapsed, so focus goes to the same control on the row now in Morning Mix's place.
    assert.equal(page.document.activeElement, inTile('focus', '.hide-btn'));
    assert.deepEqual(notices.at(-1), {message: 'Playlist hidden', type: 'success'});
    // A hidden favorite returns to Favorites, exactly where a reload places it.
    press(page.byId('hidden-toggle-btn'));
    const show = inTile('gone', '.unhide-btn');
    show.focus();
    replies.push(toggled('is_hidden', false));
    press(show); await settle();
    assert.deepEqual(grid('favorites-grid'), ['fav', 'gone']); assert.equal(tile('gone').dataset.section, 'favorites');
    assert.equal(inTile('gone', '.unhide-btn').hidden, true); assert.equal(inTile('gone', '.hide-btn').hidden, false);
    // The row is still shown after the move, so focus stays on it: its Show became Hide.
    assert.equal(page.document.activeElement, inTile('gone', '.hide-btn'));
    assert.deepEqual(notices.at(-1), {message: 'Playlist shown', type: 'success'});
    // An unpinned one leads All playlists, and the last one out hides the empty section.
    for (const id of ['quiet', 'mix']) {
        inTile(id, '.unhide-btn').focus();
        replies.push(toggled('is_hidden', false));
        press(inTile(id, '.unhide-btn')); await settle();
    }
    assert.deepEqual(grid('playlist-grid'), ['mix', 'quiet', 'focus', 'road']);
    assert.equal(page.byId('hidden-playlists-section').hidden, true);
    assert.equal(page.text(page.byId('hidden-count')), '0');
    assert.equal(page.document.activeElement, inTile('mix', '.hide-btn'));
    assert.deepEqual(navigations, []); assert.equal(writes().length, 4);
""",
    )


def test_organize_drag_state_and_hide_control_follow_the_row(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    const managed = id => [tile(id).draggable, tile(id).classList.contains('organize-mode-active')];
    assert.deepEqual(managed('road'), [true, true]);
    const hide = inTile('road', '.hide-btn');
    hide.focus();
    replies.push(toggled('is_hidden', true));
    press(hide); await settle();
    assert.deepEqual(managed('road'), [false, false]);
    // Focus goes to the same control on the row that took its place.
    assert.equal(page.document.activeElement, inTile('focus', '.hide-btn'));
    press(page.byId('hidden-toggle-btn'));
    replies.push(toggled('is_hidden', false));
    press(inTile('road', '.unhide-btn')); await settle();
    assert.deepEqual(managed('road'), [true, true]);
    assert.equal(inTile('road', '.hide-btn').hidden, false);
""",
    )


def test_focus_on_a_row_that_leaves_view_goes_to_its_replacement_else_the_hidden_toggle(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    // Focus that was on a row that goes into the collapsed Hidden list, on no preference
    // control, lands on the row that took its place: the row itself, never a control in it.
    inTile('mix', '.library-open-playlist').focus();
    replies.push(toggled('is_hidden', true));
    press(inTile('mix', '.hide-btn')); await settle();
    assert.equal(page.document.activeElement, tile('focus'));
    assert.equal(tile('focus').getAttribute('tabindex'), '-1');
    // The last row of a section has none in its place, so the Hidden toggle takes focus.
    inTile('fav', '.library-open-playlist').focus();
    replies.push(toggled('is_hidden', true));
    press(inTile('fav', '.hide-btn')); await settle();
    assert.equal(page.byId('favorites-section').hidden, true);
    assert.equal(page.document.activeElement, page.byId('hidden-toggle-btn'));
    // A row still shown whose control went away keeps focus on the row itself.
    press(page.byId('hidden-toggle-btn'));
    inTile('road', '.library-move-up').focus();
    replies.push(toggled('is_hidden', true));
    press(inTile('road', '.hide-btn')); await settle();
    assert.deepEqual(grid('hidden-grid'), ['gone', 'quiet', 'mix', 'fav', 'road']);
    assert.equal(page.document.activeElement, tile('road'));
""",
    )


def test_refused_or_unconfirmed_preference_changes_leave_the_tile_and_say_why(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    const star = inTile('focus', '.fav-btn');
    replies.push(page.reply({success: false, message: 'Failed to update pin state'}, 500));
    press(star); await settle();
    assert.deepEqual(notices.at(-1), {message: 'Failed to update pin state', type: 'error'});
    assert.equal(tile('focus').dataset.pinned, 'false');
    assert.deepEqual(grid('playlist-grid'), ['mix', 'focus', 'road']);
    assert.equal(page.byId('library-reload-notice').hidden, true);
    // No readable reply: the change may have been saved, so the page says so and offers a reload.
    replies.push(() => { throw new TypeError('Failed to fetch'); });
    press(star); await settle();
    replies.push({ok: false, status: 502, json: async () => { throw new SyntaxError('not JSON'); }});
    press(inTile('road', '.hide-btn')); await settle();
    // A confirmed change the page cannot show is named as saved.
    replies.push(toggled('is_pinned', 'yes'));
    press(star); await settle();
    assert.equal(page.byId('library-reload-notice').hidden, false);
    assert.equal(page.text(page.byId('library-reload-message')),
        'Your change to “Focus Flow” could not be confirmed. ' +
        'Your change to “Road Trip” could not be confirmed. Your change to “Focus Flow” was saved, but this page ' +
        'could not show it.');
    assert.equal(page.$$('#library-reload-notice button').length, 1);
    assert.equal(tile('focus').dataset.pinned, 'false'); assert.equal(tile('road').dataset.hidden, 'false');
    assert.deepEqual(navigations, []);
""",
    )


def test_one_preference_request_per_tile_runs_at_a_time(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    let release; replies.push(() => new Promise(resolve => { release = resolve; }));
    press(inTile('focus', '.fav-btn')); await tick();
    assert.equal(tile('focus').getAttribute('aria-busy'), 'true');
    press(inTile('focus', '.fav-btn')); press(inTile('focus', '.hide-btn')); await tick();
    assert.equal(writes().length, 1);
    replies.push(toggled('is_pinned', true));
    press(inTile('road', '.fav-btn')); await settle();
    assert.equal(writes().length, 2); assert.deepEqual(grid('favorites-grid'), ['fav', 'road']);
    release(toggled('is_pinned', true)); await settle();
    assert.equal(tile('focus').hasAttribute('aria-busy'), false);
    assert.deepEqual(grid('favorites-grid'), ['fav', 'road', 'focus']);
""",
    )


def test_reset_asks_the_shared_confirmation_then_reloads_through_the_guard(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    const reset = page.$('[data-action-click="dash-004"]');
    page.answer = false;
    reset.focus(); press(reset); await settle();
    assert.equal(asked.length, 1);
    // Danger is the shared confirmation's default tone; reset never asks for the plain one.
    assert.equal(asked[0].title, 'Reset Library preferences?'); assert.notEqual(asked[0].tone, 'default');
    assert.equal(asked[0].confirmLabel, 'Reset preferences'); assert.equal(asked[0].trigger, reset);
    assert.match(asked[0].message, /favorites, hidden playlists and tile order.*playlists on Spotify do not change/);
    assert.equal(writes().length, 0);
    page.answer = true;
    replies.push(page.reply({success: true, message: 'Reset 3 playlist preferences'}));
    press(reset); await settle();
    assert.equal(calls.at(-1).url, '/api/playlist-preferences/reset'); assert.equal(calls.at(-1).method, 'POST');
    assert.deepEqual(navigations, ['reload']);
    assert.equal(page.byId('library-reload-notice').hidden, true);
""",
    )


def test_reset_waits_for_an_order_save_already_sending_and_no_order_follows_it(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    const preferenceCalls = () => calls.map(call => call.url)
        .filter(url => url.startsWith('/api/playlist-preferences/'));
    // A move's save is still sending, and a second move queues another.
    let release; replies.push(() => new Promise(resolve => { release = resolve; }));
    const down = inTile('mix', '.library-move-down');
    press(down); await settle();
    press(down); await settle(); await heard();
    // Reset, confirmed, waits for the save that is sending ...
    const reset = page.$('[data-action-click="dash-004"]');
    let answer; replies.push(() => new Promise(resolve => { answer = resolve; }));
    press(reset); await settle();
    assert.deepEqual(preferenceCalls(), ['/api/playlist-preferences/order']);
    release(saved()); await settle();
    assert.deepEqual(preferenceCalls(), ['/api/playlist-preferences/order', '/api/playlist-preferences/reset']);
    // ... and a move while it runs is not sent after it: the reset decides the saved order.
    press(inTile('mix', '.library-move-up')); await settle();
    answer(page.reply({success: true, message: 'Reset 3 playlist preferences'})); await settle(); await settle();
    assert.deepEqual(preferenceCalls(), ['/api/playlist-preferences/order', '/api/playlist-preferences/reset']);
    assert.deepEqual(navigations, ['reload']);
    assert.deepEqual(notices.filter(notice => /order/i.test(notice.message)), []);
    // The page still shows the earlier order: a later reset that fails sends none of it.
    replies.push(page.reply({success: false, message: 'Failed to reset preferences'}, 500));
    press(reset); await settle(); await settle();
    assert.deepEqual(preferenceCalls(), ['/api/playlist-preferences/order', '/api/playlist-preferences/reset',
        '/api/playlist-preferences/reset']);
""",
    )


def test_a_reset_that_fails_leaves_the_order_shown_and_saves_it(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    const preferenceCalls = () => calls.map(call => call.url)
        .filter(url => url.startsWith('/api/playlist-preferences/'));
    let release; replies.push(() => new Promise(resolve => { release = resolve; }));
    press(inTile('mix', '.library-move-down')); await settle(); await heard();
    press(page.$('[data-action-click="dash-004"]')); await settle();
    // The save that was sending is refused, and the reset fails: the order shown is saved then,
    // and only that save's outcome is reported.
    replies.push(page.reply({success: false, message: 'Failed to reset preferences'}, 500));
    replies.push(saved());
    release(page.reply({success: false, message: 'Failed to save playlist order'}, 500));
    await settle(); await settle();
    assert.deepEqual(preferenceCalls(), ['/api/playlist-preferences/order', '/api/playlist-preferences/reset',
        '/api/playlist-preferences/order']);
    assert.deepEqual(ordered(1), ['fav', 'focus', 'mix', 'road']);
    assert.deepEqual(notices.map(notice => notice.message),
        ['Failed to reset preferences', 'Order saved']);
    assert.deepEqual(navigations, []);
""",
    )


def test_a_reset_whose_outcome_is_unknown_sends_no_queued_order(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    const preferenceCalls = () => calls.map(call => call.url)
        .filter(url => url.startsWith('/api/playlist-preferences/'));
    let release; replies.push(() => new Promise(resolve => { release = resolve; }));
    press(inTile('mix', '.library-move-down')); await settle();
    press(inTile('mix', '.library-move-down')); await settle(); await heard();
    press(page.$('[data-action-click="dash-004"]')); await settle();
    // The reset's reply is lost: it may have cleared the order, so the queued order is not sent.
    // The notice names it beside the reset, so the order shown is never taken as saved, and offers
    // the reload that shows what happened.
    replies.push(() => { throw new TypeError('Failed to fetch'); });
    release(saved()); await settle(); await settle(); await heard();
    assert.deepEqual(preferenceCalls(), ['/api/playlist-preferences/order', '/api/playlist-preferences/reset']);
    const unknown = 'The preferences reset could not be confirmed. Your new Library order could not be confirmed.';
    assert.equal(page.byId('library-reload-notice').hidden, false);
    assert.equal(page.text(page.byId('library-reload-message')), unknown);
    assert.equal(announced(), unknown);
    // That order is dropped: a retried reset that is refused sends none of it either.
    replies.push(page.reply({success: false, message: 'Failed to reset preferences'}, 500));
    press(page.$('[data-action-click="dash-004"]')); await settle(); await settle();
    assert.deepEqual(preferenceCalls(), ['/api/playlist-preferences/order', '/api/playlist-preferences/reset',
        '/api/playlist-preferences/reset']);
    // The next move is the user's latest choice: it saves the order shown.
    replies.push(saved());
    press(inTile('mix', '.library-move-up')); await settle(); await heard();
    assert.equal(orders().length, 2);
""",
    )


def test_reset_unconfirmed_names_the_unknown(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    const reset = page.$('[data-action-click="dash-004"]');
    replies.push(page.reply({success: false, message: 'Failed to reset preferences'}, 500));
    press(reset); await settle();
    assert.deepEqual(notices.at(-1), {message: 'Failed to reset preferences', type: 'error'});
    assert.equal(page.byId('library-reload-notice').hidden, true);
    // Without a readable reply the reset may have happened, so the page says so and offers a reload.
    replies.push(() => { throw new TypeError('Failed to fetch'); });
    press(reset); await settle();
    assert.equal(page.byId('library-reload-notice').hidden, false);
    assert.equal(page.text(page.byId('library-reload-message')), 'The preferences reset could not be confirmed.');
    assert.deepEqual(navigations, []); assert.equal(asked.length, 2);
""",
    )


def test_declined_reloads_after_refresh_and_reset_leave_one_persistent_control(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    await lostShuffle();
    const refresh = page.byId('refresh-playlists-btn');
    let release; replies.push(() => new Promise(resolve => { release = resolve; }));
    refresh.focus(); press(refresh); await tick();
    // Waiting keeps the button focusable; a repeat activation sends nothing.
    assert.equal(refresh.getAttribute('aria-disabled'), 'true'); assert.equal(refresh.disabled, false);
    assert.equal(page.document.activeElement, refresh);
    press(refresh); await tick();
    assert.equal(calls.filter(call => call.url === '/api/library?refresh=1' && call.method === 'GET').length, 1);
    release(page.reply({success: true, message: 'Playlists refreshed successfully.'})); await settle();
    assert.equal(refresh.hasAttribute('aria-disabled'), false);
    assert.equal(page.byId('live-operation-leave').hidden, false);
    page.byId('live-operation-leave-stay').click(); await settle();
    // Declining keeps the page and its results, and names the change in the persistent notice.
    assert.deepEqual(navigations, []); assert.equal(page.window.PlaylistOperations.getRecords().length, 1);
    const notice = page.byId('library-reload-notice'), message = page.byId('library-reload-message');
    assert.equal(notice.hidden, false); assert.equal(message.hasAttribute('role'), false);
    await heard();
    assert.equal(page.text(page.byId('live-operation-announcer')),
        'Your playlists were refreshed from Spotify, but this page still shows the earlier list.');
    assert.equal(page.text(message),
        'Your playlists were refreshed from Spotify, but this page still shows the earlier list.');
    organize();
    replies.push(page.reply({success: true, message: 'Reset 3 playlist preferences'}));
    press(page.$('[data-action-click="dash-004"]')); await settle();
    page.byId('live-operation-leave-stay').click(); await settle();
    assert.equal(page.text(message), 'Your playlists were refreshed from Spotify, but this page still shows the ' +
        'earlier list. Your Library preferences were reset, but this page still shows the earlier layout.');
    assert.equal(page.$$('#library-reload-notice button').length, 1);
    await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(notice.hidden, false);
    const reload = page.byId('library-reload-btn');
    assert.equal(page.text(reload), 'Reload to show the latest');
    reload.click(); await settle();
    page.byId('live-operation-leave-confirm').click(); await settle();
    assert.deepEqual(navigations, ['reload']);
    assert.equal(notices.filter(notice => notice.type === 'info').length, 0);
""",
    )


def test_a_failed_refresh_says_so_and_does_not_reload(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    replies.push(page.reply({success: false, message: 'Failed to refresh playlists. Please try again.'}, 500));
    press(page.byId('refresh-playlists-btn')); await settle();
    assert.deepEqual(notices.at(-1), {message: 'Failed to refresh playlists. Please try again.', type: 'error'});
    assert.deepEqual(navigations, []); assert.equal(page.byId('library-reload-notice').hidden, true);
""",
    )


def test_a_verified_undo_shows_its_track_count_and_focus_stays_in_the_results(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    openActions('mix');
    const undo = inTile('mix', '.library-undo');
    assert.equal(undo.classList.contains('hidden'), false);
    assert.equal(page.text(inTile('mix', '.library-track-count')), '40 tracks');
    const visibility = inTile('mix', '.visibility-btn').dataset.public;
    undo.focus();
    await review(undo, 'undo', 'mix', 'Morning Mix');
    assert.equal(page.document.activeElement, page.byId('live-operation-heading'));
    replies.push(page.reply({success: true, message: 'Playlist restored successfully.',
        playlist_state: {current_index: 0}, operation: {kind: 'undo', aggregate_status: 'complete',
        resources: [{resource: 'mix', resource_name: 'Morning Mix',
            step: 'undo', mutation_type: 'track_membership', status: 'complete', dispatched: true, affected_count: 1,
            retryable: false, verification: 'ordered', desired_track_uris: [uri(2), uri(2), uri(1)],
            current_track_uris: [uri(2), uri(2), uri(1)]}],
            bookkeeping: [{kind: 'session_history', status: 'complete'}]}}));
    runReview(); await settle();
    const record = page.window.PlaylistOperations.getRecords()[0];
    assert.equal(record.complete, true); assert.equal(record.displayAttention, undefined);
    assert.equal(page.text(inTile('mix', '.library-track-count')), '3 tracks');
    assert.equal(inTile('mix', '.library-shuffle-form').dataset.trackCount, '3');
    assert.equal(inTile('mix', 'input[name="keep_first"]').max, '3');
    assert.equal(inTile('mix', '.library-quick-shuffle').disabled, false);
    assert.equal(undo.classList.contains('hidden'), true);
    // Focus stays in the results view; it is not moved back to the row.
    assert.equal(page.document.activeElement, page.byId('live-operation-heading'));
    assert.equal(inTile('mix', '.visibility-btn').dataset.public, visibility);
    // A shuffle keeps the count it had.
    await review(inTile('mix', '.library-quick-shuffle'), 'shuffle', 'mix', 'Morning Mix');
    replies.push(page.reply({success: true, message: 'Playlist shuffled successfully.',
        playlist_state: {current_index: 1}, operation: {kind: 'shuffle', aggregate_status: 'complete',
        resources: [{resource: 'mix', resource_name: 'Morning Mix',
            step: 'shuffle', mutation_type: 'track_order', status: 'complete', dispatched: true, affected_count: 3,
            retryable: false, verification: 'ordered', current_track_uris: [uri(1), uri(2), uri(2)]}],
            bookkeeping: [{kind: 'session_history', status: 'complete'}]}}));
    runReview(); await settle();
    assert.equal(page.text(inTile('mix', '.library-track-count')), '3 tracks');
    assert.equal(page.document.activeElement, page.byId('live-operation-heading'));
""",
    )


def test_a_verified_undo_without_its_verified_contents_asks_to_update_the_view(
    node, library
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    openActions('mix');
    await review(inTile('mix', '.library-undo'), 'undo', 'mix', 'Morning Mix');
    replies.push(page.reply({success: true, playlist_state: {current_index: 0}, operation: {kind: 'undo',
        aggregate_status: 'complete', resources: [{resource: 'mix', resource_name: 'Morning Mix', step: 'undo',
            mutation_type: 'track_membership', status: 'complete', dispatched: true, affected_count: 1,
            retryable: false, verification: 'ordered'}],
        bookkeeping: [{kind: 'session_history', status: 'complete'}]}}));
    runReview(); await settle();
    const record = page.window.PlaylistOperations.getRecords()[0];
    assert.equal(record.complete, true); assert.equal(record.displayAttention, 'complete');
    assert.equal(page.text(inTile('mix', '.library-track-count')), '40 tracks');
""",
    )


def test_an_attention_result_never_moves_focus_out_of_the_results(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    await lostShuffle();
    const feedback = inTile('mix', '.library-shuffle-feedback');
    // The outcome is shown on the row, styled as needing attention, but not read out again.
    assert.equal(feedback.hidden, false); assert.equal(feedback.classList.contains('sh-field-error'), true);
    assert.equal(feedback.hasAttribute('role'), false);
    assert.equal(page.document.activeElement, page.byId('live-operation-heading'));
""",
    )


@pytest.mark.parametrize("playlist_id", ["focus", "mix"])
def test_a_favorite_change_keeps_focus_on_its_own_row_when_focus_was_in_its_playlist_actions(
    node, library, playlist_id
):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    const id = OPTIONS_ID;
    openActions(id);
    const visibility = inTile(id, '.visibility-btn');
    visibility.focus();
    assert.equal(page.text(visibility), 'Make private now');
    organize();
    replies.push(toggled('is_pinned', true));
    press(inTile(id, '.fav-btn')); await settle();
    assert.equal(tile(id).dataset.section, 'favorites');
    // The move keeps the row's Playlist actions open and focus where it was, never on a neighbour.
    assert.equal(inTile(id, '.library-playlist-actions').open, true);
    assert.equal(page.document.activeElement, visibility);
""".replace("OPTIONS_ID", repr(playlist_id)),
    )


def test_a_tile_hidden_into_an_open_hidden_list_keeps_focus(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    organize();
    press(page.byId('hidden-toggle-btn'));
    inTile('focus', '.hide-btn').focus();
    replies.push(toggled('is_hidden', true));
    press(inTile('focus', '.hide-btn')); await settle();
    assert.deepEqual(grid('hidden-grid'), ['gone', 'quiet', 'focus']);
    // Still shown, so focus stays on the row: its Hide became Show.
    assert.equal(page.document.activeElement, inTile('focus', '.unhide-btn'));
""",
    )


def test_a_finished_run_adds_no_second_spoken_message_from_the_library(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    const spoken = () => notices.filter(notice => notice.spoken !== false);
    const live = () => page.$$('.sh-library [role="status"], .sh-library [role="alert"], .sh-library [aria-live]');
    assert.deepEqual(live(), []);
    // Shuffle now, then Restore: the shared announcer reports each run; the row only shows it.
    openActions('mix');
    await review(inTile('mix', '.library-quick-shuffle'), 'shuffle', 'mix', 'Morning Mix');
    replies.push(page.reply({success: true, message: 'Playlist shuffled successfully.',
        playlist_state: {current_index: 2}, operation: {kind: 'shuffle', aggregate_status: 'complete',
        resources: [{resource: 'mix', resource_name: 'Morning Mix', step: 'shuffle', mutation_type: 'track_order',
            status: 'complete', dispatched: true, affected_count: 2, retryable: false, verification: 'ordered',
            current_track_uris: [uri(2), uri(1)]}],
        bookkeeping: [{kind: 'session_history', status: 'complete'}]}}));
    runReview(); await settle();
    const feedback = inTile('mix', '.library-shuffle-feedback');
    assert.equal(page.text(feedback), 'Playlist shuffled successfully.');
    assert.equal(feedback.hasAttribute('role'), false);
    assert.deepEqual(spoken(), []); assert.deepEqual(live(), []);
    // Making the playlist private reports through the announcer too, with no toast.
    const visibility = inTile('mix', '.visibility-btn');
    replies.push(page.reply({success: true, context: {version: 1, kind: 'privacy',
        target: {id: 'mix', name: 'Morning Mix'}, label: 'Make playlist private now',
        resources: [{resource: 'mix', resource_name: 'Morning Mix', role: 'target', domain: 'spotify',
            access: 'write', mutation_type: 'metadata', field: 'public'}], required_bookkeeping: [],
        review_binding: {version: 1, scope: SCOPE, before_order: null, history: null},
        current: {public: true, loaded_at: '2026-10-12T08:00:00Z'}, action: {public: false}}}));
    const run = page.window.LibraryPlaylistActions.setVisibility('mix', visibility); await settle();
    replies.push(page.reply({success: true, public: false, message: 'Playlist set to private',
        operation: {kind: 'privacy', aggregate_status: 'complete', bookkeeping: [],
            resources: [{resource: 'mix', resource_name: 'Morning Mix', step: 'privacy', mutation_type: 'metadata',
                status: 'complete', dispatched: true, affected_count: 1, retryable: false,
                verification: 'metadata'}]}}));
    runReview(); assert.equal((await run).complete, true); await settle();
    assert.equal(visibility.dataset.public, 'false');
    assert.equal(page.text(visibility), 'Make public now');
    assert.deepEqual(spoken(), []); assert.deepEqual(live(), []);
""",
    )


@pytest.mark.parametrize("stage", ["prepare", "run"])
def test_a_client_error_the_controller_never_reports_is_read_as_an_alert(node, library, stage):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    const stage = STAGE;
    const operations = page.window.PlaylistOperations;
    if (stage === 'prepare') operations.prepare = async () => { throw new Error('This review could not start.'); };
    else {
        operations.prepare = async () => ({kind: 'shuffle'});
        operations.run = async () => { throw new Error('This run could not start.'); };
    }
    openActions('mix');
    press(inTile('mix', '.library-quick-shuffle')); await settle();
    const feedback = inTile('mix', '.library-shuffle-feedback');
    assert.equal(feedback.getAttribute('role'), 'alert');
    assert.equal(page.text(feedback),
        stage === 'prepare' ? 'This review could not start.' : 'This run could not start.');
    assert.equal(writes().length, 0);
""".replace("STAGE", repr(stage)),
    )


def test_closing_results_after_undo_to_the_first_version_returns_focus_to_its_named_card(node, library):
    run_page(
        node,
        library,
        SCENARIO
        + r"""
    openActions('mix');
    const undo = inTile('mix', '.library-undo');
    undo.focus();
    await review(undo, 'undo', 'mix', 'Morning Mix');
    replies.push(page.reply({success: true, message: 'Playlist restored successfully.',
        playlist_state: {current_index: 0}, operation: {kind: 'undo', aggregate_status: 'complete',
        resources: [{resource: 'mix', resource_name: 'Morning Mix', step: 'undo', mutation_type: 'track_membership',
            status: 'complete', dispatched: true, affected_count: 1, retryable: false, verification: 'ordered',
            current_track_uris: [uri(2), uri(2), uri(1)]}],
        bookkeeping: [{kind: 'session_history', status: 'complete'}]}}));
    runReview(); await settle();
    assert.equal(undo.classList.contains('hidden'), true);
    // Restore, which opened the results, is gone; closing them returns focus to that row
    // itself, never to a control on it that writes, and the row is a list item named by its title.
    page.key(page.document.activeElement, 'Escape'); await settle();
    assert.equal(page.byId('live-operation-panel').hidden, true);
    const row = tile('mix');
    assert.equal(page.document.activeElement, row); assert.equal(row.getAttribute('tabindex'), '-1');
    assert.equal(row.localName, 'li'); assert.equal(row.parentElement.localName, 'ol');
    assert.equal(row.hasAttribute('role'), false);
    assert.equal(page.text(page.byId(row.getAttribute('aria-labelledby'))), 'Morning Mix');
""",
    )


# Classes or rules that would hide an action until a pointer hovers.
HOVER_ONLY = re.compile(r"(?:group|peer)-hover:|(?<![\w-])hover:(?:block|flex|grid|inline|visible|opacity)")
HOVER_RULE = re.compile(r"([^{}]*:hover[^{}]*)\{([^{}]*)\}")


def test_no_library_action_waits_for_a_hover():
    for path in [
        "shuffify/templates/dashboard.html",
        "shuffify/templates/macros/cards.html",
        "shuffify/static/js/library.js",
        "shuffify/static/js/library-shuffle.js",
    ]:
        assert not HOVER_ONLY.search((ROOT / path).read_text()), path
    for path in ["assets/design/pages/library.css", "assets/design/components/library.css"]:
        css = re.sub(r"/\*.*?\*/", "", (ROOT / path).read_text(), flags=re.S)
        for selector, body in HOVER_RULE.findall(css):
            assert not re.search(r"\b(?:display|visibility|opacity)\s*:", body), (path, selector)


def test_the_hover_guard_can_see_a_hover_only_action():
    assert HOVER_ONLY.search('<div class="opacity-0 group-hover:opacity-100">')
    assert HOVER_ONLY.search('class="hidden hover:block"')
    assert not HOVER_ONLY.search('class="sh-library-quiet-control"')
    assert HOVER_RULE.findall(".row:hover .actions { opacity: 1; }")


LIBRARY_CSS = ["assets/design/components/library.css", "assets/design/pages/library.css"]
# A selector for a state: an attribute, a pseudo-class, or a second class on one element.
STATE_SELECTOR = re.compile(r"\[|(?<!:):(?!:)|\.[\w-]+\.")


def css_rules(path):
    """Each style rule of a stylesheet: (its @screen/@media context, selectors, declarations)."""
    css = re.sub(r"/\*.*?\*/", "", (ROOT / path).read_text(), flags=re.S)
    rules, preludes, start = [], [], 0
    for brace in re.finditer(r"[{}]", css):
        text, start = " ".join(css[start : brace.start()].split()), brace.end()
        if brace[0] == "{":
            preludes.append(text)
            continue
        prelude = preludes.pop()
        if not prelude.startswith("@"):
            context = tuple(outer for outer in preludes if not outer.startswith("@layer"))
            declarations = frozenset(part.strip() for part in text.split(";") if part.strip())
            rules.append((context, tuple(part.strip() for part in prelude.split(",")), declarations))
    return rules


def properties(declarations):
    return {declaration.split(":")[0].strip() for declaration in declarations}


def elements(nodes, parent=None):
    """Every element of a page tree: (tag, attributes, classes, parent element)."""
    for item in nodes:
        if isinstance(item, dict):
            attributes = dict(item["a"])
            element = (item["t"], attributes, set(attributes.get("class", "").split()), parent)
            yield element
            yield from elements(item["c"], element)


def test_the_library_page_file_only_composes_and_each_recipe_is_written_once(library):
    component, page = (css_rules(path) for path in LIBRARY_CSS)
    # A state belongs to its recipe, styled once in the component file, never by the page.
    assert [s for _, selectors, _ in page for s in selectors if STATE_SELECTOR.search(s)] == []
    # The page never restyles a component selector, nor sets what a component recipe on
    # the same element sets (a variant is a recipe too).
    styled = {(context, s) for context, selectors, _ in component for s in selectors}
    assert [s for context, selectors, _ in page for s in selectors if (context, s) in styled] == []
    recipes = {}
    for _, selectors, declarations in component:
        for selector in selectors:
            if re.fullmatch(r"\.[\w-]+", selector):
                recipes.setdefault(selector[1:], set()).update(properties(declarations))
    tree = list(elements(page_tree(library)))
    for _, selectors, declarations in page:
        for selector in selectors:
            subject = set(re.findall(r"\.([\w-]+)", re.split(r"[\s>+~]+", selector)[-1]))
            for _, _, classes, _ in tree:
                if subject and subject <= classes:
                    recipe = set().union(*(recipes.get(name, set()) for name in classes))
                    assert not recipe & properties(declarations), (selector, sorted(classes))
    # Rules that say the same thing are one rule naming every selector.
    blocks = Counter((context, declarations) for context, _, declarations in page)
    assert [sorted(block) for (_, block), count in blocks.items() if count > 1] == []
    # One rule turns every disclosure chevron, from the disclosure's own state, and every
    # chevron sits where it applies: in an open details element's summary, or directly in a
    # control that says it is expanded.
    turns = [selectors for _, selectors, declarations in component + page
             if any("chevron" in s for s in selectors) and "transform" in properties(declarations)]
    assert turns == [("details[open] > summary .sh-library-chevron",
                      '[aria-expanded="true"] > .sh-library-chevron')]
    chevrons = [parent for _, _, classes, parent in tree if "sh-library-chevron" in classes]
    assert len(chevrons) == 7  # each row's Playlist actions, and the Hidden toggle
    for parent in chevrons:
        summary = parent
        while summary and summary[0] != "summary":
            summary = summary[3]
        assert "aria-expanded" in parent[1] or (summary and summary[3][0] == "details"), parent[:3]


NATIVE_DIALOG_GUARDED_FILES = [
    "shuffify/templates/dashboard.html",
    "shuffify/templates/macros/cards.html",
    "shuffify/templates/schedules.html",
    "shuffify/static/js/library.js",
    "shuffify/static/js/library-shuffle.js",
    "shuffify/static/js/routine-management.js",
    "shuffify/static/js/base.js",
]
# A native dialog call: bare, or through window/self/globalThis. Methods such as
# ScheduleEdit.confirm(...) and AppConfirm.ask(...) are not native dialogs.
NATIVE_DIALOG = re.compile(
    r"(?<![\w$.])(?:confirm|alert|prompt)\s*\(|"
    r"\b(?:window|self|globalThis)\s*\.\s*(?:confirm|alert|prompt)\s*\("
)
COMMENTS = re.compile(
    r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/|"
    r"<!--.*?-->|\{#.*?#\}",
    re.S,
)


def _code(text):
    # Comments go; quoted text stays, so a dialog call built in a string is still found.
    return COMMENTS.sub(lambda match: match[0] if match[0][0] in "\"'`" else "", text)


@pytest.mark.parametrize("path", NATIVE_DIALOG_GUARDED_FILES)
def test_library_and_routines_use_no_native_dialogs(path):
    assert not NATIVE_DIALOG.search(_code((ROOT / path).read_text())), path


def test_the_native_dialog_guard_can_see_a_violation():
    for call in [
        "if (!confirm('Delete?')) return;",
        "window.confirm('x')",
        "alert(message)",
        "const name = prompt('Name')",
        "self.alert('x')",
    ]:
        assert NATIVE_DIALOG.search(_code(call)), call
    for safe in [
        "ScheduleEdit.confirm({title: 'x'})",
        "AppConfirm.ask({})",
        "// confirm('old')",
        "/* alert(x) */",
        "confirmLabel: 'Delete'",
    ]:
        assert not NATIVE_DIALOG.search(_code(safe)), safe
