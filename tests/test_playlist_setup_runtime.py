"""Shared playlist setup page, on the rendered page with its own scripts.

Staging, Undo, Back, the section links and Refresh never change anything: the only
requests that do are Save playlist settings (PUT settings) and Create playlist in Spotify
(POST spotify-playlists); Review changes asks for a read-only impact preview. Save moves
through Saved, Conflict, Busy, Error and Unknown, and an Unknown save is reconciled by
reading the context, never sent again. Creation is one request per deliberate press, and an
Unknown creation blocks Create until Refresh my playlists. Leaving follows the page's leave guard.
The page's real scripts run over its real markup (see tests/page_runtime.py); the network
and the shared confirmation are test doubles, and no server route is involved.
"""

import copy
import json
import re
from pathlib import Path

import pytest
from flask import render_template, session

from tests.page_runtime import page_tree, render_page, run_page
from tests.paths import api_path
from tests.test_playlist_operations_runtime import node as node  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
TARGET = {"id": "target", "name": "Morning Mix"}
BASE = "/playlist/target/setup"
URLS = {
    "context": api_path(BASE + "/context"),
    "impact": api_path(BASE + "/impact"),
    "settings": api_path(BASE + "/settings"),
    "create": api_path(BASE + "/spotify-playlists"),
    "playlists": api_path(BASE + "/playlists"),
}
REVISION = "a" * 64
INDIE = "4uLU6hMCjMI75M1A2tKUQC"
FRESH = "37i9dQZF1DXcBWIGoYBM5M"
HOSTILE = '<img src=x onerror="alert(1)"> & "quotes"'


def routine(schedule_id, job_type, operation, timing, enabled=True, **uses):
    return {
        "id": schedule_id,
        "job_type": job_type,
        "operation": operation,
        "timing_text": timing,
        "is_enabled": enabled,
        "uses": {
            "sources": uses.get("sources", False),
            "collection": uses.get("collection", False),
            "drip": uses.get("drip", False),
            "archive": uses.get("archive", False),
        },
        "drip_count_override": None,
    }


def source(source_id, name, playlist_id=None, query=None, count=5, kind="external"):
    return {
        "id": source_id,
        "type": "search_query" if query else kind,
        "playlist_id": playlist_id,
        "search_query": query,
        "name": name,
        "raid_count": count,
    }


def setup_context(**changes):
    context = {
        "version": 1,
        "target": TARGET,
        "sources": [
            source(5, "Indie Finds", playlist_id=INDIE),
            source(6, "rainy day", query="rainy day", count=10),
        ],
        "routine_only_sources": [{"playlist_id": "ghost1", "routine_ids": [18]}],
        "collection": {
            "playlist_id": "inbox",
            "name": "Mix Inbox",
            "drip_enabled": True,
            "drip_count": 3,
        },
        "archive": None,
        "routines": [
            routine(18, "raid", "Collect", "Every day at 07:30 UTC", sources=True),
            routine(
                19,
                "raid_and_drip",
                "Collect and add",
                "Every Monday at 09:00 UTC",
                enabled=False,
                sources=True,
                drip=True,
            ),
        ],
        "limits": {"max_sources": 10, "raid_count": [1, 100], "drip_count": [1, 50]},
        "configuration_revision": REVISION,
        "section_digests": {
            "sources": "1" * 64,
            "collection": "2" * 64,
            "archive": "3" * 64,
            "routines": "4" * 64,
        },
        "state_digest": "5" * 64,
    }
    context.update(changes)
    return context


def setup_page(db_app, context=None, section="sources", target=TARGET, back=None):
    return render_page(
        db_app,
        "playlist_setup.html",
        path=BASE + "?section=" + section,
        target=target,
        setup_context=copy.deepcopy(context or setup_context()),
        setup_urls=URLS,
        return_link=back or {"href": "/workshop/target", "label": "Back to playlist"},
        section=section,
    )


@pytest.fixture
def setup(db_app):
    return setup_page(db_app)


# The page, its network doubles and helpers. Reads (GET) answer from the route table: the
# saved context (reads.saved, which a scenario replaces to change what a read returns) and
# the owned playlists (reads.owned). Writes and the impact preview need a queued reply.
SCENARIO = r"""
const CONTEXT = __CONTEXT__, URLS = __URLS__;
const OWNED = [
    {id: 'target', name: 'Morning Mix', track_count: 40, image_url: null, owned: true},
    {id: 'inbox', name: 'Mix Inbox', track_count: 3, image_url: null, owned: true},
    {id: 'keep', name: 'Keepers', track_count: 12, image_url: null, owned: true},
    {id: 'shared', name: 'Team Mix', track_count: 7, image_url: null, owned: false},
];
const clone = value => JSON.parse(JSON.stringify(value));
const reads = {saved: clone(CONTEXT), owned: OWNED.slice(), contextFails: false, playlistsFail: false};
function route(url, init) {
    if ((init.method || 'GET') !== 'GET') return null;
    if (url === URLS.context) {
        if (reads.contextFails) return () => { throw new Error('read lost'); };
        return page.reply(Object.assign({success: true}, clone(reads.saved)));
    }
    if (url.startsWith(URLS.playlists)) {
        if (reads.playlistsFail) return () => { throw new Error('read lost'); };
        return page.reply({success: true, playlists: clone(reads.owned)});
    }
    return null;
}
// The browser follows the action footer's size; each box the page asks it to observe is recorded.
const observed = [];
loadPage(TREE, {path: '/playlist/target/setup', route, before: window => {
    window.ResizeObserver = class { observe(target) { observed.push(target); } disconnect() {} };
}});
const {calls, replies, navigations, asked} = page;
// Every announcement, in order: each outcome is spoken once.
const spoken = [];
const announce = page.window.AppPanels.announce;
page.window.AppPanels.announce = message => { spoken.push(message); announce(message); };
const said = message => spoken.filter(item => item === message).length;
const $ = id => page.byId(id);
const text = node => page.text(node);
const shown = node => Boolean(node) && node.getClientRects().length > 0;
const focused = () => page.document.activeElement;
// Activates a control the way a person can: only while it is shown.
function press(node) {
    assert.ok(shown(node), 'not shown: ' + (node && (node.id || node.getAttribute('data-setup-focus'))));
    node.focus();
    node.click();
}
function type(node, value) {
    node.focus();
    node.value = value;
    page.input(node);
}
async function commit(node, value) {
    type(node, value);
    page.change(node);
    await heard();
}
// A native same-tab link click, as the browser dispatches it: true when the page stays.
function follow(link) {
    const event = page.event('click', {bubbles: true, cancelable: true, button: 0});
    link.focus();
    link.dispatchEvent(event);
    return event.defaultPrevented;
}
const byFocus = key => page.$$('[data-setup-focus]')
    .find(node => node.getAttribute('data-setup-focus') === key) || null;
const pendingLabels = () => page.$$('#setup-pending-list li').map(item => text(item.querySelector('.sh-detail')));
const pendingUndo = label => page.$$('#setup-pending-list li').find(item =>
    text(item.querySelector('.sh-detail')) === label).querySelector('[data-setup-undo]');
const rows = id => page.$$('#' + id + ' > li').map(item => text(item.querySelector('.sh-setup-row-text')));
const options = id => $(id).options.map(option => option.value);
// Requests that change something: Save (PUT settings) and Create (POST spotify-playlists).
const mutations = () => calls.filter(call => call.method === 'PUT' || call.url === URLS.create);
const reviews = () => calls.filter(call => call.url === URLS.impact);
const bodyOf = call => JSON.parse(call.body);
const disabled = node => node.getAttribute('aria-disabled') === 'true';
const status = () => ($('setup-status').hidden ? '' : text($('setup-status')));
const outcome = () => ($('setup-outcome').hidden ? null :
    {heading: text($('setup-outcome-heading')), text: text($('setup-outcome-text'))});
const TEXT = {
    saved: 'Settings saved. Existing routines now use these settings. No routine was started.',
    savedNone: 'Settings saved. No existing routines are affected. This does not schedule anything.',
    ackAffected: 'I understand these existing routines will use these settings. ' +
        'Saving does not start or schedule any routine.',
    ackNone: 'No existing routines are affected. This does not schedule anything.',
    expired: 'This review expired. Review the changes again.',
    busy: 'A routine or save is running on this playlist. Try again when it finishes.',
    unknownSave: 'Could not confirm settings save.',
    notConfirmed: 'Your changes are not in your saved settings yet. A save may still be finishing. ' +
        'Check again, or review the changes again.',
    creationUnknown: 'We could not confirm whether the playlist was created. ' +
        'Check your Spotify playlists before creating another.',
    leaveSaving: "Saving can't be cancelled by leaving; wait for it to finish.",
};
const ACK = {version: 1, issued_at: 1760000000, signature: 'f'.repeat(64)};
function effect(id, operation, timing, enabled, effects = ['will use this source on its next run']) {
    return {id, operation, timing_text: timing, is_enabled: enabled, effects};
}
function impactReply(affected = [effect(18, 'Collect', 'Every day at 07:30 UTC', true)], extra = {}) {
    return page.reply(Object.assign({success: true, configuration_revision: CONTEXT.configuration_revision,
        changes: [{op: 'remove_source', source_id: 5, summary: 'Remove source: Indie Finds (playlist)'}],
        affected_routines: affected, unaffected_routines: [], notes: [],
        expected_state_digest: 'e'.repeat(64), acknowledgment: ACK}, extra));
}
function savedContext(changes = {}) {
    return Object.assign(clone(CONTEXT), {sources: CONTEXT.sources.filter(item => item.id !== 5),
        configuration_revision: 'b'.repeat(64), state_digest: 'e'.repeat(64),
        section_digests: Object.assign({}, CONTEXT.section_digests, {sources: '9'.repeat(64)})}, changes);
}
async function stageRemoval() {
    press(byFocus('source-remove-5'));
    await settle();
}
// Stages Remove Indie Finds, reviews it and checks the acknowledgment: Save is then available.
async function reviewed(reply = impactReply()) {
    await stageRemoval();
    replies.push(reply);
    press($('setup-review-btn'));
    await settle();
    press($('setup-ack'));
    await settle();
    assert.equal(disabled($('setup-save')), false);
}
// A Save whose reply the scenario releases: {release(reply)}; the PUT is already sent.
function holdSave() {
    let release;
    replies.push(() => new Promise(resolve => { release = resolve; }));
    press($('setup-save'));
    return {release: reply => release(reply)};
}
"""


def scenario(body, context=None):
    script = SCENARIO.replace("__CONTEXT__", json.dumps(context or setup_context()))
    return script.replace("__URLS__", json.dumps(URLS)) + body


def run(node, html, body, context=None):
    return run_page(
        node, html, scenario(body + "\nassert.deepEqual(page.errors, []);", context)
    )


def test_the_page_names_its_playlist_sections_and_saved_settings(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    assert.equal(page.$$('h1').length, 1);
    assert.equal(text($('setup-title')), 'Playlist setup · Morning Mix');
    assert.equal(text($('setup-boundary')), 'Settings for this playlist · Save applies immediately');
    assert.equal(text($('setup-summary-heading')), 'Saved settings for this playlist');
    assert.equal($('setup-footer').getAttribute('aria-label'), 'Playlist setup actions');
    assert.match(text($('setup-identity')), /Morning Mix/);
    // Under the h1: the playlist identity, then the boundary line.
    const header = ['setup-title', 'setup-identity', 'setup-boundary'];
    assert.deepEqual(page.$$('#setup-page *').map(node => node.id).filter(id => header.includes(id)), header);
    assert.equal($('setup-back').getAttribute('href'), '/workshop/target');
    assert.equal(text($('setup-back')), 'Back to playlist');
    // Ordinary in-page links to three labelled sections.
    assert.deepEqual(page.$$('#setup-nav a').map(link => link.getAttribute('href')),
        ['#setup-sources', '#setup-collection', '#setup-archive']);
    for (const [id, name] of [['setup-sources', 'Sources'], ['setup-collection', 'Collection'],
        ['setup-archive', 'Archive']]) {
        const section = $(id);
        assert.equal(section.localName, 'section');
        assert.equal(text($(section.getAttribute('aria-labelledby'))), name);
    }
    // ?section= picks the section focus starts at.
    assert.equal(focused(), $('setup-sources-heading'));
    // Every field has a real label.
    page.$$('input, select').forEach(field => {
        const label = page.$$('label').find(item => item.getAttribute('for') === field.id) || field.closest('label');
        assert.ok(label && text(label), 'unlabelled: ' + field.id);
    });
    assert.deepEqual(rows('setup-source-list'), ['Indie Finds Playlist', 'rainy day Search words']);
    assert.deepEqual(page.$$('#setup-source-list > li').map(item => item.getAttribute('data-source-id')), ['5', '6']);
    assert.equal(byFocus('source-count-5').value, '5');
    assert.equal(byFocus('source-count-6').value, '10');
    assert.equal(text($('setup-source-limit')), '2 of 10 sources');
    assert.deepEqual(rows('setup-routine-only-list'), ['ghost1 Used only by routine #18: not a saved source']);
    assert.equal(text(byFocus('source-remove-5')), 'Remove: Indie Finds');
    // The collection is shown first, with Replace and Unlink; Drip says what it applies to.
    assert.match(text($('setup-collection-current')), /^Mix Inbox Collection playlist Replace/);
    assert.deepEqual($('setup-collection-current').children.map(node => node.localName), ['div']);
    assert.equal($('setup-collection-choose').hidden, true);
    assert.equal(shown($('setup-drip')), true);
    assert.equal($('setup-drip-enabled').checked, true);
    assert.equal($('setup-drip-count').value, '3');
    assert.equal(text($('setup-drip-enabled-help')), 'Applies to saved routine runs.');
    // Both toggles are the shared toggle field, described by their note and their error line.
    for (const id of ['setup-drip-enabled', 'setup-auto-archive']) {
        assert.equal($(id).closest('label').className, 'sh-toggle');
        assert.equal($(id).getAttribute('aria-describedby'), id + '-help ' + id + '-error');
        assert.equal($(id).hasAttribute('name'), false);
    }
    assert.equal(text($('setup-auto-archive-help')), 'Used by Workshop drafts only.');
    // No archive yet: use an existing playlist or create one; the list leaves out this playlist.
    assert.equal(text($('setup-archive-current')), 'No archive playlist yet.');
    assert.equal(shown($('setup-archive-use')), true);
    assert.equal(text($('setup-archive-use')), 'Use existing playlist');
    assert.equal(text($('setup-archive-refresh')), 'Refresh my playlists');
    assert.equal(text($('setup-archive-create')), 'Create playlist in Spotify');
    assert.deepEqual(options('setup-archive-select'), ['', 'inbox', 'keep', 'shared']);
    assert.equal($('setup-archive-select').options[3].textContent, 'Team Mix (7 tracks) · shared with you');
    assert.equal(shown($('setup-auto-archive-group')), false);
    // The summary holds the saved settings and the routines with their state in words.
    assert.deepEqual(page.$$('#setup-saved dd').map(text), ['2 sources: Indie Finds, rainy day',
        'Mix Inbox · adds 3 per run', 'None']);
    assert.deepEqual(page.$$('#setup-routines li').map(text), [
        'Collect · Every day at 07:30 UTC (#18) — Active',
        'Collect and add · Every Monday at 09:00 UTC (#19) — Paused']);
    assert.equal(text($('setup-pending-count')), 'No pending changes');
    assert.equal(disabled($('setup-review-btn')), true);
    assert.equal(disabled($('setup-save')), true);
    assert.equal($('setup-save').disabled, false);
    // The actions are the shared sticky action footer, whose size the page follows (AppPanels);
    // the Workshop's own module is not loaded here.
    assert.deepEqual(observed, [$('setup-footer')]);
    assert.ok($('setup-footer').classList.contains('sh-action-footer'));
    assert.equal($('setup-footer').hasAttribute('data-action-footer'), true);
    assert.deepEqual(page.$$('script').map(script => script.getAttribute('src') || '')
        .filter(source => source.includes('playlist-workspace')), []);
    // Loading reads the owned playlists for the chooser and nothing else.
    assert.deepEqual(calls.map(call => call.method + ' ' + call.url), ['GET ' + URLS.playlists]);
""",
    )


@pytest.mark.parametrize("section", ["collection", "archive"])
def test_the_section_parameter_picks_where_focus_starts(node, db_app, section):
    run(
        node,
        setup_page(db_app, section=section),
        r"""
    assert.equal(focused(), $('setup-__SECTION__-heading'));
""".replace("__SECTION__", section),
    )


def test_browsing_staging_undo_refresh_and_back_send_no_change(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    // Section links stay on the page.
    page.$$('#setup-nav a').forEach(link => assert.equal(follow(link), false));
    // Add by link and by search words; change a count; remove a source and a routine-only one.
    type($('setup-add-playlist-input'), 'https://open.spotify.com/playlist/' + '__FRESH__' + '?si=abc');
    press($('setup-add-playlist'));
    assert.equal($('setup-add-playlist-input').value, '');
    type($('setup-add-search-input'), '  late night  ');
    press($('setup-add-search'));
    await commit(byFocus('source-count-5'), '8');
    press(byFocus('source-remove-6'));
    press(byFocus('routine-source-0-remove'));
    assert.equal(text($('setup-source-limit')), '3 of 10 sources');
    // Replace the collection with an existing playlist; selecting alone stages nothing.
    press(byFocus('collection-replace'));
    await settle();
    assert.equal(byFocus('collection-replace').getAttribute('aria-expanded'), 'true');
    assert.equal(focused(), byFocus('collection-replace'));
    // Its own playlist is not offered as its replacement, nor is the playlist being set up.
    assert.deepEqual(options('setup-collection-select'), ['', 'keep', 'shared']);
    $('setup-collection-select').value = 'keep';
    page.change($('setup-collection-select'));
    assert.equal(pendingLabels().length, 5);
    press($('setup-collection-use'));
    assert.equal(focused(), $('setup-collection'));
    press($('setup-drip-enabled'));
    await commit($('setup-drip-count'), '5');
    // Read the playlists again, then pair an archive and turn auto-archive off.
    press($('setup-archive-refresh'));
    await settle();
    $('setup-archive-select').value = 'shared';
    press($('setup-archive-use'));
    assert.equal(shown($('setup-auto-archive-group')), true);
    assert.equal($('setup-auto-archive').checked, true);
    press($('setup-auto-archive'));
    assert.deepEqual(pendingLabels(), [
        'Add source: a playlist from a link (named when you review)',
        'Add source: “late night” (search)',
        'Indie Finds: collect 8 per run (was 5)',
        'Remove source: rainy day',
        'Stop routines collecting from ghost1',
        'Collection: replace Mix Inbox with Keepers',
        'Collection: stop adding collected tracks automatically, add 5 per run',
        'Archive: use Team Mix',
        'Archive: stop auto-archiving removed tracks']);
    assert.equal(text($('setup-pending-count')), '9 pending changes');
    assert.deepEqual(rows('setup-source-list').slice(2), [
        'a playlist from a link (named when you review) Pending: add', '“late night” (search) Pending: add']);
    // Undo removes just that change; focus moves to the next change's Undo.
    const next = pendingUndo('Indie Finds: collect 8 per run (was 5)');
    press(pendingUndo('Add source: “late night” (search)'));
    assert.equal(focused().getAttribute('data-setup-undo'), next.getAttribute('data-setup-undo'));
    assert.equal(pendingLabels().length, 8);
    // Back asks before discarding; staying keeps everything.
    page.answer = false;
    assert.equal(follow($('setup-back')), true);
    await settle();
    assert.equal(asked.length, 1);
    assert.equal(asked[0].title, 'Discard unsaved settings changes?');
    assert.equal(asked[0].confirmLabel, 'Discard and leave');
    assert.equal(asked[0].cancelLabel, 'Stay');
    assert.equal(asked[0].trigger, $('setup-back'));
    assert.deepEqual(navigations, []);
    assert.equal(pendingLabels().length, 8);
    // Nothing above changed anything: reads only.
    assert.deepEqual(mutations(), []);
    assert.deepEqual([...new Set(calls.map(call => call.method))], ['GET']);
    assert.deepEqual(calls.map(call => call.url), [URLS.playlists, URLS.playlists + '?refresh=1']);
    // Review changes asks for the read-only impact of the staged list, in order.
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    assert.deepEqual(mutations(), []);
    assert.equal(reviews().length, 1);
    assert.equal(reviews()[0].method, 'POST');
    assert.deepEqual(bodyOf(reviews()[0]), {configuration_revision: CONTEXT.configuration_revision, changes: [
        {op: 'add_playlist_source', playlist_id: '__FRESH__'},
        {op: 'set_source_count', source_id: 5, raid_count: 8},
        {op: 'remove_source', source_id: 6},
        {op: 'remove_routine_source', playlist_id: 'ghost1'},
        {op: 'replace_collection', playlist_id: 'keep'},
        {op: 'set_drip', drip_enabled: false, drip_count: 5},
        {op: 'pair_archive', playlist_id: 'shared'},
        {op: 'set_auto_archive', value: false}], section_digests: CONTEXT.section_digests});
""".replace("__FRESH__", FRESH),
    )


def test_review_then_acknowledge_then_save_adopts_the_saved_settings(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await stageRemoval();
    assert.equal(focused(), byFocus('source-undo-5'));
    const stops = ['will stop collecting from Indie Finds'];
    replies.push(impactReply([effect(18, 'Collect', 'Every day at 07:30 UTC', true, stops),
        effect(19, 'Collect and add', 'Every Monday at 09:00 UTC', false, stops)],
        {unaffected_routines: [{id: 20, operation: 'Add collected tracks', reason: 'does not collect'}],
            notes: ['Collect stays enabled but cannot collect discoveries until a source is added.']}));
    press($('setup-review-btn'));
    assert.equal(text($('setup-review-btn')), 'Reviewing…');
    await settle();
    // The review names every affected routine with its state in words, then asks.
    assert.equal(focused(), $('setup-review-heading'));
    assert.deepEqual(page.$$('#setup-review-changes li').map(text), ['Remove source: Indie Finds (playlist)']);
    assert.deepEqual(page.$$('#setup-review-affected li').map(text), [
        'Collect · Every day at 07:30 UTC (#18) — Active — will stop collecting from Indie Finds',
        'Collect and add · Every Monday at 09:00 UTC (#19) — Paused — will stop collecting from Indie Finds']);
    assert.deepEqual(page.$$('#setup-review-unaffected li').map(text),
        ['Add collected tracks (#20) — does not collect']);
    assert.deepEqual(page.$$('#setup-review-notes li').map(text),
        ['Collect stays enabled but cannot collect discoveries until a source is added.']);
    assert.equal(text($('setup-ack-text')), TEXT.ackAffected);
    assert.equal($('setup-ack').type, 'checkbox');
    assert.equal($('setup-ack').checked, false);
    // Checking it changes only the footer: no list is rebuilt under the person.
    const listed = page.$('#setup-pending-list > li');
    // Save waits for the acknowledgment; a press before it is refused and sends nothing.
    assert.equal(disabled($('setup-save')), true);
    press($('setup-save'));
    await settle();
    assert.deepEqual(mutations(), []);
    assert.equal(status(), 'Check the acknowledgment in the review first.');
    press($('setup-ack'));
    assert.equal(disabled($('setup-save')), false);
    assert.equal(page.$('#setup-pending-list > li'), listed);
    assert.equal(focused(), $('setup-ack'));
    // Saving is marked busy and keeps focus; a second press is refused.
    const held = holdSave();
    await tick();
    const save = $('setup-save');
    assert.equal(text(save), 'Saving…');
    assert.equal(disabled(save), true);
    assert.equal(save.disabled, false);
    assert.equal(focused(), save);
    assert.equal($('setup-page').getAttribute('aria-busy'), 'true');
    save.click();
    await settle();
    assert.equal(mutations().length, 1);
    assert.equal(mutations()[0].method, 'PUT');
    assert.equal(mutations()[0].url, URLS.settings);
    assert.deepEqual(bodyOf(mutations()[0]), {configuration_revision: CONTEXT.configuration_revision,
        changes: [{op: 'remove_source', source_id: 5}], acknowledgment: ACK, section_digests: CONTEXT.section_digests});
    held.release(page.reply({success: true, saved: true, configuration_revision: 'b'.repeat(64),
        affected_schedule_ids: [18, 19], context: savedContext()}));
    await settle();
    await heard();
    // Saved: the page shows the saved settings, the pending list is empty, focus is on the outcome.
    assert.deepEqual(outcome(), {heading: 'Settings saved', text: TEXT.saved});
    assert.equal(focused(), $('setup-outcome-heading'));
    assert.equal(said(TEXT.saved), 1);
    assert.equal(status(), 'Settings saved.');
    assert.deepEqual(pendingLabels(), []);
    assert.equal(text($('setup-pending-count')), 'No pending changes');
    assert.deepEqual(rows('setup-source-list'), ['rainy day Search words']);
    assert.equal(shown($('setup-review')), false);
    assert.equal($('setup-page').hasAttribute('aria-busy'), false);
    assert.equal(text(save), 'Save playlist settings');
    // Nothing is pending now, so Back leaves freely.
    assert.equal(follow($('setup-back')), false);
    assert.deepEqual(asked, []);
    assert.equal(mutations().length, 1);
""",
    )


def test_a_save_affecting_no_routine_says_so_and_reads_a_missing_context(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed(impactReply([], {notes: [TEXT.ackNone]}));
    // With no affected routine the acknowledgment is the no-routine note, shown once.
    assert.equal(text($('setup-ack-text')), TEXT.ackNone);
    assert.equal(shown($('setup-review-affected')), false);
    assert.equal(shown($('setup-review-notes')), false);
    reads.saved = savedContext();
    replies.push(page.reply({success: true, saved: true, configuration_revision: 'b'.repeat(64),
        affected_schedule_ids: [], context: null}));
    press($('setup-save'));
    await settle();
    await heard();
    // The context missing from the reply is read, not assumed.
    assert.deepEqual(calls.slice(-2).map(call => call.method + ' ' + call.url),
        ['PUT ' + URLS.settings, 'GET ' + URLS.context]);
    assert.deepEqual(outcome(), {heading: 'Settings saved', text: TEXT.savedNone});
    assert.equal(said(TEXT.savedNone), 1);
    assert.deepEqual(rows('setup-source-list'), ['rainy day Search words']);
""",
    )


def test_nothing_is_sent_or_staged_while_the_settings_after_a_reply_are_read(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    // Saved, without the settings: the page reads them while the old review is still shown.
    let release;
    replies.push(page.reply({success: true, saved: true, configuration_revision: 'b'.repeat(64),
        affected_schedule_ids: [18], context: null}));
    replies.push(() => new Promise(resolve => { release = resolve; }));
    press($('setup-save'));
    await settle();
    assert.equal(calls.at(-1).url, URLS.context);
    // Save, Review changes and staging wait for that read, with their reason.
    const wait = 'Wait for your saved settings to load.';
    press($('setup-save'));
    await settle();
    assert.equal(mutations().length, 1);
    assert.equal(status(), wait);
    press($('setup-review-btn'));
    await settle();
    assert.equal(reviews().length, 1);
    press(byFocus('source-remove-6'));
    assert.equal(status(), wait);
    release(page.reply(Object.assign({success: true}, savedContext())));
    await settle();
    await heard();
    // The one outcome shows once the read settles.
    assert.deepEqual(outcome(), {heading: 'Settings saved', text: TEXT.saved});
    assert.equal(said(TEXT.saved), 1);
    assert.deepEqual(pendingLabels(), []);
    // A review refused because the settings changed reads them too: the acknowledged review it
    // replaces is not sent meanwhile.
    press(byFocus('source-remove-6'));
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    press($('setup-ack'));
    replies.push(page.reply({success: false, error_code: 'configuration_changed', message: 'Changed.'}, 409));
    replies.push(() => new Promise(resolve => { release = resolve; }));
    press($('setup-review-btn'));
    await settle();
    press($('setup-save'));
    await settle();
    assert.equal(status(), wait);
    release(page.reply(Object.assign({success: true}, savedContext({configuration_revision: 'c'.repeat(64)}))));
    await settle();
    await heard();
    assert.equal(outcome().heading, 'Settings changed');
    assert.equal(shown($('setup-review')), false);
    assert.equal(mutations().length, 1);
""",
    )


def test_a_conflict_adopts_the_fresh_settings_and_marks_what_no_longer_applies(
    node, setup
):
    run(
        node,
        setup,
        r"""
    await settle();
    type($('setup-add-search-input'), 'late night');
    press($('setup-add-search'));
    await commit($('setup-drip-count'), '6');
    await reviewed();
    const fresh = Object.assign(clone(CONTEXT), {configuration_revision: 'c'.repeat(64), collection: null,
        sources: CONTEXT.sources.concat([{id: 7, type: 'search_query', playlist_id: null, search_query: 'late night',
            name: 'late night', raid_count: 5}]),
        section_digests: Object.assign({}, CONTEXT.section_digests,
            {sources: '7'.repeat(64), collection: '8'.repeat(64)})});
    replies.push(page.reply({success: false, error_code: 'configuration_changed', message: 'Changed.',
        changed_sections: ['sources', 'collection'], context: fresh}, 409));
    press($('setup-save'));
    await settle();
    await heard();
    const message = 'Settings changed since your review: Sources, Collection. 2 changes no longer apply. ' +
        'Review the changes again.';
    assert.deepEqual(outcome(), {heading: 'Settings changed', text: message});
    assert.equal(said(message), 1);
    // The change whose subject still exists stays; the others are marked, not sent again.
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    assert.deepEqual(page.$$('#setup-stale-list li').map(item => text(item.querySelector('.sh-setup-row-text'))), [
        'Add source: “late night” (search) Already a source',
        'Collection: add 6 per run No collection playlist is linked now.']);
    assert.equal(text($('setup-collection-current')), 'No collection playlist yet.');
    // A new review is required: Save is unavailable, focus stays on it.
    assert.equal(shown($('setup-review')), false);
    assert.equal(disabled($('setup-save')), true);
    assert.equal(focused(), $('setup-save'));
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    // The new review is of the adopted settings, with their section digests.
    assert.deepEqual(bodyOf(reviews().at(-1)), {configuration_revision: 'c'.repeat(64),
        changes: [{op: 'remove_source', source_id: 5}], section_digests: fresh.section_digests});
    // A note that no longer applies can be cleared; focus moves to the next note's Undo.
    const [first, second] = page.$$('#setup-stale-list [data-setup-stale]');
    press(first);
    assert.equal(page.$$('#setup-stale-list li').length, 1);
    assert.equal(focused().getAttribute('data-setup-stale'), second.getAttribute('data-setup-stale'));
    assert.equal(mutations().length, 1);
""",
    )


def test_a_conflict_names_the_servers_changed_sections_when_it_gives_them(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    // The server names the sections that differ from the digests this page sent.
    const fresh = Object.assign(clone(CONTEXT), {configuration_revision: 'c'.repeat(64),
        section_digests: Object.assign({}, CONTEXT.section_digests, {routines: '0'.repeat(64)})});
    replies.push(page.reply({success: false, error_code: 'configuration_changed', changed_sections: ['archive'],
        context: fresh}, 409));
    press($('setup-save'));
    await settle();
    await heard();
    assert.deepEqual(bodyOf(mutations()[0]).section_digests, CONTEXT.section_digests);
    assert.deepEqual(outcome(), {heading: 'Settings changed',
        text: 'Settings changed since your review: Archive. Review the changes again.'});
    // Without them the page names the sections whose digests differ in the settings it adopts.
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    press($('setup-ack'));
    replies.push(page.reply({success: false, error_code: 'configuration_changed', changed_sections: null,
        context: Object.assign(clone(fresh), {configuration_revision: 'd'.repeat(64),
            section_digests: Object.assign({}, fresh.section_digests, {sources: '9'.repeat(64)})})}, 409));
    press($('setup-save'));
    await settle();
    await heard();
    assert.deepEqual(bodyOf(mutations()[1]).section_digests, fresh.section_digests);
    assert.equal(outcome().text, 'Settings changed since your review: Sources. Review the changes again.');
""",
    )


def test_settings_without_a_full_set_of_section_digests_send_none(node, db_app):
    context = setup_context(section_digests={"sources": "1" * 64})
    run(
        node,
        setup_page(db_app, context),
        r"""
    await settle();
    await stageRemoval();
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    assert.equal('section_digests' in bodyOf(reviews()[0]), false);
""",
        context,
    )


def test_an_expired_acknowledgment_needs_a_new_review_and_busy_keeps_everything(
    node, setup
):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    replies.push(page.reply({success: false, error_code: 'playlist_busy', retryable: true, message: TEXT.busy}, 409));
    press($('setup-save'));
    await settle();
    await heard();
    // Busy keeps the review and its acknowledgment: Save stays available, focus stays on it.
    assert.deepEqual(outcome(), {heading: 'Not saved yet', text: TEXT.busy});
    assert.equal(said(TEXT.busy), 1);
    assert.equal(shown($('setup-review')), true);
    assert.equal($('setup-ack').checked, true);
    assert.equal(disabled($('setup-save')), false);
    assert.equal(focused(), $('setup-save'));
    replies.push(page.reply({success: false, error_code: 'acknowledgment_invalid', reason: 'expired',
        message: 'Expired.'}, 409));
    press($('setup-save'));
    await settle();
    await heard();
    assert.deepEqual(outcome(), {heading: 'Review expired', text: TEXT.expired});
    assert.equal(said(TEXT.expired), 1);
    assert.equal(shown($('setup-review')), false);
    assert.equal(disabled($('setup-save')), true);
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    // Two deliberate presses, two saves: none is sent by the page itself.
    assert.equal(mutations().length, 2);
""",
    )


def test_refused_changes_are_marked_beside_their_controls_and_focus_the_first(
    node, setup
):
    run(
        node,
        setup,
        r"""
    await settle();
    type($('setup-add-playlist-input'), '__FRESH__');
    press($('setup-add-playlist'));
    await commit(byFocus('source-count-5'), '7');
    replies.push(page.reply({success: false, error_code: 'invalid_changes', message: 'Some changes are not valid.',
        errors: [{index: 0, field: 'playlist_id', message: 'Playlist not found'},
            {index: 1, field: 'raid_count', message: 'Count refused'}]}, 400));
    press($('setup-review-btn'));
    await settle();
    await heard();
    // The first refused change in the list takes focus: the new source's Undo, described by its error.
    const undo = page.$('#setup-source-list [data-setup-undo]');
    assert.equal(focused(), undo);
    const error = $(undo.getAttribute('aria-describedby'));
    assert.equal(text(error), 'Playlist not found');
    // The count field is marked invalid and described by its error.
    const count = byFocus('source-count-5');
    assert.equal(count.getAttribute('aria-invalid'), 'true');
    assert.equal(text($('setup-source-count-5-error')), 'Count refused');
    assert.ok(count.getAttribute('aria-describedby').split(' ').includes('setup-source-count-5-error'));
    assert.equal(outcome().heading, 'Some changes need attention');
    assert.deepEqual(page.$$('#setup-outcome-list li').map(text), [
        'Add source: a playlist from a link (named when you review): Playlist not found',
        'Indie Finds: collect 7 per run (was 5): Count refused']);
    // The pending list marks them too: each change's Undo there is described by its reason.
    const reason = label => text($(pendingUndo(label).getAttribute('aria-describedby')));
    assert.equal(reason('Add source: a playlist from a link (named when you review)'), 'Playlist not found');
    assert.equal(reason('Indie Finds: collect 7 per run (was 5)'), 'Count refused');
    const ids = page.$$('[id]').map(node => node.id);
    assert.deepEqual(ids.filter((id, index) => ids.indexOf(id) !== index), []);
    assert.equal(spoken.filter(item => item.startsWith('2 changes need attention.')).length, 1);
    // Undoing a refused change clears its mark.
    press(undo);
    assert.equal(page.$('#setup-source-list [data-setup-undo]'), null);
    assert.equal(byFocus('source-count-5').getAttribute('aria-invalid'), 'true');
    assert.deepEqual(mutations(), []);
""".replace("__FRESH__", FRESH),
    )


def test_an_unknown_save_that_reads_back_as_saved_is_confirmed_and_never_resent(
    node, setup
):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    reads.saved = savedContext();
    replies.push(() => { throw new Error('connection lost'); });
    press($('setup-save'));
    await settle();
    await heard();
    const message = TEXT.saved + ' Confirmed by reading your saved settings.';
    assert.equal(said(TEXT.unknownSave), 1);
    assert.deepEqual(outcome(), {heading: 'Settings saved', text: message});
    assert.equal(said(message), 1);
    assert.equal(focused(), $('setup-outcome-heading'));
    assert.deepEqual(pendingLabels(), []);
    assert.deepEqual(calls.filter(call => call.method !== 'GET' || call.url === URLS.context).map(call =>
        call.method + ' ' + call.url), ['POST ' + URLS.impact, 'PUT ' + URLS.settings, 'GET ' + URLS.context]);
""",
    )


def test_an_unknown_save_with_an_unchanged_revision_is_not_confirmed_never_not_saved(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    replies.push(page.reply({success: false, error_code: 'save_failed', message: 'Save failed.'}, 500));
    press($('setup-save'));
    await settle();
    await heard();
    // Any 5xx is unknown: the page reads the context. An unchanged revision does not show the save
    // yet, but it may still be finishing, so the page never says it was not saved.
    assert.equal(said(TEXT.unknownSave), 1);
    assert.deepEqual(outcome(), {heading: 'Not confirmed', text: TEXT.notConfirmed});
    assert.equal(said(TEXT.notConfirmed), 1);
    assert.equal(focused(), $('setup-outcome-heading'));
    assert.equal(shown($('setup-check-again')), true);
    assert.doesNotMatch(spoken.concat([status(), outcome().heading, outcome().text]).join(' '),
        /not saved|nothing changed/i);
    // The staged changes stay; saving them again needs a new review and acknowledgment.
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    assert.equal(shown($('setup-review')), false);
    assert.equal($('setup-ack').checked, false);
    assert.equal(disabled($('setup-save')), true);
    assert.equal(text($('setup-save-reason')), 'Review the changes first.');
    assert.equal(disabled($('setup-review-btn')), false);
    // Leaving says the settings may already be saved.
    page.answer = false;
    follow($('setup-back'));
    await settle();
    assert.match(asked.at(-1).message, /Your settings may already be saved\./);
    // The save finishes after all: Check again confirms it by reading, never by saving again.
    reads.saved = savedContext();
    press($('setup-check-again'));
    await settle();
    await heard();
    assert.deepEqual(outcome(),
        {heading: 'Settings saved', text: TEXT.saved + ' Confirmed by reading your saved settings.'});
    assert.deepEqual(pendingLabels(), []);
    assert.equal(mutations().length, 1);
""",
    )


def test_a_save_not_confirmed_can_be_reviewed_again_for_a_new_acknowledgment(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    replies.push(page.reply({success: false}, 502));
    press($('setup-save'));
    await settle();
    await heard();
    assert.equal(outcome().heading, 'Not confirmed');
    // Review changes again: a new review of the same staged list, whose acknowledgment starts unticked.
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    assert.equal(reviews().length, 2);
    assert.deepEqual(bodyOf(reviews()[1]), bodyOf(reviews()[0]));
    assert.equal(shown($('setup-review')), true);
    assert.equal($('setup-ack').checked, false);
    assert.equal(disabled($('setup-save')), true);
    assert.equal(shown($('setup-outcome')), false);
    // The first save may still be finishing, so leaving still says so.
    page.answer = false;
    follow($('setup-back'));
    await settle();
    assert.match(asked.at(-1).message, /Your settings may already be saved\./);
    // A confirmed save settles it: leaving then has nothing to say.
    press($('setup-ack'));
    replies.push(page.reply({success: true, saved: true, configuration_revision: 'b'.repeat(64),
        affected_schedule_ids: [18], context: savedContext()}));
    press($('setup-save'));
    await settle();
    assert.equal(outcome().heading, 'Settings saved');
    assert.equal(follow($('setup-back')), false);
    assert.equal(asked.length, 1);
    assert.equal(mutations().length, 2);
""",
    )


def test_an_unknown_save_with_other_changes_takes_the_conflict_path(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    reads.saved = Object.assign(clone(CONTEXT), {configuration_revision: 'd'.repeat(64), state_digest: '6'.repeat(64),
        section_digests: Object.assign({}, CONTEXT.section_digests, {routines: '0'.repeat(64)})});
    replies.push(page.reply('not json at all'));
    press($('setup-save'));
    await settle();
    await heard();
    const message = 'Settings changed since your review: Routines. Review the changes again.';
    assert.deepEqual(outcome(), {heading: 'Settings changed', text: message});
    assert.equal(said(message), 1);
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    assert.equal(shown($('setup-review')), false);
    assert.equal(mutations().length, 1);
""",
    )


def test_an_unknown_save_whose_check_fails_stays_unknown_until_checked_again(
    node, setup
):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    reads.contextFails = true;
    replies.push(() => { throw new Error('connection lost'); });
    press($('setup-save'));
    await settle();
    await heard();
    assert.equal(outcome().heading, 'Could not confirm settings save');
    assert.match(outcome().text, /^Could not confirm settings save\. Your saved settings could not be read\./);
    assert.equal(shown($('setup-check-again')), true);
    // While unknown, nothing is staged or saved again, and leaving says what may stay.
    press(byFocus('source-remove-6'));
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    assert.equal(status(), 'Check your saved settings before changing anything.');
    press($('setup-save'));
    await settle();
    assert.equal(mutations().length, 1);
    page.answer = false;
    assert.equal(follow($('setup-back')), true);
    await settle();
    assert.equal(asked.at(-1).title, 'Discard unsaved settings changes?');
    assert.match(asked.at(-1).message, /Your settings may already be saved\./);
    assert.match(asked.at(-1).message, /These details are kept only on this page\.$/);
    // Check again reads the settings once more; it still never sends the save.
    reads.contextFails = false;
    reads.saved = savedContext();
    press($('setup-check-again'));
    await settle();
    await heard();
    assert.deepEqual(outcome(),
        {heading: 'Settings saved', text: TEXT.saved + ' Confirmed by reading your saved settings.'});
    assert.equal(mutations().length, 1);
    assert.equal(shown($('setup-check-again')), false);
""",
    )


def test_every_staging_control_is_refused_while_a_review_loads(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    // An unknown creation found again by Refresh offers Use it.
    type($('setup-archive-create-name'), 'Morning Archive');
    replies.push(page.reply({success: false, outcome: 'unknown', error_code: 'creation_unknown',
        message: TEXT.creationUnknown}, 502));
    press($('setup-archive-create'));
    await settle();
    reads.owned = OWNED.concat([{id: 'made2', name: 'Morning Archive', track_count: 0, image_url: null, owned: true}]);
    press($('setup-archive-refresh'));
    await settle();
    // The collection's chooser is open and one change is staged.
    press(byFocus('collection-replace'));
    await settle();
    await stageRemoval();
    let release;
    replies.push(() => new Promise(resolve => { release = resolve; }));
    press($('setup-review-btn'));
    await tick();
    // While the review loads nothing staged changes: each control says why, once.
    const wait = 'Wait for the review to finish.', staged = pendingLabels();
    let refusals = 0;
    function refused(name) {
        refusals += 1;
        assert.equal(said(wait), refusals, name);
        assert.equal(status(), wait, name);
        assert.deepEqual(pendingLabels(), staged, name);
    }
    type($('setup-add-playlist-input'), '__FRESH__');
    press($('setup-add-playlist'));
    refused('add by link');
    type($('setup-add-search-input'), 'late night');
    press($('setup-add-search'));
    refused('add by search words');
    press(byFocus('source-remove-6'));
    refused('remove a source');
    // A changed count (Tab fires change as focus moves on) or box is put back.
    type(byFocus('source-count-6'), '9');
    byFocus('source-remove-6').focus();
    page.change(byFocus('source-count-6'));
    refused('count');
    assert.equal(byFocus('source-count-6').value, '10');
    press(byFocus('routine-source-0-remove'));
    refused('remove a routine-only source');
    press(byFocus('source-undo-5'));
    refused('undo');
    press(byFocus('collection-replace'));
    refused('replace');
    assert.equal(byFocus('collection-replace').getAttribute('aria-expanded'), 'true');
    press(byFocus('collection-unlink'));
    refused('unlink');
    $('setup-collection-select').value = 'keep';
    press($('setup-collection-use'));
    refused('use existing');
    press($('setup-archive-found-use'));
    refused('use the found playlist');
    press($('setup-drip-enabled'));
    refused('drip');
    assert.equal($('setup-drip-enabled').checked, true);
    release(impactReply());
    await settle();
    assert.equal(shown($('setup-review')), true);
    assert.equal(mutations().length, 1);
""".replace("__FRESH__", FRESH),
    )


def test_auto_archive_is_refused_and_put_back_while_a_review_loads(node, db_app):
    context = setup_context(archive={"playlist_id": "keep", "name": "Keepers", "auto_archive_on_remove": True})
    run(
        node,
        setup_page(db_app, context),
        r"""
    await settle();
    await stageRemoval();
    let release;
    replies.push(() => new Promise(resolve => { release = resolve; }));
    press($('setup-review-btn'));
    await tick();
    press($('setup-auto-archive'));
    assert.equal(status(), 'Wait for the review to finish.');
    assert.equal($('setup-auto-archive').checked, true);
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    release(impactReply());
    await settle();
    assert.deepEqual(mutations(), []);
""",
        context,
    )


def test_a_count_committed_with_focus_in_its_field_is_put_back_while_a_review_loads(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await stageRemoval();
    let release;
    replies.push(() => new Promise(resolve => { release = resolve; }));
    press($('setup-review-btn'));
    await tick();
    // Enter or the spinner commits a count with focus still in its field: refused, and put back there.
    type(byFocus('source-count-6'), '9');
    page.change(byFocus('source-count-6'));
    await heard();
    assert.equal(status(), 'Wait for the review to finish.');
    assert.equal(focused(), byFocus('source-count-6'));
    assert.equal(byFocus('source-count-6').value, '10');
    // So is the shared Drip count, whose field stays in place.
    const drip = $('setup-drip-count');
    type(drip, '7');
    page.change(drip);
    assert.equal(focused(), drip);
    assert.equal(drip.value, '3');
    assert.equal(said('Wait for the review to finish.'), 2);
    release(impactReply());
    await settle();
    assert.equal(byFocus('source-count-6').value, '10');
    assert.equal(drip.value, '3');
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
""",
    )


def test_staging_after_a_review_needs_a_new_review(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    press(byFocus('source-remove-6'));
    assert.equal(shown($('setup-review')), false);
    assert.equal($('setup-ack').checked, false);
    assert.equal(disabled($('setup-save')), true);
    assert.equal(text($('setup-save-reason')), 'Review the changes first.');
    press($('setup-save'));
    await settle();
    assert.equal(status(), 'Review the changes first.');
    assert.deepEqual(mutations(), []);
""",
    )


def test_creating_a_playlist_stages_it_and_says_it_stays_in_spotify(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    // The name is explicit: an empty one is refused beside its field and nothing is sent.
    press($('setup-archive-create'));
    await settle();
    assert.equal(text($('setup-archive-create-error')), 'Enter a name for the new playlist.');
    assert.equal($('setup-archive-create-name').getAttribute('aria-invalid'), 'true');
    assert.equal(focused(), $('setup-archive-create-name'));
    assert.deepEqual(mutations(), []);
    type($('setup-archive-create-name'), '  Morning Archive ');
    let release;
    replies.push(() => new Promise(resolve => { release = resolve; }));
    press($('setup-archive-create'));
    await tick();
    // Creating is marked busy, keeps focus, and a second press sends nothing.
    const create = $('setup-archive-create');
    assert.equal(text(create), 'Creating…');
    assert.equal(disabled(create), true);
    assert.equal(focused(), create);
    create.click();
    await settle();
    assert.equal(mutations().length, 1);
    assert.deepEqual(bodyOf(mutations()[0]), {purpose: 'archive', name: 'Morning Archive'});
    // Leaving while creating is blocked, without asking.
    assert.equal(follow($('setup-back')), true);
    assert.deepEqual(asked, []);
    assert.equal(status(), "Creating a playlist can't be cancelled by leaving; wait for it to finish.");
    release(page.reply({success: true, outcome: 'created', playlist: {id: 'made1', name: 'Morning Archive'},
        privacy_confirmed: true}, 201));
    await settle();
    await heard();
    const message = 'Created Morning Archive in your Spotify library. It stays there if you close setup. ' +
        'Save playlist settings to use it.';
    assert.equal(text($('setup-archive-create-status')), message);
    assert.equal(said(message), 1);
    // It is a staged pair, not a saved one; focus goes to its section.
    assert.deepEqual(pendingLabels(), ['Archive: use Morning Archive']);
    assert.match(text($('setup-archive-current')), /^Morning Archive Pending: use as the archive playlist Undo/);
    assert.equal(focused(), $('setup-archive'));
    assert.equal(shown($('setup-auto-archive-group')), true);
    // Leaving names what stays in Spotify.
    page.answer = false;
    follow($('setup-back'));
    await settle();
    assert.equal(asked.at(-1).message, 'Your pending changes are not saved. Leaving discards them. ' +
        'Morning Archive stays in your Spotify library. These details are kept only on this page.');
    // Undoing the pair keeps the created playlist offered, and still disclosed on leaving.
    press(byFocus('archive-undo'));
    assert.ok(options('setup-archive-select').includes('made1'));
    follow($('setup-back'));
    await settle();
    assert.equal(asked.at(-1).title, 'Leave setup?');
    assert.equal(asked.at(-1).message, 'Morning Archive stays in your Spotify library. ' +
        'These details are kept only on this page.');
    assert.equal(mutations().length, 1);
""",
    )


def test_playlists_created_here_are_offered_on_top_until_spotify_lists_them(node, setup):
    run(
        node,
        setup,
        r"""
    // Only this playlist is in the person's library: nothing else is offered.
    reads.owned = [OWNED[0]];
    await settle();
    assert.deepEqual(options('setup-archive-select'), ['']);
    assert.equal(text($('setup-archive-list-status')), 'No other playlists found.');
    async function create(name, id) {
        type($('setup-archive-create-name'), name);
        replies.push(page.reply({success: true, outcome: 'created', playlist: {id, name},
            privacy_confirmed: true}, 201));
        press($('setup-archive-create'));
        await settle();
        // Undo the staged pair: the chooser shows again.
        press(byFocus('archive-undo'));
    }
    // A playlist created here is offered at once, and still when Spotify's list lags behind.
    await create('Morning Archive', 'made1');
    assert.deepEqual(options('setup-archive-select'), ['', 'made1']);
    assert.equal(text($('setup-archive-list-status')), '');
    press($('setup-archive-refresh'));
    await settle();
    assert.deepEqual(options('setup-archive-select'), ['', 'made1']);
    // The newest is first; once Spotify lists one, it is listed once, in Spotify's place.
    await create('Evening Archive', 'made2');
    assert.deepEqual(options('setup-archive-select'), ['', 'made2', 'made1']);
    reads.owned = [OWNED[0], {id: 'made1', name: 'Morning Archive', track_count: 4, image_url: null, owned: true}];
    press($('setup-archive-refresh'));
    await settle();
    assert.deepEqual(options('setup-archive-select'), ['', 'made2', 'made1']);
    assert.equal($('setup-archive-select').options[2].textContent, 'Morning Archive (4 tracks)');
    assert.equal(mutations().length, 2);
""",
    )


def test_a_saved_created_playlist_drops_its_creation_note(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    type($('setup-archive-create-name'), 'Morning Archive');
    replies.push(page.reply({success: true, outcome: 'created', playlist: {id: 'made1', name: 'Morning Archive'},
        privacy_confirmed: true}, 201));
    press($('setup-archive-create'));
    await settle();
    assert.equal(shown($('setup-archive-create-status')), true);
    replies.push(impactReply([]));
    press($('setup-review-btn'));
    await settle();
    press($('setup-ack'));
    replies.push(page.reply({success: true, saved: true, configuration_revision: 'b'.repeat(64),
        affected_schedule_ids: [], context: Object.assign(clone(CONTEXT), {configuration_revision: 'b'.repeat(64),
            archive: {playlist_id: 'made1', name: 'Morning Archive', auto_archive_on_remove: true}})}));
    press($('setup-save'));
    await settle();
    // Saved: the playlist is the archive now, so neither the note nor leaving mentions it.
    assert.match(text($('setup-archive-current')), /^Morning Archive Archive playlist Replace/);
    assert.equal(shown($('setup-archive-create-status')), false);
    assert.equal(follow($('setup-back')), false);
    assert.deepEqual(asked, []);
""",
    )


def test_a_creation_note_belongs_to_its_playlist_and_refresh_finds_ones_made_here(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    const note = $('setup-archive-create-status');
    // A playlist created here, then set aside.
    type($('setup-archive-create-name'), 'Morning Archive');
    replies.push(page.reply({success: true, outcome: 'created', playlist: {id: 'made1', name: 'Morning Archive'},
        privacy_confirmed: true}, 201));
    press($('setup-archive-create'));
    await settle();
    press(byFocus('archive-undo'));
    // Another with the same name is unknown; Spotify's list lags behind, but Refresh still points
    // out the one created here.
    type($('setup-archive-create-name'), 'Morning Archive');
    replies.push(page.reply({success: false, outcome: 'unknown', error_code: 'creation_unknown',
        message: TEXT.creationUnknown}, 502));
    press($('setup-archive-create'));
    await settle();
    press($('setup-archive-refresh'));
    await settle();
    assert.equal(text($('setup-archive-found-text')), 'Found Morning Archive — use it?');
    // A later refusal's note is not about the playlist created first: saving that one keeps it.
    type($('setup-archive-create-name'), 'Evening Archive');
    replies.push(page.reply({success: false, outcome: 'refused', error_code: 'creation_refused',
        message: 'Spotify refused to create the playlist.'}, 422));
    press($('setup-archive-create-another'));
    await settle();
    assert.equal(text(note), 'Spotify refused to create the playlist.');
    $('setup-archive-select').value = 'made1';
    press($('setup-archive-use'));
    replies.push(impactReply([]));
    press($('setup-review-btn'));
    await settle();
    press($('setup-ack'));
    replies.push(page.reply({success: true, saved: true, configuration_revision: 'b'.repeat(64),
        affected_schedule_ids: [], context: Object.assign(clone(CONTEXT), {configuration_revision: 'b'.repeat(64),
            archive: {playlist_id: 'made1', name: 'Morning Archive', auto_archive_on_remove: true}})}));
    press($('setup-save'));
    await settle();
    assert.match(text($('setup-archive-current')), /^Morning Archive Archive playlist Replace/);
    assert.equal(text(note), 'Spotify refused to create the playlist.');
""",
    )


def test_an_unknown_creation_blocks_create_until_refresh_then_offers_another(
    node, setup
):
    run(
        node,
        setup,
        r"""
    await settle();
    type($('setup-archive-create-name'), 'Morning Archive');
    replies.push(page.reply({success: false, outcome: 'unknown', error_code: 'creation_unknown',
        message: TEXT.creationUnknown}, 502));
    press($('setup-archive-create'));
    await settle();
    await heard();
    assert.equal(text($('setup-archive-create-status')), TEXT.creationUnknown);
    assert.equal(said(TEXT.creationUnknown), 1);
    // Create is blocked (marked, still focusable) and a press sends nothing.
    assert.equal(disabled($('setup-archive-create')), true);
    assert.equal(focused(), $('setup-archive-create'));
    $('setup-archive-create-status').textContent = '';
    press($('setup-archive-create'));
    await settle();
    await heard();
    assert.equal(mutations().length, 1);
    assert.equal(text($('setup-archive-create-status')), TEXT.creationUnknown);
    assert.equal(said(TEXT.creationUnknown), 2);
    assert.equal(shown($('setup-archive-create-another')), false);
    // Leaving says the playlist may already exist.
    page.answer = false;
    follow($('setup-back'));
    await settle();
    assert.equal(asked.at(-1).title, 'Leave setup?');
    assert.equal(asked.at(-1).message, 'Morning Archive may already be in your Spotify library. ' +
        'These details are kept only on this page.');
    // Refresh my playlists reads again (bypassing the cache) and points out the name, never choosing it.
    reads.owned = OWNED.concat([{id: 'made2', name: 'Morning Archive', track_count: 0, image_url: null, owned: true}]);
    press($('setup-archive-refresh'));
    await settle();
    await heard();
    assert.equal(calls.at(-1).url, URLS.playlists + '?refresh=1');
    assert.equal(text($('setup-archive-found-text')), 'Found Morning Archive — use it?');
    assert.equal(said('Found Morning Archive — use it?'), 1);
    assert.equal($('setup-archive-select').value, '');
    assert.deepEqual(pendingLabels(), []);
    // Then an explicit Create another playlist replaces Create.
    assert.equal(shown($('setup-archive-create')), false);
    assert.equal(shown($('setup-archive-create-another')), true);
    assert.equal(text($('setup-archive-create-another')), 'Create another playlist');
    // Enter in the name field does not stand in for that choice.
    page.$('#setup-archive-create-form').dispatchEvent(page.event('submit', {bubbles: true, cancelable: true}));
    await settle();
    assert.equal(mutations().length, 1);
    // Using the found playlist stages it.
    press($('setup-archive-found-use'));
    assert.deepEqual(pendingLabels(), ['Archive: use Morning Archive']);
    assert.equal(mutations().length, 1);
""",
    )


def test_a_list_read_before_an_unknown_creation_is_not_its_check(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    type($('setup-archive-create-name'), 'Morning Archive');
    // Refresh my playlists is pressed first and its read is slow; Create is pressed meanwhile.
    let releaseRefresh, releaseCreate;
    replies.push(() => new Promise(resolve => { releaseRefresh = resolve; }));
    press($('setup-archive-refresh'));
    await tick();
    replies.push(() => new Promise(resolve => { releaseCreate = resolve; }));
    press($('setup-archive-create'));
    await tick();
    // Spotify made the playlist but the reply is lost; then the list read before that arrives.
    releaseCreate(page.reply({success: false, outcome: 'unknown', error_code: 'creation_unknown',
        message: TEXT.creationUnknown}, 502));
    await settle();
    releaseRefresh(page.reply({success: true, playlists: OWNED}));
    await settle();
    await heard();
    // That list cannot show the new playlist: it is not the check, and Create stays blocked.
    assert.equal(said('Your playlists were refreshed.'), 1);
    assert.equal(spoken.some(item => item.startsWith('No playlist named')), false);
    assert.equal(shown($('setup-archive-create-another')), false);
    assert.equal(disabled($('setup-archive-create')), true);
    press($('setup-archive-create'));
    await settle();
    assert.equal(mutations().length, 1);
    // A read started after the outcome is the check, and points the playlist out.
    reads.owned = OWNED.concat([{id: 'made2', name: 'Morning Archive', track_count: 0, image_url: null, owned: true}]);
    press($('setup-archive-refresh'));
    await settle();
    await heard();
    assert.equal(text($('setup-archive-found-text')), 'Found Morning Archive — use it?');
    assert.equal(shown($('setup-archive-create-another')), true);
    assert.equal(mutations().length, 1);
""",
    )


def test_refresh_is_refused_while_its_playlist_is_being_created(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    type($('setup-archive-create-name'), 'Morning Archive');
    let release;
    replies.push(() => new Promise(resolve => { release = resolve; }));
    press($('setup-archive-create'));
    await tick();
    // A list read now could predate the playlist: Refresh says why it waits, keeps focus and reads nothing.
    const before = calls.length;
    press($('setup-archive-refresh'));
    await settle();
    await heard();
    assert.equal(calls.length, before);
    assert.equal(status(), 'Wait for the playlist to be created.');
    assert.equal(said('Wait for the playlist to be created.'), 1);
    assert.equal(focused(), $('setup-archive-refresh'));
    release(page.reply({success: false, outcome: 'unknown', error_code: 'creation_unknown',
        message: TEXT.creationUnknown}, 502));
    await settle();
    // Once the outcome is known, Refresh is the creation's check.
    press($('setup-archive-refresh'));
    await settle();
    await heard();
    assert.equal(said('No playlist named Morning Archive was found. You can create another playlist.'), 1);
    assert.equal(shown($('setup-archive-create-another')), true);
    assert.equal(mutations().length, 1);
""",
    )


def test_a_playlist_found_after_an_unknown_creation_is_named_on_leaving(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    type($('setup-archive-create-name'), 'Morning Archive');
    replies.push(page.reply({success: false, outcome: 'unknown', error_code: 'creation_unknown',
        message: TEXT.creationUnknown}, 502));
    press($('setup-archive-create'));
    await settle();
    reads.owned = OWNED.concat([{id: 'made2', name: 'Morning Archive', track_count: 0, image_url: null, owned: true}]);
    press($('setup-archive-refresh'));
    await settle();
    press($('setup-archive-found-use'));
    assert.deepEqual(pendingLabels(), ['Archive: use Morning Archive']);
    // Leaving names the playlist the creation most likely made, as one made here...
    const stays = 'Morning Archive stays in your Spotify library. These details are kept only on this page.';
    page.answer = false;
    follow($('setup-back'));
    await settle();
    assert.equal(asked.at(-1).message, 'Your pending changes are not saved. Leaving discards them. ' + stays);
    // ...and still does once its pair is undone.
    press(byFocus('archive-undo'));
    assert.deepEqual(pendingLabels(), []);
    follow($('setup-back'));
    await settle();
    assert.equal(asked.length, 2);
    assert.equal(asked.at(-1).title, 'Leave setup?');
    assert.equal(asked.at(-1).message, stays);
    assert.deepEqual(navigations, []);
    assert.equal(mutations().length, 1);
""",
    )


def test_create_another_playlist_is_one_more_deliberate_request(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    type($('setup-archive-create-name'), 'Morning Archive');
    replies.push(() => { throw new Error('connection lost'); });
    press($('setup-archive-create'));
    await settle();
    reads.playlistsFail = true;
    press($('setup-archive-refresh'));
    await settle();
    // A failed read does not unblock Create.
    assert.equal(text($('setup-archive-list-status')),
        'Could not load your playlists. Refresh my playlists to try again.');
    assert.equal(shown($('setup-archive-create-another')), false);
    reads.playlistsFail = false;
    press($('setup-archive-refresh'));
    await settle();
    await heard();
    assert.equal(said('No playlist named Morning Archive was found. You can create another playlist.'), 1);
    replies.push(page.reply({success: true, outcome: 'created', playlist: {id: 'made3', name: 'Morning Archive'},
        privacy_confirmed: false}, 201));
    press($('setup-archive-create-another'));
    await settle();
    assert.equal(mutations().length, 2);
    assert.match(text($('setup-archive-create-status')), /Spotify did not confirm that it is private\.$/);
    assert.deepEqual(pendingLabels(), ['Archive: use Morning Archive']);
""",
    )


def test_a_refresh_replaced_by_a_later_one_says_nothing(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    // Pressed twice: the first read is replaced by the second, so only the second is shown and said.
    press($('setup-archive-refresh'));
    press($('setup-archive-refresh'));
    await settle();
    await heard();
    assert.deepEqual(calls.slice(-2).map(call => call.url), Array(2).fill(URLS.playlists + '?refresh=1'));
    assert.deepEqual(spoken, ['Your playlists were refreshed.']);
    assert.equal(text($('setup-archive-list-status')), '');
    // The latest read's failure is said.
    reads.playlistsFail = true;
    press($('setup-archive-refresh'));
    await settle();
    await heard();
    assert.equal(spoken.at(-1), 'Could not load your playlists. Try Refresh my playlists again.');
""",
    )


def test_refused_rate_limited_and_already_linked_creations(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    type($('setup-archive-create-name'), 'Morning Archive');
    replies.push(page.reply({success: false, outcome: 'refused', error_code: 'creation_refused',
        message: 'Spotify refused to create the playlist.'}, 422));
    press($('setup-archive-create'));
    await settle();
    await heard();
    assert.equal(text($('setup-archive-create-status')), 'Spotify refused to create the playlist.');
    assert.equal(focused(), $('setup-archive-create'));
    assert.equal(disabled($('setup-archive-create')), false);
    const limited = page.reply({success: false, outcome: 'rate_limited', message: 'Slow down.'}, 429);
    limited.headers = {get: name => (name === 'Retry-After' ? '30' : 'application/json')};
    replies.push(limited);
    press($('setup-archive-create'));
    await settle();
    await heard();
    assert.equal(text($('setup-archive-create-status')), 'Spotify is busy. Try again in 30 seconds.');
    assert.equal(said('Spotify is busy. Try again in 30 seconds.'), 1);
    // Another tab paired an archive first: the refusal shows it, read from the saved settings.
    reads.saved = Object.assign(clone(CONTEXT), {archive: {playlist_id: 'keep', name: 'Keepers',
        auto_archive_on_remove: true}, configuration_revision: 'c'.repeat(64)});
    replies.push(page.reply({success: false, error_code: 'already_paired',
        message: 'This playlist already has an archive.',
        archive: {playlist_id: 'keep', name: 'Keepers'}}, 409));
    press($('setup-archive-create'));
    await settle();
    await heard();
    assert.equal(text($('setup-archive-create-status')), 'This playlist already has an archive.');
    assert.match(text($('setup-archive-current')), /^Keepers Archive playlist Replace/);
    assert.equal(shown($('setup-archive-choose')), false);
    assert.equal(focused(), $('setup-archive'));
    assert.equal(mutations().length, 3);
""",
    )


def test_settings_adopted_from_a_refused_creation_clear_the_review(node, setup):
    """Create's already-paired refusal adopts the fresh saved settings: a review made from the
    old ones no longer matches, so Save needs a new review."""
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    assert.equal(shown($('setup-review')), true);
    // Another tab paired an archive while this review was open.
    reads.saved = Object.assign(clone(CONTEXT), {configuration_revision: 'c'.repeat(64),
        archive: {playlist_id: 'keep', name: 'Keepers', auto_archive_on_remove: true},
        section_digests: Object.assign({}, CONTEXT.section_digests, {archive: '8'.repeat(64)})});
    type($('setup-archive-create-name'), 'Morning Archive');
    replies.push(page.reply({success: false, error_code: 'already_paired',
        message: 'This playlist already has an archive.'}, 409));
    press($('setup-archive-create'));
    await settle();
    assert.match(text($('setup-archive-current')), /^Keepers Archive playlist Replace/);
    assert.equal(shown($('setup-review')), false);
    assert.equal($('setup-ack').checked, false);
    assert.equal(disabled($('setup-save')), true);
    assert.equal(text($('setup-save-reason')), 'Review the changes first.');
    press($('setup-save'));
    await settle();
    assert.equal(calls.filter(call => call.method === 'PUT').length, 0);
    // The staged removal still applies; it is reviewed again against the fresh settings.
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    assert.equal(bodyOf(reviews().at(-1)).configuration_revision, 'c'.repeat(64));
""",
    )


@pytest.mark.parametrize(
    ("purpose", "error_code"),
    [("archive", "already_paired"), ("collection", "already_linked")],
)
@pytest.mark.parametrize("read_succeeds", [True, False])
def test_refused_creation_blocks_edits_until_its_context_read_settles(
    node, db_app, purpose, error_code, read_succeeds
):
    context = setup_context(collection=None) if purpose == "collection" else setup_context()
    body = r"""
    await settle();
    await reviewed();
    const purpose = '__PURPOSE__', errorCode = '__ERROR_CODE__';
    type($('setup-' + purpose + '-create-name'), 'New playlist');
    let releaseRead;
    replies.push(page.reply({success: false, error_code: errorCode,
        message: 'A playlist is already linked.'}, 409));
    replies.push(() => new Promise(resolve => { releaseRead = resolve; }));
    press($('setup-' + purpose + '-create'));
    await settle();
    assert.equal(typeof releaseRead, 'function');
    assert.equal($('setup-page').getAttribute('aria-busy'), 'true');
    assert.equal(text($('setup-save-reason')), 'Wait for your saved settings to load.');
    const beforeChanges = pendingLabels(), beforeReviews = reviews().length;
    const beforeSaves = calls.filter(call => call.method === 'PUT').length;
    press(byFocus('source-remove-6'));
    assert.equal(status(), 'Wait for your saved settings to load.');
    press($('setup-review-btn'));
    press($('setup-save'));
    await settle();
    assert.deepEqual(pendingLabels(), beforeChanges);
    assert.equal(reviews().length, beforeReviews);
    assert.equal(calls.filter(call => call.method === 'PUT').length, beforeSaves);
    assert.equal(shown($('setup-review')), true);

    if (__READ_SUCCEEDS__) {
        const fresh = Object.assign(clone(CONTEXT), {configuration_revision: 'c'.repeat(64),
            [purpose]: {playlist_id: 'keep', name: 'Keepers'},
            section_digests: Object.assign({}, CONTEXT.section_digests,
                {[purpose]: '8'.repeat(64)})});
        releaseRead(page.reply({success: true, context: fresh}));
        await settle();
        assert.equal(shown($('setup-review')), false);
        assert.equal(text($('setup-save-reason')), 'Review the changes first.');
        assert.match(text($('setup-' + purpose + '-current')), /^Keepers/);
        assert.deepEqual(pendingLabels(), beforeChanges);
    } else {
        releaseRead(page.reply({success: false, error_code: 'spotify_unavailable'}, 502));
        await settle();
        assert.equal(shown($('setup-review')), false);
        assert.equal($('setup-ack').checked, false);
        assert.equal(disabled($('setup-save')), true);
        assert.equal(text($('setup-save-reason')), 'Review the changes first.');
        const message = 'This playlist already has a ' + purpose + ' playlist. ' +
            'Could not load your saved settings. Reload the page to show them.';
        assert.equal(text($('setup-' + purpose + '-create-status')), message);
        assert.equal(said(message), 1);
        press($('setup-save'));
        await settle();
        assert.equal(calls.filter(call => call.method === 'PUT').length, beforeSaves);
        assert.deepEqual(pendingLabels(), beforeChanges);
    }
    assert.equal($('setup-page').getAttribute('aria-busy'), null);
"""
    body = (
        body.replace("__PURPOSE__", purpose)
        .replace("__ERROR_CODE__", error_code)
        .replace("__READ_SUCCEEDS__", "true" if read_succeeds else "false")
    )
    run(node, setup_page(db_app, context), body, context)


def test_a_creation_refusal_is_read_from_its_body_not_its_status(node, setup):
    """Only the server's own codes name a field error or Spotify being busy: another layer's
    400 (the CSRF check) or 429 (the app's rate limit) is a refusal with its own message."""
    run(
        node,
        setup,
        r"""
    await settle();
    const field = $('setup-archive-create-name'), create = $('setup-archive-create');
    const error = $('setup-archive-create-error'), line = $('setup-archive-create-status');
    type(field, 'Morning Archive');
    // The server's refusal of the name is the name field's error.
    replies.push(page.reply({success: false, error_code: 'invalid_request',
        message: 'Validation error: name: too long'}, 400));
    press(create);
    await settle();
    assert.equal(text(error), 'Validation error: name: too long');
    assert.equal(field.getAttribute('aria-invalid'), 'true');
    assert.equal(focused(), field);
    // The CSRF check's 400 has no error_code: a refusal with its message, focus on Create.
    const unverified = 'Your request could not be verified. Please refresh the page and try again.';
    replies.push(page.reply({success: false, message: unverified, category: 'error'}, 400));
    press(create);
    await settle();
    await heard();
    assert.equal(text(line), unverified);
    assert.equal(said(unverified), 1);
    assert.equal(error.hidden, true);
    assert.equal(field.hasAttribute('aria-invalid'), false);
    assert.equal(focused(), create);
    // The app's rate limit has no outcome: its message, never "Spotify is busy".
    const tooMany = 'Too many requests. Please wait a moment and try again.';
    const limited = page.reply({success: false, message: tooMany, category: 'error'}, 429);
    limited.headers = {get: name => (name === 'Retry-After' ? '5' : 'application/json')};
    replies.push(limited);
    press(create);
    await settle();
    await heard();
    assert.equal(text(line), tooMany);
    assert.equal(said(tooMany), 1);
    assert.equal(spoken.some(item => item.startsWith('Spotify is busy')), false);
    assert.equal(field.hasAttribute('aria-invalid'), false);
    assert.equal(focused(), create);
    // Each was one deliberate press; Create stays available.
    assert.equal(mutations().length, 3);
    assert.equal(disabled(create), false);
""",
    )


def test_leaving_follows_the_page_state(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    // The browser's own warning: true when an unload now would warn.
    const warns = () => {
        const unload = page.event('beforeunload', {cancelable: true});
        page.window.dispatchEvent(unload);
        return unload.defaultPrevented;
    };
    // Nothing pending: the link is followed natively, without asking, and nothing warns.
    assert.equal(follow($('setup-back')), false);
    assert.deepEqual(asked, []);
    assert.equal(warns(), false);
    // Pending: Escape (no) stays, and the browser still warns about reloading or closing.
    await stageRemoval();
    page.answer = false;
    assert.equal(follow($('setup-back')), true);
    await settle();
    assert.equal(asked.length, 1);
    assert.deepEqual(navigations, []);
    assert.equal(warns(), true);
    // Yes leaves, and that navigation is not warned about again; only that one.
    page.answer = true;
    follow($('setup-back'));
    await settle();
    assert.deepEqual(navigations, ['https://example.test/workshop/target']);
    assert.equal(warns(), false, 'just confirmed: no second warning');
    assert.equal(warns(), true);
    // An approval not yet used when a save starts does not excuse leaving during it.
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    press($('setup-ack'));
    follow($('setup-back'));
    await settle();
    assert.equal(navigations.length, 2);
    const held = holdSave();
    await tick();
    assert.equal(warns(), true, 'saving: leaving cannot stop it');
    // Saving: leaving is blocked with its reason, without asking.
    assert.equal(follow($('setup-back')), true);
    await settle();
    assert.equal(asked.length, 3);
    assert.equal(status(), TEXT.leaveSaving);
    assert.equal(said(TEXT.leaveSaving), 1);
    assert.equal(warns(), true);
    held.release(page.reply({success: true, saved: true, configuration_revision: 'b'.repeat(64),
        affected_schedule_ids: [18], context: savedContext()}));
    await settle();
    // Saved: nothing pending, so leaving is free again and the browser does not warn.
    assert.equal(warns(), false);
    assert.equal(follow($('setup-back')), false);
""",
    )


def test_staging_inputs_are_checked_beside_their_fields(node, db_app):
    many = [
        source(100 + index, "Source " + str(index), playlist_id="p" + str(index))
        for index in range(10)
    ]
    run(
        node,
        setup_page(db_app, setup_context(sources=many, routine_only_sources=[])),
        r"""
    await settle();
    const field = $('setup-add-playlist-input');
    type(field, 'not a playlist');
    press($('setup-add-playlist'));
    assert.equal(text($('setup-add-playlist-error')), 'Enter a Spotify playlist link, URI or ID.');
    assert.equal(field.getAttribute('aria-invalid'), 'true');
    assert.equal(focused(), field);
    assert.equal(field.value, 'not a playlist');
    // At the limit, another source is refused until one is removed.
    type(field, 'spotify:playlist:__FRESH__');
    press($('setup-add-playlist'));
    assert.equal(text($('setup-add-playlist-error')), 'A playlist can have at most 10 sources');
    assert.equal(text($('setup-source-limit')), '10 of 10 sources');
    press(byFocus('source-remove-100'));
    press($('setup-add-playlist'));
    assert.equal($('setup-add-playlist-error').hidden, true);
    assert.equal(field.hasAttribute('aria-invalid'), false);
    assert.equal(text($('setup-source-limit')), '10 of 10 sources');
    // A count outside 1..100 keeps what was typed, beside its error, and holds the review.
    // Tab fires change as focus moves on: the rebuilt row keeps the refused value and focus.
    type(byFocus('source-count-101'), '0');
    byFocus('source-remove-101').focus();
    page.change(byFocus('source-count-101'));
    await heard();
    assert.equal(focused(), byFocus('source-remove-101'));
    assert.equal(byFocus('source-count-101').value, '0');
    assert.equal(byFocus('source-count-101').getAttribute('aria-invalid'), 'true');
    assert.equal(text($('setup-source-count-101-error')), 'Enter a whole number from 1 to 100.');
    page.$('#setup-review-btn').focus();
    $('setup-review-btn').click();
    await settle();
    assert.equal(focused(), byFocus('source-count-101'));
    assert.equal(status(), 'Fix the marked field first.');
    assert.equal(reviews().length, 0);
    await commit(byFocus('source-count-101'), '12');
    assert.equal(byFocus('source-count-101').hasAttribute('aria-invalid'), false);
    assert.ok(pendingLabels().includes('Source 1: collect 12 per run (was 5)'));
    // Setting it back to the saved count removes the change.
    await commit(byFocus('source-count-101'), '5');
    assert.equal(pendingLabels().includes('Source 1: collect 12 per run (was 5)'), false);
    // Already a source, and the drip count bounds.
    type($('setup-add-search-input'), 'Source 3');
    assert.deepEqual(mutations(), []);
""".replace("__FRESH__", FRESH),
    )


def test_an_add_create_or_select_error_never_holds_the_review_and_clears_when_edited(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await stageRemoval();
    // Create with no name, a link that is not one, no search words, and Use existing with nothing
    // chosen: each is marked beside its field.
    press($('setup-archive-create'));
    await settle();
    type($('setup-add-playlist-input'), 'not a playlist');
    press($('setup-add-playlist'));
    press($('setup-add-search'));
    press($('setup-archive-use'));
    const marked = {'setup-archive-create-name': 'setup-archive-create-error',
        'setup-add-playlist-input': 'setup-add-playlist-error', 'setup-add-search-input': 'setup-add-search-error',
        'setup-archive-select': 'setup-archive-select-error'};
    Object.entries(marked).forEach(([field, error]) => {
        assert.equal($(field).getAttribute('aria-invalid'), 'true', field);
        assert.equal(shown($(error)), true, error);
    });
    // None of them is a staged value, so none holds the review.
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    assert.equal(reviews().length, 1);
    assert.equal(focused(), $('setup-review-heading'));
    // Editing a field clears its error and stages nothing: the review stands.
    type($('setup-add-playlist-input'), 'n');
    type($('setup-add-search-input'), 'l');
    type($('setup-archive-create-name'), 'M');
    $('setup-archive-select').value = 'keep';
    page.change($('setup-archive-select'));
    Object.entries(marked).forEach(([field, error]) => {
        assert.equal($(field).hasAttribute('aria-invalid'), false, field);
        assert.equal(shown($(error)), false, error);
    });
    assert.equal(shown($('setup-review')), true);
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    // Choosing a playlist another way clears the chooser's errors too, so Undo does not bring them back.
    type($('setup-archive-create-name'), '');
    press($('setup-archive-create'));
    await settle();
    assert.equal(shown($('setup-archive-create-error')), true);
    press($('setup-archive-use'));
    press(byFocus('archive-undo'));
    assert.equal(shown($('setup-archive-create-error')), false);
    assert.equal($('setup-archive-create-name').hasAttribute('aria-invalid'), false);
    assert.deepEqual(mutations(), []);
""",
    )


def test_duplicates_the_target_and_distinct_assets_are_refused(node, db_app):
    target = {"id": FRESH, "name": "Morning Mix"}
    context = setup_context(
        target=target,
        archive={
            "playlist_id": "keep",
            "name": "Keepers",
            "auto_archive_on_remove": True,
        },
    )
    run(
        node,
        setup_page(db_app, context, target=target),
        r"""
    await settle();
    const field = $('setup-add-playlist-input');
    type(field, '__FRESH__');
    press($('setup-add-playlist'));
    assert.equal(text($('setup-add-playlist-error')), 'This is the playlist being set up');
    type(field, 'https://open.spotify.com/intl-de/playlist/__INDIE__');
    press($('setup-add-playlist'));
    assert.equal(text($('setup-add-playlist-error')), 'Already a source');
    type($('setup-add-search-input'), 'rainy day');
    press($('setup-add-search'));
    assert.equal(text($('setup-add-search-error')), 'Already a source');
    // The collection cannot become the archive playlist.
    press(byFocus('collection-replace'));
    await settle();
    $('setup-collection-select').value = 'keep';
    press($('setup-collection-use'));
    assert.equal(text($('setup-collection-select-error')), 'The collection and archive must be different playlists');
    assert.equal($('setup-collection-select').getAttribute('aria-invalid'), 'true');
    assert.equal(focused(), $('setup-collection-select'));
    $('setup-collection-select').value = '';
    press($('setup-collection-use'));
    assert.equal(text($('setup-collection-select-error')), 'Choose a playlist first.');
    await commit($('setup-drip-count'), '51');
    assert.equal(text($('setup-drip-count-error')), 'Enter a whole number from 1 to 50.');
    assert.equal($('setup-drip-count').value, '51');
    assert.deepEqual(pendingLabels(), []);
""".replace("__FRESH__", FRESH).replace("__INDIE__", INDIE),
        context,
    )


def test_unlink_and_remove_hand_focus_to_undo_and_back(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    press(byFocus('collection-unlink'));
    assert.equal(focused(), byFocus('collection-undo'));
    assert.equal(text(byFocus('collection-undo')), 'Undo: Collection: unlink Mix Inbox');
    // Unlinking hides the Drip settings that belong to the link.
    assert.equal(shown($('setup-drip')), false);
    press(byFocus('collection-undo'));
    assert.equal(focused(), byFocus('collection-unlink'));
    await stageRemoval();
    assert.equal(focused(), byFocus('source-undo-5'));
    press(byFocus('source-undo-5'));
    assert.equal(focused(), byFocus('source-remove-5'));
    press(byFocus('routine-source-0-remove'));
    assert.equal(focused(), byFocus('routine-source-0-undo'));
    press(byFocus('routine-source-0-undo'));
    assert.equal(focused(), byFocus('routine-source-0-remove'));
    // The last pending change's Undo, once gone, leaves focus on the pending changes group.
    press(byFocus('source-remove-6'));
    press(page.$('#setup-pending-list [data-setup-undo]'));
    assert.equal(focused(), $('setup-pending'));
    assert.deepEqual(mutations(), []);
""",
    )


def test_each_linked_playlist_stages_its_own_changes_and_checks_the_other(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    // Replace the collection, then undo it: focus returns to Replace.
    press(byFocus('collection-replace'));
    await settle();
    $('setup-collection-select').value = 'keep';
    press($('setup-collection-use'));
    assert.deepEqual(pendingLabels(), ['Collection: replace Mix Inbox with Keepers']);
    press(byFocus('collection-undo'));
    assert.equal(focused(), byFocus('collection-replace'));
    // The archive cannot be the collection playlist.
    $('setup-archive-select').value = 'inbox';
    press($('setup-archive-use'));
    assert.equal(text($('setup-archive-select-error')), 'The collection and archive must be different playlists');
    // Pair an archive with auto-archive off, and unlink the collection.
    $('setup-archive-select').value = 'keep';
    press($('setup-archive-use'));
    press($('setup-auto-archive'));
    press(byFocus('collection-unlink'));
    assert.deepEqual(pendingLabels(), ['Archive: use Keepers', 'Archive: stop auto-archiving removed tracks',
        'Collection: unlink Mix Inbox']);
    // The server refuses the unlink: its Undo is marked with the reason and takes focus.
    replies.push(page.reply({success: false, error_code: 'invalid_changes', errors: [
        {index: 2, field: 'op', message: 'Unlink refused'}]}, 400));
    press($('setup-review-btn'));
    await settle();
    assert.deepEqual(bodyOf(reviews()[0]).changes, [{op: 'pair_archive', playlist_id: 'keep'},
        {op: 'set_auto_archive', value: false}, {op: 'unlink_collection'}]);
    assert.equal(focused(), byFocus('collection-undo'));
    assert.equal(text($(focused().getAttribute('aria-describedby'))), 'Unlink refused');
    assert.deepEqual(mutations(), []);
""",
    )


def test_a_conflict_keeps_links_that_still_apply_with_their_settings(node, db_app):
    context = setup_context(collection=None, archive=None)
    run(
        node,
        setup_page(db_app, context),
        r"""
    await settle();
    // Link a collection with Drip on; pair an archive with auto-archive off.
    $('setup-collection-select').value = 'keep';
    press($('setup-collection-use'));
    press($('setup-drip-enabled'));
    $('setup-archive-select').value = 'shared';
    press($('setup-archive-use'));
    press($('setup-auto-archive'));
    const staged = ['Collection: use Keepers', 'Collection: add collected tracks automatically',
        'Archive: use Team Mix', 'Archive: stop auto-archiving removed tracks'];
    assert.deepEqual(pendingLabels(), staged);
    // The settings changed elsewhere, but nothing is linked yet: the links still apply, and so
    // do the settings that belong to them.
    const fresh = changes => Object.assign(clone(CONTEXT), {collection: null, archive: null,
        configuration_revision: 'c'.repeat(64), section_digests: Object.assign({}, CONTEXT.section_digests,
            {routines: '0'.repeat(64)})}, changes);
    replies.push(page.reply({success: false, error_code: 'configuration_changed', context: fresh({}),
        changed_sections: ['routines']}, 409));
    press($('setup-review-btn'));
    await settle();
    assert.deepEqual(bodyOf(reviews()[0]).changes.map(change => change.op),
        ['link_collection', 'set_drip', 'pair_archive', 'set_auto_archive']);
    assert.deepEqual(pendingLabels(), staged);
    assert.equal(shown($('setup-stale')), false);
    // Another tab paired an archive: this page's pair no longer applies; auto-archive now applies
    // to that archive.
    replies.push(page.reply({success: false, error_code: 'configuration_changed', changed_sections: ['archive'],
        context: fresh({configuration_revision: 'd'.repeat(64),
            archive: {playlist_id: 'inbox', name: 'Mix Inbox', auto_archive_on_remove: true}})}, 409));
    press($('setup-review-btn'));
    await settle();
    assert.deepEqual(page.$$('#setup-stale-list .sh-setup-row-text').map(text),
        ['Archive: use Team Mix A playlist is already linked here now.']);
    assert.deepEqual(pendingLabels(), ['Collection: use Keepers', 'Collection: add collected tracks automatically',
        'Archive: stop auto-archiving removed tracks']);
    // Unlinking that archive stages its own unlink, in place of the auto-archive change.
    press(byFocus('archive-unlink'));
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    assert.deepEqual(bodyOf(reviews().at(-1)).changes, [{op: 'link_collection', playlist_id: 'keep'},
        {op: 'set_drip', drip_enabled: true}, {op: 'unpair_archive'}]);
    assert.deepEqual(mutations(), []);
""",
        context,
    )


def test_a_review_whose_settings_changed_reads_them_and_asks_again(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await stageRemoval();
    press(byFocus('collection-unlink'));
    reads.saved = Object.assign(clone(CONTEXT), {configuration_revision: 'c'.repeat(64), collection: null,
        section_digests: Object.assign({}, CONTEXT.section_digests, {collection: '8'.repeat(64)})});
    // The preview refuses a stale revision without a context: the page reads it.
    replies.push(page.reply({success: false, error_code: 'configuration_changed', message: 'Changed.'}, 409));
    press($('setup-review-btn'));
    await settle();
    await heard();
    assert.equal(calls.at(-1).url, URLS.context);
    const message = 'Settings changed since your review: Collection. 1 change no longer applies. ' +
        'Review the changes again.';
    assert.deepEqual(outcome(), {heading: 'Settings changed', text: message});
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    assert.deepEqual(page.$$('#setup-stale-list .sh-setup-row-text').map(text),
        ['Collection: unlink Mix Inbox Nothing is linked here now.']);
    assert.deepEqual(mutations(), []);
""",
    )


def test_a_conflict_whose_settings_cannot_be_read_still_needs_a_new_review(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    reads.contextFails = true;
    replies.push(page.reply({success: false, error_code: 'configuration_changed', message: 'Changed.'}, 409));
    press($('setup-save'));
    await settle();
    await heard();
    assert.deepEqual(outcome(), {heading: 'Settings changed',
        text: 'Settings changed since your review. Review the changes again.'});
    assert.equal(shown($('setup-review')), false);
    assert.equal(disabled($('setup-save')), true);
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    assert.equal(mutations().length, 1);
""",
    )


def test_an_unanswered_review_or_a_refused_save_changes_nothing_on_the_page(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await stageRemoval();
    replies.push(() => { throw new Error('lost'); });
    press($('setup-review-btn'));
    await settle();
    await heard();
    const lost = 'The review could not be loaded. Nothing was saved. Try Review changes again.';
    assert.deepEqual(outcome(), {heading: 'Could not review the changes', text: lost});
    assert.equal(said(lost), 1);
    assert.equal(shown($('setup-review')), false);
    assert.equal(focused(), $('setup-review-btn'));
    // A refusal other than the named ones shows its message; focus stays on Save.
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    press($('setup-ack'));
    replies.push(page.reply({success: false, error_code: 'forbidden',
        message: 'You can only set up your own or collaborative playlists.'}, 403));
    press($('setup-save'));
    await settle();
    await heard();
    assert.deepEqual(outcome(), {heading: 'Not saved',
        text: 'You can only set up your own or collaborative playlists.'});
    assert.equal(focused(), $('setup-save'));
    assert.deepEqual(pendingLabels(), ['Remove source: Indie Finds']);
    assert.equal(mutations().length, 1);
""",
    )


def test_a_save_whose_settings_cannot_be_read_says_so_and_checks_again(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    reads.contextFails = true;
    replies.push(page.reply({success: true, saved: true, configuration_revision: 'b'.repeat(64),
        affected_schedule_ids: [18], context: null}));
    press($('setup-save'));
    await settle();
    await heard();
    assert.deepEqual(outcome(), {heading: 'Settings saved', text: TEXT.saved +
        ' This page could not load the saved settings. Check again to show them.'});
    assert.deepEqual(pendingLabels(), []);
    assert.equal(shown($('setup-check-again')), true);
    // A check that cannot read them either keeps offering the check its message names.
    press($('setup-check-again'));
    await settle();
    assert.deepEqual(outcome(), {heading: 'Settings saved',
        text: 'Your saved settings could not be read. Check again.'});
    assert.equal(shown($('setup-check-again')), true);
    reads.contextFails = false;
    reads.saved = savedContext();
    press($('setup-check-again'));
    await settle();
    assert.deepEqual(outcome(), {heading: 'Settings saved', text: 'Showing your saved settings.'});
    assert.deepEqual(rows('setup-source-list'), ['rainy day Search words']);
    assert.equal(mutations().length, 1);
""",
    )


def test_check_again_reads_once_however_often_it_is_pressed(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    reads.contextFails = true;
    replies.push(page.reply({success: true, saved: true, configuration_revision: 'b'.repeat(64),
        affected_schedule_ids: [18], context: null}));
    press($('setup-save'));
    await settle();
    await heard();
    assert.equal(shown($('setup-check-again')), true);
    reads.contextFails = false;
    const contextReads = () => calls.filter(call => call.url === URLS.context).length, before = contextReads();
    let release;
    replies.push(() => new Promise(resolve => { release = resolve; }));
    press($('setup-check-again'));
    await tick();
    // Pressed again while its read is out: refused. Nothing is staged on the settings it will show.
    $('setup-check-again').click();
    press(byFocus('source-remove-6'));
    assert.equal(status(), 'Wait for your saved settings to load.');
    assert.equal($('setup-page').getAttribute('aria-busy'), 'true');
    release(page.reply(Object.assign({success: true}, savedContext())));
    await settle();
    await heard();
    assert.equal(contextReads() - before, 1);
    assert.deepEqual(outcome(), {heading: 'Settings saved', text: 'Showing your saved settings.'});
    assert.equal(said('Showing your saved settings.'), 1);
    assert.deepEqual(pendingLabels(), []);
    assert.equal(mutations().length, 1);
""",
    )


def test_every_outcome_of_a_check_puts_focus_on_the_outcome_heading(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    reads.contextFails = true;
    replies.push(() => { throw new Error('connection lost'); });
    press($('setup-save'));
    await settle();
    await heard();
    assert.equal(outcome().heading, 'Could not confirm settings save');
    assert.equal(focused(), $('setup-outcome-heading'));
    // The read works now, and the settings do not show the save yet.
    reads.contextFails = false;
    press($('setup-check-again'));
    await settle();
    await heard();
    assert.equal(outcome().heading, 'Not confirmed');
    assert.equal(focused(), $('setup-outcome-heading'));
    // They changed some other way: the conflict path, whose outcome has no Check again.
    reads.saved = Object.assign(clone(CONTEXT), {configuration_revision: 'd'.repeat(64), state_digest: '6'.repeat(64),
        section_digests: Object.assign({}, CONTEXT.section_digests, {routines: '0'.repeat(64)})});
    press($('setup-check-again'));
    await settle();
    await heard();
    assert.equal(outcome().heading, 'Settings changed');
    assert.equal(shown($('setup-check-again')), false);
    assert.equal(focused(), $('setup-outcome-heading'));
    // The settings shown now say what the save did: leaving no longer calls it unconfirmed.
    page.answer = false;
    follow($('setup-back'));
    await settle();
    assert.equal(asked.at(-1).message, 'Your pending changes are not saved. Leaving discards them.');
    // A save whose settings cannot be read offers Check again; showing them hides it.
    replies.push(impactReply());
    press($('setup-review-btn'));
    await settle();
    press($('setup-ack'));
    reads.contextFails = true;
    replies.push(page.reply({success: true, saved: true, configuration_revision: 'b'.repeat(64),
        affected_schedule_ids: [18], context: null}));
    press($('setup-save'));
    await settle();
    await heard();
    reads.contextFails = false;
    press($('setup-check-again'));
    await settle();
    await heard();
    assert.deepEqual(outcome(), {heading: 'Settings saved', text: 'Showing your saved settings.'});
    assert.equal(shown($('setup-check-again')), false);
    assert.equal(focused(), $('setup-outcome-heading'));
    assert.equal(mutations().length, 2);
""",
    )


def test_a_save_that_never_answers_becomes_unknown_at_the_time_limit(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    // The page's own time limits (two minutes for a write, 30 seconds for a read) pass at once here.
    const plainTimeout = page.window.setTimeout, limits = [];
    page.window.setTimeout = (callback, delay, ...args) => {
        if (delay >= 30000) limits.push(delay);
        return plainTimeout(callback, delay >= 30000 ? 1 : delay, ...args);
    };
    reads.saved = savedContext();
    replies.push(() => new Promise(() => {}));
    press($('setup-save'));
    await heard();
    await settle();
    await heard();
    assert.equal(said(TEXT.unknownSave), 1);
    assert.deepEqual(outcome(), {heading: 'Settings saved',
        text: TEXT.saved + ' Confirmed by reading your saved settings.'});
    assert.equal(mutations().length, 1);
    // The save, then the read that confirmed it; a read is never served from a cache.
    assert.deepEqual(limits, [120000, 30000]);
    assert.equal(calls.at(-1).url, URLS.context);
    assert.equal(calls.at(-1).init.cache, 'no-store');
""",
    )


def test_a_reply_that_cannot_be_read_is_unknown_whatever_its_status(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    // A proxy's page in place of the reply: the save may have happened, so it is checked by reading.
    replies.push(page.reply('<html>Forbidden</html>', 403));
    press($('setup-save'));
    await settle();
    await heard();
    assert.equal(said(TEXT.unknownSave), 1);
    assert.deepEqual(outcome(), {heading: 'Not confirmed', text: TEXT.notConfirmed});
    // So is a creation's.
    type($('setup-archive-create-name'), 'Morning Archive');
    replies.push(page.reply('<html>Too large</html>', 413));
    press($('setup-archive-create'));
    await settle();
    assert.equal(text($('setup-archive-create-status')), TEXT.creationUnknown);
    assert.equal(mutations().length, 2);
""",
    )


def test_settings_that_already_match_the_review_are_confirmed_saved(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    await reviewed();
    // The same revision, but the state the review expects: settings match, so they are saved.
    reads.saved = Object.assign(clone(CONTEXT), {state_digest: 'e'.repeat(64)});
    replies.push(page.reply({success: false}, 504));
    press($('setup-save'));
    await settle();
    await heard();
    assert.deepEqual(outcome(), {heading: 'Settings saved',
        text: TEXT.saved + ' Confirmed by reading your saved settings.'});
""",
    )


def test_a_shared_collection_and_archive_is_noted_and_kept(node, db_app):
    context = setup_context(
        archive={"playlist_id": "inbox", "name": "Mix Inbox", "auto_archive_on_remove": False}
    )
    run(
        node,
        setup_page(db_app, context),
        r"""
    await settle();
    for (const purpose of ['collection', 'archive']) {
        const note = $('setup-' + purpose + '-shared-note');
        assert.equal(shown(note), true);
        assert.match(text(note), /^This playlist is both the collection and the archive/);
    }
    assert.equal($('setup-auto-archive').checked, false);
    assert.deepEqual(pendingLabels(), []);
    // Unlinking the collection drops a staged Drip change that belonged to it.
    press($('setup-drip-enabled'));
    assert.deepEqual(pendingLabels(), ['Collection: stop adding collected tracks automatically']);
    press(byFocus('collection-unlink'));
    assert.deepEqual(pendingLabels(), ['Collection: unlink Mix Inbox']);
    assert.deepEqual(calls.map(call => call.method), []);
""",
        context,
    )


def test_undoing_a_new_link_or_pair_drops_the_settings_that_belong_to_it(node, db_app):
    context = setup_context(collection=None, archive=None)
    run(
        node,
        setup_page(db_app, context),
        r"""
    await settle();
    $('setup-collection-select').value = 'keep';
    press($('setup-collection-use'));
    press($('setup-drip-enabled'));
    $('setup-archive-select').value = 'shared';
    press($('setup-archive-use'));
    press($('setup-auto-archive'));
    assert.deepEqual(pendingLabels(), ['Collection: use Keepers', 'Collection: add collected tracks automatically',
        'Archive: use Team Mix', 'Archive: stop auto-archiving removed tracks']);
    // Undoing the new link takes its Drip change with it; focus moves to the next Undo still listed.
    press(pendingUndo('Collection: use Keepers'));
    assert.deepEqual(pendingLabels(), ['Archive: use Team Mix', 'Archive: stop auto-archiving removed tracks']);
    assert.equal(focused(), pendingUndo('Archive: use Team Mix'));
    assert.equal(shown($('setup-drip')), false);
    // Undoing the new pair beside its section takes auto-archive with it.
    press(byFocus('archive-undo'));
    assert.deepEqual(pendingLabels(), []);
    assert.equal(shown($('setup-auto-archive-group')), false);
    assert.deepEqual(calls.map(call => call.method), ['GET']);
""",
        context,
    )


def test_undoing_a_replace_keeps_the_settings_of_the_saved_link(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    press(byFocus('collection-replace'));
    await settle();
    $('setup-collection-select').value = 'keep';
    press($('setup-collection-use'));
    await commit($('setup-drip-count'), '6');
    assert.deepEqual(pendingLabels(), ['Collection: replace Mix Inbox with Keepers', 'Collection: add 6 per run']);
    // The saved collection stays linked, so its Drip change still applies.
    press(byFocus('collection-undo'));
    assert.deepEqual(pendingLabels(), ['Collection: add 6 per run']);
    assert.equal($('setup-drip-count').value, '6');
""",
    )


def test_a_refused_drip_change_marks_its_own_control(node, setup):
    run(
        node,
        setup,
        r"""
    await settle();
    press($('setup-drip-enabled'));
    press(byFocus('routine-source-0-remove'));
    replies.push(page.reply({success: false, error_code: 'invalid_changes', errors: [
        {index: 1, field: 'playlist_id', message: 'Not used by any routine'},
        {index: 0, field: 'drip_enabled', message: 'No collection to set'}]}, 400));
    press($('setup-review-btn'));
    await settle();
    assert.equal(focused(), $('setup-drip-enabled'));
    assert.equal($('setup-drip-enabled').getAttribute('aria-invalid'), 'true');
    assert.equal(text($('setup-drip-enabled-error')), 'No collection to set');
    const undo = byFocus('routine-source-0-undo');
    assert.equal(text($(undo.getAttribute('aria-describedby'))), 'Not used by any routine');
""",
    )


def test_every_name_from_spotify_or_the_person_is_text(node, db_app):
    hostile = HOSTILE
    target = {"id": "target", "name": hostile}
    context = setup_context(
        target=target,
        sources=[
            source(5, hostile, playlist_id=INDIE),
            source(6, hostile, query=hostile),
        ],
        routine_only_sources=[{"playlist_id": hostile, "routine_ids": [18]}],
        collection={
            "playlist_id": "inbox",
            "name": hostile,
            "drip_enabled": True,
            "drip_count": 3,
        },
        routines=[routine(18, "raid", hostile, hostile)],
    )
    html = setup_page(db_app, context, target=target)
    # The server escapes it in markup (&lt;) and in the page data (\u003c).
    assert "<img src=x" not in html
    assert "Playlist setup · &lt;img src=x" in html
    run(
        node,
        html,
        r"""
    const HOSTILE = __HOSTILE__;
    reads.owned = [{id: 'evil', name: HOSTILE, track_count: 1, image_url: null, owned: true}];
    await settle();
    press(byFocus('source-remove-5'));
    replies.push(impactReply([effect(18, HOSTILE, HOSTILE, true, [HOSTILE])],
        {changes: [{summary: HOSTILE}], notes: [HOSTILE],
            unaffected_routines: [{id: 9, operation: HOSTILE, reason: HOSTILE}]}));
    press($('setup-review-btn'));
    await settle();
    type($('setup-archive-create-name'), HOSTILE);
    replies.push(page.reply({success: true, outcome: 'created', playlist: {id: 'made', name: HOSTILE},
        privacy_confirmed: true}, 201));
    press($('setup-archive-create'));
    await settle();
    // No markup was made from any of them: no image element anywhere, and the words are shown.
    assert.equal(page.$$('img').length, 0);
    assert.equal(text($('setup-title')), 'Playlist setup · ' + HOSTILE);
    assert.ok(text(page.$('#setup-source-list')).includes(HOSTILE));
    assert.ok(text($('setup-review')).includes(HOSTILE));
    assert.ok(text($('setup-archive-create-status')).includes(HOSTILE));
    assert.ok(pendingLabels().includes('Archive: use ' + HOSTILE));
    assert.ok($('setup-archive-select').options.some(option => option.textContent.startsWith(HOSTILE)));
""".replace("__HOSTILE__", json.dumps(hostile)),
        context,
    )


def test_workshop_links_to_playlist_setup(db_app):
    """Workshop entry links: native view links, so the Workshop's own leave guard
    runs, each opening its section of the setup page and returning to this playlist."""
    with db_app.test_request_context("/workshop/pl1"):
        session["spotify_token"] = {"access_token": "valid-fixture"}
        html = render_template(
            "partials/workspace_tools.html",
            playlist={"id": "pl1", "name": "Mix", "tracks": []},
            algorithms=[],
        )
    links = re.findall(
        r'<a href="(/playlist/[^"]+)"\s+data-workshop-compat="([^"]+)"[^>]*>([^<]+)</a>',
        html,
    )
    sources = "/playlist/pl1/setup?section=sources&amp;return=/workshop/pl1"
    archive = "/playlist/pl1/setup?section=archive&amp;return=/workshop/pl1"
    assert links == [
        (sources, "view", "Playlist setup"),
        (sources, "view", "Playlist setup"),
        (archive, "view", "Playlist setup"),
    ]
    tree = page_tree(html)

    def find(nodes, panel_id):
        for item in nodes:
            if isinstance(item, str):
                continue
            if dict(item["a"]).get("id") == panel_id:
                return item
            found = find(item["c"], panel_id)
            if found:
                return found
        return None

    def first_link(node):
        body = next(
            child
            for child in node["c"]
            if not isinstance(child, str)
            and "sh-panel-body" in dict(child["a"]).get("class", "")
        )
        setup = next(child for child in body["c"] if not isinstance(child, str))
        return next(child for child in setup["c"] if not isinstance(child, str))

    # At the top of Source setup and Archive setup.
    for panel_id, href in (
        ("workshop-sources-panel", sources),
        ("workshop-archive-setup-panel", archive),
    ):
        link = first_link(find(tree, panel_id))
        assert link["t"] == "a" and dict(link["a"])["href"] == href.replace(
            "&amp;", "&"
        )
    # And beside the Inbox's Source setup button.
    inbox = html[
        html.index('id="workshop-inbox-panel"') : html.index(
            'id="workshop-sources-panel"'
        )
    ]
    assert inbox.index('id="workshop-sources-btn"') < inbox.index(sources)


def _css_rules(path):
    """Each rule's selector and declarations in one stylesheet, comments removed."""
    css = re.sub(r"/\*.*?\*/", "", (ROOT / path).read_text(), flags=re.S)
    rules = re.findall(r"([^{};]+)\{([^{}]*)\}", css)
    return [(re.sub(r"\s+", " ", selector).strip(), body) for selector, body in rules]


def test_setup_composes_core_surfaces_dividers_and_warning_text(node, setup):
    """pages/setup.css lays the page out; the surfaces, dividers and warning text it shows are
    core's recipes, named in the markup instead of restated."""
    rules = _css_rules("assets/design/pages/setup.css")
    for selector in (".sh-setup-section", ".sh-setup-summary", ".sh-setup-review, .sh-setup-outcome",
                     ".sh-setup-pending-mark"):
        bodies = [body for name, body in rules if name == selector]
        declared = [name for body in bodies for name in re.findall(r"([\w-]+)\s*:", body)]
        visual = [name for name in declared if name.startswith(("border", "background", "color"))]
        assert bodies and not visual, selector
    classes = {
        node_id: set(value.split())
        for node_id, value in re.findall(r'<\w+ id="([\w-]+)" class="([^"]*)"', setup)
    }
    for node_id in ("setup-sources", "setup-collection", "setup-archive", "setup-summary"):
        assert "sh-surface" in classes[node_id], node_id
    for node_id in ("setup-review", "setup-outcome"):
        assert "sh-divider" in classes[node_id], node_id
    run(
        node,
        setup,
        r"""
    await settle();
    await stageRemoval();
    assert.ok(page.$$('.sh-setup-pending-mark').length > 0);
    page.$$('.sh-setup-pending-mark').forEach(mark => assert.ok(mark.classList.contains('sh-text-warning')));
""",
    )


SUMMARIES = json.loads((ROOT / "tests/fixtures/setup_change_summaries.json").read_text())


@pytest.mark.parametrize("group", ["cases", "unsaved_cases"])
def test_each_change_is_described_in_the_one_wording_the_server_uses(node, db_app, group):
    """describe() gives exactly the fixture's summaries, which the server's normalized summaries
    give too: `cases` over the saved collection and archive, `unsaved_cases` with neither."""
    names, saved = SUMMARIES["playlists"], group == "cases"

    def asset(purpose, **settings):
        playlist_id = SUMMARIES["saved"][purpose]
        return dict(playlist_id=playlist_id, name=names[playlist_id], **settings) if saved else None

    context = setup_context(
        sources=[
            source(int(source_id), item["name"], playlist_id=INDIE, count=item["raid_count"])
            for source_id, item in SUMMARIES["sources"].items()
        ],
        collection=asset("collection", drip_enabled=False, drip_count=3),
        archive=asset("archive", auto_archive_on_remove=True),
    )
    owned = [
        {"id": playlist_id, "name": name, "track_count": 1, "image_url": None, "owned": True}
        for playlist_id, name in names.items()
    ]
    run(
        node,
        setup_page(db_app, context),
        r"""
    reads.owned = __OWNED__;
    await settle();
    // The person's playlists name the cases' playlists: a chooser's first showing reads them.
    if (__SAVED__) {
        press(byFocus('collection-replace'));
        await settle();
    }
    // A read-only view of the page's wording, not a way to change its state.
    assert.equal(Object.isFrozen(page.window.PlaylistSetup), true);
    const describe = page.window.PlaylistSetup.describe;
    for (const item of __CASES__) assert.equal(describe(item.change), item.summary, JSON.stringify(item.change));
    assert.deepEqual(mutations(), []);
""".replace("__OWNED__", json.dumps(owned))
        .replace("__SAVED__", json.dumps(saved))
        .replace("__CASES__", json.dumps(SUMMARIES[group])),
        context,
    )


def _url_parser_cases(monkeypatch):
    """Every text the server parser's own suite (tests/spotify/test_url_parser.py) parses, with
    the server's answer: recorded by running that suite's tests."""
    from tests.spotify import test_url_parser as suite

    parse, seen = suite.parse_spotify_playlist_url, []

    def record(value):
        seen.append((value, parse(value)))
        return seen[-1][1]

    monkeypatch.setattr(suite, "parse_spotify_playlist_url", record)
    tests = suite.TestParseSpotifyPlaylistUrl()
    for name in sorted(dir(tests)):
        if name.startswith("test_"):
            getattr(tests, name)()
    # The page parses what is typed: text only.
    return [(value, result) for value, result in seen if isinstance(value, str)]


def test_the_link_parser_reads_the_server_parsers_link_and_uri_cases_alike(node, setup, monkeypatch):
    cases = _url_parser_cases(monkeypatch)
    assert len(cases) >= 20 and any(result is None for _, result in cases)
    run(
        node,
        setup,
        r"""
    await settle();
    const parse = page.window.PlaylistSetup.parsePlaylistId;
    for (const [text, expected] of __CASES__) assert.equal(parse(text), expected, JSON.stringify(text));
""".replace("__CASES__", json.dumps(cases)),
    )
