"""Workshop's compact exact-ID routine collection on its rendered page."""

from tests.test_playlist_operations_runtime import node as node  # noqa: F401
from tests.test_workshop_live_runtime import run_workspace, workspace_page


def test_workshop_shows_all_exact_rows_and_batched_latest(node, db_app):
    run_workspace(node, workspace_page(db_app), r"""
await openWorkshop('/workshop/target', {
    '/api/playlist/target/routines/context': {success: true, routines: [
        {schedule_id: 5, schedule_revision: 'five', schedule: {id: 5,
            target_playlist_id: 'target', target_playlist_name: 'Morning Mix',
            job_type: 'raid', is_enabled: true}, timing: {text: 'Every day'},
            registration: {status: 'registered'}, state: 'Active'},
        {schedule_id: 6, schedule_revision: 'six', schedule: {id: 6,
            target_playlist_id: 'target', target_playlist_name: 'Morning Mix',
            job_type: 'shuffle', is_enabled: false}, timing: {text: 'Every week'},
            registration: {status: 'paused'}, state: 'Paused'}],
        capabilities: {raid: {available: false, reason: 'Add a source.'}}},
    '/api/schedules/latest-results': {success: true, results: {'5': null, '6': null}},
});
await evaluate('playlistSchedules.loadSchedules()');
await settle();
assert.deepEqual(page.$$('#schedules-list .schedule-card').map(card => card.dataset.scheduleId), ['5', '6']);
assert.equal(reads('/api/schedules/latest-results'), 1);
assert.match(page.text(page.$('#schedules-list .schedule-card[data-schedule-id="5"]')),
    /Missing prerequisite: Add a source/);
assert.equal(page.$('#schedules-list .schedule-card[data-schedule-id="6"] .routine-edit').getAttribute('href'),
    '/playlist/target/routines/new?schedule_id=6&from=playlist');
assert.equal(page.calls.filter(call => call.method !== 'GET').length, 0);
""")


def test_archive_inbox_initializes_cache_and_loads_tracks(node, db_app):
    run_workspace(node, workspace_page(db_app), r"""
await openWorkshop('/workshop/target', {
    '/api/playlist/target/pair/archive-tracks': {success: true, tracks: [], total: 0},
});
evaluate("workshopState.archivePair = {archive_playlist_id: 'archive', archive_playlist_name: 'Archive'}");
evaluate('loadArchiveTracksForInbox()'); await settle();
assert.equal(reads('/api/playlist/target/pair/archive-tracks'), 1);
assert.equal(evaluate('archiveInboxLoaded'), true);
assert.deepEqual(page.errors, []);
""")


def test_failed_refresh_removes_stale_actionable_cards_and_retry_restores(node, db_app):
    run_workspace(node, workspace_page(db_app), r"""
const context = {success: true, routines: [{schedule_id: 5, schedule_revision: 'five',
    schedule: {id: 5, target_playlist_id: 'target', target_playlist_name: 'Morning Mix',
        job_type: 'raid', is_enabled: true}, timing: {text: 'Every day'},
    registration: {status: 'registered'}, state: 'Active'}], capabilities: {}};
await openWorkshop('/workshop/target', {
    '/api/playlist/target/routines/context': context,
    '/api/schedules/latest-results': {success: true, results: {'5': null}},
});
await evaluate('playlistSchedules.loadSchedules()');
assert.ok(page.$('#schedules-list .schedule-card[data-schedule-id="5"] .routine-toggle'));
page.replies.push(page.reply({success: false, message: 'unavailable'}, 503));
await evaluate('playlistSchedules.loadSchedules()');
assert.equal(evaluate('playlistSchedules.loaded'), false);
assert.deepEqual(plain(evaluate('playlistSchedules.schedules')), []);
assert.equal(page.$('#schedules-list .schedule-card'), null);
assert.equal(page.byId('schedules-error').hidden, false);
assert.equal(page.byId('schedules-error').getAttribute('role'), 'alert');
assert.equal(page.byId('schedules-empty').hidden, true);
assert.equal(page.$('#schedules-list .routine-run'), null);
page.byId('schedules-error').querySelector('[data-action-click="wk-060"]').click();
await settle();
assert.equal(evaluate('playlistSchedules.loaded'), true);
assert.ok(page.$('#schedules-list .schedule-card[data-schedule-id="5"] .routine-toggle'));
assert.equal(page.byId('schedules-error').hidden, true);
""")


def test_latest_result_generation_ignores_older_success_and_failure(node, db_app):
    run_workspace(node, workspace_page(db_app), r"""
await openWorkshop('/workshop/target', {
    '/api/playlist/target/routines/context': {success: true, routines: [{schedule_id: 5,
        schedule_revision: 'five', schedule: {id: 5, target_playlist_id: 'target',
            target_playlist_name: 'Morning Mix', job_type: 'raid', is_enabled: true},
        timing: {text: 'Every day'}, registration: {status: 'registered'}, state: 'Active'}],
        capabilities: {}},
    '/api/schedules/latest-results': {success: true, results: {'5': null}},
});
await evaluate('playlistSchedules.loadSchedules()');
const card = () => page.$('#schedules-list .schedule-card[data-schedule-id="5"]');
async function overlap(olderReply, newerReply) {
    let releaseOlder, releaseNewer;
    page.replies.push(() => new Promise(resolve => { releaseOlder = resolve; }),
        () => new Promise(resolve => { releaseNewer = resolve; }));
    const older = evaluate('playlistSchedules.manager.loadLatest()');
    const newer = evaluate('playlistSchedules.manager.loadLatest()');
    await tick();
    releaseNewer(newerReply); await newer;
    const afterNewer = {text: page.text(card()),
        error: page.$('#workshop-routine-management [data-routine-result-error]').hidden};
    releaseOlder(olderReply); await older;
    assert.deepEqual({text: page.text(card()),
        error: page.$('#workshop-routine-management [data-routine-result-error]').hidden}, afterNewer);
    return afterNewer;
}
const newestFailure = await overlap(page.reply({success: true, results: {'5': null}}),
    page.reply({success: false}, 503));
assert.match(newestFailure.text, /Latest result unavailable/);
assert.equal(newestFailure.error, false);
const newestSuccess = await overlap(page.reply({success: false}, 503),
    page.reply({success: true, results: {'5': null}}));
assert.match(newestSuccess.text, /Never run/);
assert.equal(newestSuccess.error, true);
""")
