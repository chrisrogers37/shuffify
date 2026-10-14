"""Routine edit navigation uses the dedicated exact routine form and live guard."""

from tests.page_runtime import run_page
from tests.test_playlist_operations_runtime import node as node  # noqa: F401
from tests.test_routines_runtime import START, page


def test_edit_is_a_dedicated_exact_id_link_without_inline_cron_editor(node, db_app):
    run_page(node, page(db_app), START + r"""
const edit = card(5).querySelector('.routine-edit');
assert.equal(edit.getAttribute('href'),
    '/playlist/target/routines/new?schedule_id=5&from=routines');
assert.equal(page.byId('edit-panel-5'), null);
assert.equal(page.window.ScheduleEdit, undefined);
assert.equal(page.$('[name="schedule_type"]'), null);
""")


def test_edit_navigation_from_unknown_run_requires_explicit_loss_acknowledgement(node, db_app):
    run_page(node, page(db_app), START + r"""
page.replies.push(page.reply({success: true, context: reviewContext}));
card(5).querySelector('.routine-run').click(); await settle();
page.replies.push(() => { throw new TypeError('lost reply'); });
page.byId('live-operation-run').click(); await settle();
assert.equal(page.window.PlaylistOperations.hasAttention(), true);
const edit = card(5).querySelector('.routine-edit');
const refused = page.window.AppNavigation.assign(edit.getAttribute('href'), edit);
await tick();
assert.match(page.text(page.byId('live-operation-leave-text')), /leav|los/i);
page.byId('live-operation-leave-stay').click();
assert.equal(await refused, false); assert.deepEqual(page.navigations, []);
const accepted = page.window.AppNavigation.assign(edit.getAttribute('href'), edit);
await tick();
page.byId('live-operation-leave-confirm').click();
assert.equal(await accepted, true);
assert.deepEqual(page.navigations,
    ['/playlist/target/routines/new?schedule_id=5&from=routines']);
""")
