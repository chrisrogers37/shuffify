"""Workshop History: the snapshot View, saved drafts,
the activity links and Playlist details, run on the rendered page under Node with routed replies.

A saved draft is an order kept in Shuffify only. Save, Update and Delete write it at once (group
create); Load changes only the working order (group draft), through PlaylistWorkspace.remap, after
the local discard when the page has a Draft or staged tracks, and never while saved work needs
attention. Nothing here writes to Spotify.
"""

from tests.test_playlist_operations_runtime import node as node
from tests.test_workshop_live_runtime import run_workspace, workspace_page

HISTORY = r"""
const t = n => 'spotify:track:t' + n;
const draft = (id, name, uris, updated = '2026-10-12T08:00:00+00:00') => ({id, user_id: 1, playlist_id: 'target',
    session_name: name, track_uris: uris, track_count: uris.length, created_at: updated, updated_at: updated});
const draftRows = () => page.$$('#saved-drafts-list > li');
const draftIds = () => draftRows().map(row => row.dataset.draftId);
const draftControl = (action, id) => page.$('[data-action-click="' + action + '"][data-draft-id="' + id + '"]');
const statusText = () => page.text(page.byId('saved-drafts-status'));
const failed = () => page.byId('saved-drafts-status').classList.contains('sh-text-error');
const announced = () => page.text(page.byId('live-operation-announcer'));
// The last words the announcer said: a write in flight also changes the Save status, which it says first.
const saidLast = words => assert.ok(announced().endsWith(words), announced());
const rowLines = row => row.querySelectorAll('p').map(line => page.text(line));
const sent = (method, url) => page.calls.filter(call => call.method === method && call.url === url);
const draftsSection = () => page.$('[aria-labelledby="saved-drafts-heading"]');
// Holds the next confirmation open until the scenario answers it.
function holdQuestion() {
    const plainAsk = page.window.AppConfirm.ask;
    let answer;
    page.window.AppConfirm.ask = request => {
        page.asked.push(request);
        return new Promise(resolve => { answer = resolve; });
    };
    return async choice => { answer(choice); page.window.AppConfirm.ask = plainAsk; await settle(); };
}
async function openHistory(routes = {}) {
    await openWorkshop('/workshop/target', routes);
    page.byId('workshop-tool-history').click(); await settle();
}
"""


def run_history(node, db_app, scenario, tracks=3):
    run_workspace(node, workspace_page(db_app, tracks=tracks), HISTORY + scenario)


def test_history_has_three_named_sections_and_details_keep_their_links(node, db_app):
    run_history(node, db_app, r"""
    await openHistory();
    const sections = page.byId('workshop-history-panel').querySelectorAll('[data-panel-return]');
    // Three groups, each named by its own heading.
    const names = sections.map(section => {
        const heading = page.byId(section.getAttribute('aria-labelledby'));
        assert.equal(section.getAttribute('role'), 'group');
        assert.equal(heading.closest('[data-panel-return]'), section);
        return page.text(heading);
    });
    assert.match(names[0], /^Playlist snapshots/);
    assert.deepEqual(names.slice(1), ['Saved drafts', 'Activity']);
    // Saved drafts say they stay in Shuffify and that nothing saves them automatically.
    assert.match(page.text(page.byId('saved-drafts-note')),
        /in Shuffify only\. It is never sent to Spotify, and nothing saves one automatically/);
    assert.equal(page.text(page.byId('saved-draft-save-btn')), 'Save as named draft (Shuffify only)');
    assert.equal(page.byId('saved-drafts-empty').classList.contains('hidden'), false);
    assert.deepEqual(draftRows(), []);
    // Activity leads to the activity log and the routine overview; both only navigate.
    assert.deepEqual(sections[2].querySelectorAll('a').map(link =>
        [link.getAttribute('href'), page.text(link), link.dataset.workshopCompat]),
        [['/activity', 'Activity log', 'view'], ['/schedules', 'Routine overview', 'view']]);
    // Playlist details keeps the Spotify link and the way to another playlist. Both Spotify links,
    // there and in the identity header, are the shared attribution in the view group.
    page.byId('workshop-details-btn').click(); await settle();
    for (const where of ['workshop-identity', 'workshop-details-panel']) {
        const spotify = page.$('#' + where + ' a.sh-spotify-link');
        assert.equal(spotify.getAttribute('href'), 'https://open.spotify.com/playlist/target', where);
        assert.equal(spotify.getAttribute('target'), '_blank');
        assert.equal(spotify.getAttribute('rel'), 'noopener noreferrer');
        assert.equal(spotify.getAttribute('aria-label'), 'Open Morning Mix on Spotify (new tab)');
        assert.equal(spotify.dataset.workshopCompat, 'view');
        assert.equal(spotify.querySelector('img.sh-spotify-logo').getAttribute('alt'), 'Spotify');
    }
    const choose = page.$$('#workshop-details-panel a').find(link => page.text(link) === 'Choose another playlist');
    assert.equal(choose.getAttribute('href'), '/workshop');
    assert.deepEqual(writes(), []);
    clean();
""")


def test_snapshot_view_lists_tracks_read_only_once_and_a_second_view_closes_them(node, db_app):
    run_history(node, db_app, r"""
    const SNAPSHOT = {success: true, snapshot: {id: 17, playlist_id: 'target', track_count: 3,
        track_uris: [t(2), 'spotify:track:zz', t(1)]}};
    const snapshot = id => ({id, snapshot_type: 'manual', track_count: 3, created_at: '2026-10-12T08:00:00Z',
        trigger_description: 'Before cleanup'});
    await openHistory({'/playlist/target/snapshots': {success: true, snapshots: [snapshot(17), snapshot(18)]},
        '/snapshots/17': SNAPSHOT, '/snapshots/18': {success: true, snapshot: {id: 18, track_uris: [t(3)]}}});
    const viewButton = () => page.$('[data-action-click="wk-snapshot-view"][data-snap-id="17"]');
    const region = () => page.byId('snapshot-view-17');
    const listed = () => page.$$('#snapshot-view-17 li').map(item => page.text(item));
    const view = viewButton();
    assert.match(page.text(view), /^View tracks: Manual, /);
    // A title is text, never markup.
    evaluate("trackDataByUri['spotify:track:t1'].name = 'Track <b>1</b>'");
    assert.equal(view.dataset.workshopCompat, 'view');
    assert.equal(view.getAttribute('aria-controls'), 'snapshot-view-17');
    assert.equal(view.getAttribute('aria-expanded'), 'false'); assert.equal(region().hidden, true);
    const before = page.calls.length;
    view.focus(); view.click(); await settle(); await heard();
    // One read of the snapshot, and its tracks in its card: known titles, else the URI.
    assert.deepEqual(page.calls.slice(before).map(call => [call.method, call.url]), [['GET', '/snapshots/17']]);
    assert.equal(view.getAttribute('aria-expanded'), 'true'); assert.equal(region().hidden, false);
    assert.deepEqual(listed(), ['1. Track 2', '2. spotify:track:zz', '3. Track <b>1</b>']);
    assert.equal(page.$$('#snapshot-view-17 b').length, 0);
    assert.equal(announced(), 'Showing 3 tracks from this snapshot.');
    assert.equal(focused(), view);
    // It wrote nothing and left the draft as it was.
    assert.deepEqual(writes(), []);
    assert.deepEqual(urisOf(), [t(1), t(2), t(3)]); assert.equal(evaluate('workshopState.isDirty'), false);
    // A second View closes the list without a read. A snapshot never changes, so the next
    // opening shows the same list again, still without one.
    view.click(); await settle();
    assert.equal(view.getAttribute('aria-expanded'), 'false'); assert.equal(region().hidden, true);
    assert.equal(reads('/snapshots/17'), 1);
    view.click(); await settle(); await heard();
    assert.equal(view.getAttribute('aria-expanded'), 'true'); assert.equal(region().hidden, false);
    assert.equal(reads('/snapshots/17'), 1);
    assert.deepEqual(listed(), ['1. Track 2', '2. spotify:track:zz', '3. Track <b>1</b>']);
    assert.equal(announced(), 'Showing 3 tracks from this snapshot.');
    // An open list stays open when the timeline is rebuilt, and View stays usable while Save runs.
    evaluate('renderSnapshotTimeline()');
    assert.notEqual(viewButton(), view);
    assert.equal(viewButton().getAttribute('aria-expanded'), 'true');
    assert.deepEqual(listed(), ['1. Track 2', '2. spotify:track:zz', '3. Track <b>1</b>']);
    evaluate('workshopState.isSaving = true; renderWorkshopCompat()');
    assert.equal(viewButton().disabled, false);
    evaluate('workshopState.isSaving = false; renderWorkshopCompat()');
    // A refused read says why in its card, and only the opening after it reads again.
    const other = () => page.$('[data-action-click="wk-snapshot-view"][data-snap-id="18"]');
    page.replies.push(page.reply({success: false, message: 'Snapshot not found.'}, 404));
    other().click(); await settle(); await heard();
    assert.equal(page.text(page.byId('snapshot-view-18')), 'Snapshot not found.');
    assert.equal(announced(), 'Snapshot not found.');
    other().click(); await settle();
    assert.equal(page.byId('snapshot-view-18').hidden, true); assert.equal(reads('/snapshots/18'), 1);
    other().click(); await settle(); await heard();
    assert.equal(reads('/snapshots/18'), 2); assert.equal(page.byId('snapshot-view-18').hidden, false);
    assert.deepEqual(page.$$('#snapshot-view-18 li').map(item => page.text(item)), ['1. Track 3']);
    assert.equal(reads('/snapshots/17'), 1); assert.deepEqual(writes(), []);
    clean();
""")


def test_saved_drafts_list_this_playlists_drafts_with_their_groups(node, db_app):
    run_history(node, db_app, r"""
    const elsewhere = Object.assign(draft(8, 'Elsewhere', [t(1)]), {playlist_id: 'other'});
    await openHistory({'/workshop/target/sessions': {success: true, sessions: [draft(5, 'Workout', [t(3), t(1)]),
        draft(6, 'Chill <b>mix</b>', [t(2)]), elsewhere]}});
    // Each draft shows its name, track count and last change; a draft for another playlist is not listed.
    assert.deepEqual(draftIds(), ['5', '6']);
    assert.equal(rowLines(draftRows()[0])[0], 'Workout');
    assert.match(rowLines(draftRows()[0])[1], /^2 tracks · Updated \S/);
    assert.equal(rowLines(draftRows()[1])[0], 'Chill <b>mix</b>');
    assert.match(rowLines(draftRows()[1])[1], /^1 track · Updated /);
    assert.equal(page.$$('#saved-drafts-list b').length, 0);
    assert.equal(page.byId('saved-drafts-empty').classList.contains('hidden'), true);
    // Load is a draft change; Save, Update and Delete write the saved draft (create). Each names it.
    const groups = id => ['wk-draft-load', 'wk-draft-update', 'wk-draft-delete'].map(action =>
        [page.text(draftControl(action, id)), draftControl(action, id).dataset.workshopCompat]);
    assert.deepEqual(groups(5), [['Load draft: Workout', 'draft'], ['Update with current order: Workout', 'create'],
        ['Delete draft: Workout', 'create']]);
    assert.equal(page.byId('saved-draft-name').dataset.workshopCompat, 'create');
    assert.equal(page.byId('saved-draft-save-btn').dataset.workshopCompat, 'create');
    // While Save runs, draft and create controls wait; their reasons are given.
    evaluate('workshopState.isSaving = true; renderWorkshopCompat()');
    for (const control of [draftControl('wk-draft-load', 5), draftControl('wk-draft-update', 5),
            draftControl('wk-draft-delete', 6), page.byId('saved-draft-save-btn'), page.byId('saved-draft-name')]) {
        assert.equal(control.disabled, true);
        assert.match(control.getAttribute('aria-describedby'), /workshop-compat-reason-/);
    }
    evaluate('workshopState.isSaving = false; renderWorkshopCompat()');
    assert.equal(draftControl('wk-draft-load', 5).disabled, false);
    // A failed read says so and offers to try again.
    page.replies.push(page.reply({success: false, message: 'Please log in again.'}, 401));
    evaluate('loadSavedDrafts()'); await settle();
    assert.equal(page.byId('saved-drafts-error').classList.contains('hidden'), false);
    assert.equal(page.text(page.byId('saved-drafts-error-message')), 'Please log in again.');
    assert.deepEqual(draftIds(), ['5', '6']);
    page.$('[data-action-click="wk-draft-reload"]').click(); await settle();
    assert.equal(page.byId('saved-drafts-error').classList.contains('hidden'), true);
    assert.deepEqual(writes(), []);
    clean();
""")


def test_save_as_named_draft_sends_the_working_order_and_refuses_an_empty_name(node, db_app):
    run_history(node, db_app, r"""
    const LIST = {success: true, sessions: []};
    await openHistory({'/workshop/target/sessions': LIST});
    const name = page.byId('saved-draft-name'), save = page.byId('saved-draft-save-btn');
    const posts = () => sent('POST', '/workshop/target/sessions');
    const listReads = () => sent('GET', '/workshop/target/sessions').length;
    // An empty name is refused at its field, and nothing is sent.
    name.value = '   '; save.focus(); save.click(); await settle(); await heard();
    assert.deepEqual(posts(), []);
    assert.equal(name.getAttribute('aria-invalid'), 'true');
    assert.equal(page.byId('saved-draft-name-error').hidden, false);
    assert.equal(page.text(page.byId('saved-draft-name-error')), 'Enter a name for this draft.');
    assert.equal(focused(), name); assert.equal(announced(), 'Enter a name for this draft.');
    // The order saved is the working order (a row removed here), under the trimmed name. The
    // saved draft comes back in the reply and is listed from it, without reading the list again.
    await removeRow(0);
    name.value = ' Workout ';
    page.replies.push(page.reply({success: true, message: "Session 'Workout' saved.",
        session: draft(5, 'Workout', [t(2), t(3)])}));
    save.focus(); save.click(); await settle(); await heard();
    assert.equal(posts().length, 1);
    assert.deepEqual(JSON.parse(posts()[0].body), {session_name: 'Workout', track_uris: [t(2), t(3)]});
    assert.equal(name.value, ''); assert.equal(name.hasAttribute('aria-invalid'), false);
    assert.equal(page.byId('saved-draft-name-error').hidden, true);
    assert.equal(statusText(), 'Saved "Workout" in Shuffify. Spotify does not change.'); assert.equal(failed(), false);
    saidLast('Saved "Workout" in Shuffify. Spotify does not change.');
    assert.deepEqual(draftIds(), ['5']); assert.equal(focused(), save); assert.equal(listReads(), 1);
    // The draft on the page is untouched.
    assert.deepEqual(plain(evaluate('workshopState.savedUris')), [t(1), t(2), t(3)]);
    assert.deepEqual(urisOf(), [t(2), t(3)]);
    // The server's limit is shown as it words it, and the name stays for another try.
    name.value = 'Eleventh';
    const limit = 'Maximum of 10 saved sessions per playlist reached. Delete an existing session first.';
    page.replies.push(page.reply({success: false, message: limit}, 400));
    save.click(); await settle(); await heard();
    assert.equal(posts().length, 2);
    assert.equal(statusText(), limit); assert.equal(failed(), true); saidLast(limit);
    assert.equal(name.value, 'Eleventh');
    // Enter in the name field saves as well.
    page.replies.push(page.reply({success: true, session: draft(6, 'Eleventh', [t(2), t(3)])}));
    page.key(name, 'Enter'); await settle();
    assert.equal(posts().length, 3); assert.equal(JSON.parse(posts()[2].body).session_name, 'Eleventh');
    // Pressed again while a save is in flight, it saves once. Only saved-draft routes were written.
    name.value = 'Twelfth';
    page.replies.push(page.reply({success: true, session: draft(7, 'Twelfth', [t(2), t(3)])}));
    save.click(); save.click(); page.key(name, 'Enter'); await settle();
    assert.equal(posts().length, 4);
    assert.deepEqual(writes().map(call => call.url), Array(4).fill('/workshop/target/sessions'));
    // Each saved draft goes first in the list (latest change first); the list was read once.
    assert.deepEqual(draftIds(), ['7', '6', '5']); assert.equal(listReads(), 1);
    clean();
""")


def test_update_always_sends_the_working_order_and_keeps_focus_on_update(node, db_app):
    run_history(node, db_app, r"""
    const LIST = {success: true, sessions: [draft(5, 'Workout', [t(3)]), draft(6, 'Chill', [t(2)])]};
    await openHistory({'/workshop/target/sessions': LIST});
    const puts = () => sent('PUT', '/workshop/sessions/6');
    await removeRow(2);
    // The updated draft comes back in the reply and moves first in the list (latest change first),
    // without reading the list again; focus stays on its Update.
    page.replies.push(page.reply({success: true,
        session: draft(6, 'Chill', [t(1), t(2)], '2026-10-12T09:00:00+00:00')}));
    const update = draftControl('wk-draft-update', 6);
    update.focus(); update.click(); await settle(); await heard();
    assert.equal(puts().length, 1);
    assert.deepEqual(JSON.parse(puts()[0].body), {track_uris: [t(1), t(2)], session_name: 'Chill'});
    assert.deepEqual(draftIds(), ['6', '5']); assert.equal(reads('/workshop/target/sessions'), 1);
    assert.equal(rowLines(draftRows()[0])[0], 'Chill'); assert.match(rowLines(draftRows()[0])[1], /^2 tracks · /);
    assert.notEqual(draftControl('wk-draft-update', 6), update);
    assert.equal(focused(), draftControl('wk-draft-update', 6));
    assert.equal(statusText(), 'Updated "Chill" with the order shown here. Spotify does not change.');
    saidLast('Updated "Chill" with the order shown here. Spotify does not change.');
    // An empty working order is still sent: the route would clear the tracks if they were left out.
    await removeRow(0); await removeRow(0);
    assert.deepEqual(urisOf(), []);
    page.replies.push(page.reply({success: true, session: draft(6, 'Chill', [])}));
    draftControl('wk-draft-update', 6).click(); await settle();
    assert.deepEqual(JSON.parse(puts()[1].body), {track_uris: [], session_name: 'Chill'});
    // A refused update shows the server's reason.
    page.replies.push(page.reply({success: false, message: 'Saved session not found.'}, 404));
    draftControl('wk-draft-update', 5).click(); await settle(); await heard();
    assert.equal(statusText(), 'Saved session not found.'); assert.equal(failed(), true);
    saidLast('Saved session not found.');
    // Without a reply the outcome is unknown: it says so, and the list is read again.
    const listReads = reads('/workshop/target/sessions');
    page.replies.push(() => { throw new Error('connection reset'); });
    draftControl('wk-draft-update', 5).click(); await settle();
    assert.equal(statusText(),
        'No reply came from Shuffify, so this change may not have been made. The list shows what is saved.');
    assert.equal(failed(), true); assert.equal(reads('/workshop/target/sessions'), listReads + 1);
    // A reply that cannot be read leaves the outcome unknown as well: never a success.
    page.replies.push(Object.assign(page.reply(null), {json: async () => { throw new SyntaxError('Unexpected <'); }}));
    draftControl('wk-draft-update', 5).click(); await settle();
    assert.equal(statusText(),
        'No reply came from Shuffify, so this change may not have been made. The list shows what is saved.');
    assert.equal(failed(), true); assert.equal(reads('/workshop/target/sessions'), listReads + 2);
    assert.deepEqual(writes().map(call => call.method), ['PUT', 'PUT', 'PUT', 'PUT', 'PUT']);
    clean();
""")


def test_delete_asks_first_and_focus_moves_to_the_next_or_previous_rows_delete(node, db_app):
    run_history(node, db_app, r"""
    const LIST = {success: true, sessions: [draft(5, 'A', [t(1)]), draft(6, 'B', [t(2)]), draft(7, 'C', [t(3)])]};
    await openHistory({'/workshop/target/sessions': LIST});
    const deletes = id => sent('DELETE', '/workshop/sessions/' + id);
    // While the question is open the list waits to rebuild. Cancelled, nothing is sent, the list
    // rebuilds, and focus is on Delete in the rebuilt row.
    const asking = draftControl('wk-draft-delete', 6), answer = holdQuestion();
    asking.focus(); asking.click(); await settle();
    assert.equal(page.asked.length, 1);
    const asked = page.asked[0];
    assert.equal(asked.title, 'Delete "B"?'); assert.equal(asked.confirmLabel, 'Delete draft');
    assert.equal(asked.message, 'This saved draft is permanently deleted from Shuffify. ' +
        'The order on this page and your playlist on Spotify do not change.');
    assert.equal(asked.trigger, asking);
    evaluate('loadSavedDrafts()'); await settle();
    assert.equal(asking.isConnected, true);
    await answer(false);
    assert.equal(asking.isConnected, false);
    const remove = draftControl('wk-draft-delete', 6);
    assert.deepEqual(deletes(6), []); assert.equal(focused(), remove);
    // Confirmed: one deletion, the row goes without reading the list again, and focus moves to
    // Delete on the next row.
    page.answer = true;
    const listReads = reads('/workshop/target/sessions');
    page.replies.push(page.reply({success: true, message: 'Session deleted.'}));
    remove.click(); await settle(); await heard();
    assert.equal(deletes(6).length, 1); assert.equal(deletes(6)[0].body, undefined);
    assert.deepEqual(draftIds(), ['5', '7']);
    assert.equal(focused(), draftControl('wk-draft-delete', 7));
    assert.equal(statusText(), 'Deleted "B".'); saidLast('Deleted "B".');
    assert.equal(reads('/workshop/target/sessions'), listReads);
    // The last row's deletion moves focus to Delete on the previous row.
    page.replies.push(page.reply({success: true}));
    draftControl('wk-draft-delete', 7).focus(); draftControl('wk-draft-delete', 7).click(); await settle();
    assert.deepEqual(draftIds(), ['5']); assert.equal(focused(), draftControl('wk-draft-delete', 5));
    // A refused deletion keeps the row and says why.
    page.replies.push(page.reply({success: false, message: 'Saved session not found.'}, 404));
    draftControl('wk-draft-delete', 5).click(); await settle();
    assert.deepEqual(draftIds(), ['5']); assert.equal(statusText(), 'Saved session not found.');
    // Only a deletion without a reply reads the list again, to show what is saved.
    assert.equal(reads('/workshop/target/sessions'), listReads);
    LIST.sessions = [draft(5, 'A', [t(1)])];
    page.replies.push(() => { throw new Error('connection reset'); });
    draftControl('wk-draft-delete', 5).click(); await settle();
    assert.deepEqual(draftIds(), ['5']); assert.equal(reads('/workshop/target/sessions'), listReads + 1);
    assert.equal(statusText(),
        'No reply came from Shuffify, so this change may not have been made. The list shows what is saved.');
    // The only row's deletion leaves focus in the section.
    page.replies.push(page.reply({success: true}));
    draftControl('wk-draft-delete', 5).focus(); draftControl('wk-draft-delete', 5).click(); await settle();
    assert.deepEqual(draftIds(), []); assert.equal(focused(), draftsSection());
    assert.deepEqual(writes().map(call => call.method + ' ' + call.url), ['DELETE /workshop/sessions/6',
        'DELETE /workshop/sessions/7', 'DELETE /workshop/sessions/5', 'DELETE /workshop/sessions/5',
        'DELETE /workshop/sessions/5']);
    assert.equal(reads('/workshop/target/sessions'), listReads + 1);
    clean();
""")


def test_loading_a_saved_draft_replaces_the_working_order_keeping_each_tracks_keys(node, db_app):
    run_history(node, db_app, r"""
    await openHistory({'/workshop/target/sessions': {success: true, sessions: [draft(5, 'Workout', [t(3), t(1)])]},
        '/workshop/sessions/5': {success: true, session: draft(5, 'Workout', [t(3), t(1), 'spotify:track:zz', t(1)])}});
    const [k1, k2, k3] = keysOf();
    const load = draftControl('wk-draft-load', 5);
    load.focus(); load.click(); load.click(); await settle(); await heard();
    // Pressed twice, only the latest press loads; with nothing to discard nothing is asked, and
    // nothing is written.
    assert.equal(page.asked.length, 0);
    assert.equal(reads('/workshop/sessions/5'), 2); assert.deepEqual(writes(), []);
    // The saved draft is the working order. Each track keeps its rows' keys; added copies get new ones.
    assert.deepEqual(urisOf(), [t(3), t(1), 'spotify:track:zz', t(1)]);
    const keys = keysOf();
    assert.deepEqual(keys.slice(0, 2), [k3, k1]);
    assert.equal(new Set(keys).size, 4); assert.ok(!keys.slice(2).some(key => [k1, k2, k3].includes(key)));
    // A track this page had not seen gets the usual placeholder row.
    assert.equal(page.text(rows()[2].querySelector('.track-title')), 'Track zz');
    // The saved order is unchanged, so the page is a Draft against it.
    assert.deepEqual(plain(evaluate('workshopState.savedUris')), [t(1), t(2), t(3)]);
    assert.equal(evaluate('workshopState.isDirty'), true);
    assert.equal(page.byId('undo-preview-btn').classList.contains('hidden'), false);
    assert.equal(statusText(), 'Loaded "Workout" as your draft. Spotify does not change until you save.');
    assert.match(announced(), /Loaded "Workout" as your draft\. Spotify does not change until you save\./);
    assert.equal(focused(), load);
    agrees(); clean();
""")


def test_loading_over_a_draft_or_staging_asks_and_discards_locally_first(node, db_app):
    run_history(node, db_app, r"""
    await openHistory({'/workshop/target/sessions': {success: true, sessions: [draft(5, 'Workout', [t(3), t(1)])]},
        '/workshop/sessions/5': {success: true, session: draft(5, 'Workout', [t(3), t(1)])}});
    const k3 = keysOf()[2];
    await removeRow(0);
    const load = draftControl('wk-draft-load', 5);
    // With a Draft the load asks first, and the list waits to rebuild meanwhile. Cancelled, the
    // draft stays, and focus is on Load in the rebuilt list.
    const answer = holdQuestion();
    load.focus(); load.click(); await settle();
    assert.equal(page.asked.length, 1);
    const asked = page.asked[0];
    assert.deepEqual([asked.title, asked.confirmLabel, asked.cancelLabel],
        ['Discard your draft changes and load Workout?', 'Discard and load', 'Cancel']);
    assert.equal(asked.trigger, load);
    evaluate('loadSavedDrafts()'); await settle();
    assert.equal(load.isConnected, true);
    await answer(false);
    assert.equal(load.isConnected, false);
    const reload = draftControl('wk-draft-load', 5);
    assert.deepEqual(urisOf(), [t(2), t(3)]); assert.equal(focused(), reload);
    // Staged tracks ask too. Confirmed, the local discard runs first: the staged track is back in
    // the Inbox, and only then does the saved draft become the working order.
    page.byId('workshop-tool-inbox').click(); await settle();
    page.$('#inbox-raids-list > div[data-track-id="1"] .inbox-add-btn').click(); await settle();
    assert.deepEqual(urisOf(), [t(2), t(3), 'spotify:track:p1']);
    assert.equal(evaluate('pendingRaidsState.stagedPromotions.length'), 1);
    page.byId('workshop-tool-history').click(); await settle();
    page.answer = true;
    reload.focus(); reload.click(); await settle(); await heard();
    assert.equal(page.asked.length, 2);
    assert.equal(evaluate('pendingRaidsState.stagedPromotions.length'), 0);
    assert.ok(plain(evaluate('pendingRaidsState.tracks')).some(track => track.id === 1));
    assert.deepEqual(urisOf(), [t(3), t(1)]); assert.equal(keysOf()[0], k3);
    assert.deepEqual(plain(evaluate('workshopState.savedUris')), [t(1), t(2), t(3)]);
    assert.match(announced(), /Loaded "Workout" as your draft\./); assert.equal(focused(), reload);
    assert.deepEqual(writes(), []);
    agrees(); clean();
""")


def test_load_is_refused_with_a_reason_while_it_cannot_change_the_draft(node, db_app):
    run_history(node, db_app, r"""
    const SESSION = {success: true, session: draft(5, 'Workout', [t(3), t(1)])};
    await openHistory({'/workshop/target/sessions': {success: true, sessions: [draft(5, 'Workout', [t(3), t(1)])]},
        '/workshop/sessions/5': SESSION});
    const load = draftControl('wk-draft-load', 5);
    const unchanged = () => assert.deepEqual(urisOf(), [t(1), t(2), t(3)]);
    // Saved work that needs attention refuses the load, before any read.
    evaluate("workshopState.saveAttention = {retryable: false, message: 'Check the playlist.'}");
    load.click(); await settle(); await heard();
    assert.equal(reads('/workshop/sessions/5'), 0); unchanged();
    assert.equal(statusText(), 'Resolve the saved work in Save results before loading a saved draft.');
    assert.equal(failed(), true); assert.equal(announced(), statusText());
    evaluate("workshopState.saveAttention = null; workshopState.sourceRecovery = [{kind: 'raids'}]");
    load.click(); await settle();
    assert.equal(reads('/workshop/sessions/5'), 0); unchanged();
    evaluate('workshopState.sourceRecovery = []');
    // Attention that arrives while the draft is read still refuses it.
    page.replies.push(() => {
        evaluate("workshopState.saveAttention = {retryable: false, message: 'Check the playlist.'}");
        return page.reply(SESSION);
    });
    load.click(); await settle();
    assert.equal(reads('/workshop/sessions/5'), 1); unchanged();
    assert.equal(statusText(), 'Resolve the saved work in Save results before loading a saved draft.');
    evaluate('workshopState.saveAttention = null');
    // While Save runs (a blocked draft) and while a shuffle preview is coming, it says why.
    evaluate('workshopState.isSaving = true');
    evaluate('loadSavedDraft(5, null)'); await settle();
    assert.equal(statusText(), 'Unavailable while your draft saves to Spotify.');
    evaluate('workshopState.isSaving = false; workshopState.isShuffling = true');
    load.click(); await settle();
    assert.equal(statusText(), 'Wait for the shuffle preview to finish before loading a saved draft.');
    evaluate('workshopState.isShuffling = false');
    assert.equal(reads('/workshop/sessions/5'), 1); unchanged();
    // Writes in a blocked group are refused before anything is asked or sent.
    evaluate('workshopState.isSaving = true');
    for (const call of ['deleteSavedDraft(5, null)', 'updateSavedDraft(5, null)']) {
        evaluate(call); await settle();
        assert.equal(statusText(), 'Unavailable while your draft saves to Spotify.');
    }
    evaluate("document.getElementById('saved-draft-name').value = 'Kept'; saveNamedDraft()"); await settle();
    assert.equal(statusText(), 'Unavailable while your draft saves to Spotify.');
    evaluate('workshopState.isSaving = false');
    assert.equal(page.asked.length, 0); assert.deepEqual(writes(), []);
    clean();
""")


def test_only_a_draft_listed_for_this_playlist_loads(node, db_app):
    run_history(node, db_app, r"""
    await openHistory({'/workshop/target/sessions': {success: true, sessions: [draft(5, 'Workout', [t(3)])]},
        '/workshop/sessions/5': {success: true, session: Object.assign(draft(5, 'Workout', [t(3)]),
            {playlist_id: 'other'})}});
    // The read says the draft belongs to another playlist: nothing changes.
    draftControl('wk-draft-load', 5).click(); await settle(); await heard();
    assert.equal(reads('/workshop/sessions/5'), 1);
    assert.deepEqual(urisOf(), [t(1), t(2), t(3)]); assert.equal(page.asked.length, 0);
    assert.equal(statusText(), 'This saved draft belongs to another playlist, so it was not loaded.');
    assert.equal(announced(), statusText());
    // A draft the list does not show is refused without a read.
    evaluate('loadSavedDraft(9, null)'); await settle();
    assert.equal(statusText(), 'That saved draft is no longer listed for this playlist.');
    assert.equal(page.calls.filter(call => call.url === '/workshop/sessions/9').length, 0);
    // A failed read says so and changes nothing.
    page.replies.push(page.reply({success: false, message: 'Saved session not found.'}, 404));
    draftControl('wk-draft-load', 5).click(); await settle();
    assert.equal(statusText(), 'Saved session not found.'); assert.deepEqual(urisOf(), [t(1), t(2), t(3)]);
    assert.deepEqual(writes(), []);
    clean();
""")
