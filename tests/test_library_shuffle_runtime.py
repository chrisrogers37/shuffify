"""Exercise Library controls with the shipped controller, reviews and small DOM stubs."""

from tests.test_playlist_operations_runtime import CLIENT, run_node
from tests.test_playlist_operations_runtime import node as node
from tests.test_reviewed_live_actions_runtime import REVIEW_FIXTURE

LIBRARY = (
    CLIENT
    + REVIEW_FIXTURE
    + """
const notices = [], cards = [];
function dom(id, tag = 'div') {
    const item = element(id), classes = new Set();
    Object.assign(item, {tagName: tag.toUpperCase(), value: '', hidden: false,
        classList: {add: key => classes.add(key), remove: key => classes.delete(key),
            contains: key => classes.has(key),
            toggle: (key, yes) => yes ? classes.add(key) : classes.delete(key)},
        append(...children) {children.forEach(child => this.appendChild(child));},
        focus() {document.activeElement = this;},
        querySelector: () => null, querySelectorAll: () => [],
        reportValidity() {this.reported = true; this.focus();},
        checkValidity() {return this.valid !== false;}});
    return item;
}
document.createElement = tag => dom(tag + '-' + elements.size, tag);
const library = dom('library');
function makeCard(id, count = 6, method = 'BasicShuffle') {
    const tile = dom('tile-' + id), form = dom('form-' + id, 'form'), undoForm = dom('undo-form-' + id, 'form');
    tile.dataset = {playlistId: id, playlistName: id + ' playlist'};
    form.dataset = {playlistId: id, trackCount: String(count),
        shuffleDefaults: JSON.stringify({algorithm: method, params: {keep_first: 2}})};
    form.action = '/shuffle/' + id; undoForm.action = '/undo/' + id;
    const names = ['method', 'details', 'summary', 'params', 'description', 'feedback',
        'done', 'method-name', 'method-change'];
    const parts = Object.fromEntries(names.map(name => [name, dom(id + '-' + name,
        name === 'method' ? 'select' : name === 'summary' ? 'summary' : name === 'done' ? 'button' : 'div')]));
    const quick = dom(id + '-quick', 'button'), undo = dom(id + '-undo', 'button');
    const visibility = dom(id + '-privacy', 'button'), visibilityLabel = dom(id + '-privacy-label');
    quick.dataset.liveOperationKey = 'shuffle:' + id; undo.dataset.liveOperationKey = 'undo:' + id;
    visibility.dataset = {liveOperationKey: 'privacy:' + id, public: 'true'};
    visibility.querySelector = () => visibilityLabel;
    quick.textContent = 'Shuffle now'; undo.textContent = 'Restore previous saved version now';
    quick.disabled = true; undo.classList.add('hidden'); parts.method.disabled = true;
    const options = {'': {textContent: '', dataset: {}},
        BasicShuffle: {textContent: 'Basic', dataset: {description: 'Basic description',
            parameters: JSON.stringify({keep_first: {type: 'integer', default: 0, min: 0, description: 'Keep'}})}},
        ArtistSpacingShuffle: {textContent: 'Artist spacing', dataset: {description: 'Artist description',
            parameters: JSON.stringify({min_spacing: {type: 'integer', default: 2, min: 1, description: 'Spacing'}})}}};
    parts.method.value = method; parts.method.name = 'algorithm';
    Object.defineProperty(parts.method, 'selectedOptions', {get: () => [options[parts.method.value]]});
    parts.params.querySelectorAll = () => parts.params.children.flatMap(wrapper => wrapper.children.slice(1));
    const selectors = {'.library-shuffle-method': count ? parts.method : null,
        '.library-shuffle-details': count ? parts.details : null,
        '.library-shuffle-summary': count ? parts.summary : null,
        '.library-shuffle-params': parts.params, '.library-shuffle-description': parts.description,
        '.library-quick-shuffle': quick, '.library-undo': undo, '.library-shuffle-feedback': parts.feedback,
        '.library-shuffle-done': parts.done, '.library-method-name': parts['method-name'],
        '.library-method-change': parts['method-change']};
    form.querySelector = selector => selectors[selector];
    form.closest = () => tile; visibility.closest = () => tile;
    tile.querySelector = selector => selector === '.undo-form' ? undoForm :
        selector === '.visibility-btn' ? visibility : null;
    parts.details.querySelector = () => parts.summary;
    form.fields = () => [['csrf_token', 'test-csrf'],
        ...(count && !parts.method.disabled ? [['algorithm', parts.method.value]] : []),
        ...parts.params.querySelectorAll().filter(input => !input.disabled)
            .map(input => [input.name, String(input.value)])];
    undoForm.fields = () => [['csrf_token', 'test-csrf']];
    const card = {id, tile, form, undoForm, quick, undo, visibility, visibilityLabel, ...parts};
    cards.push(card); return card;
}
const first = makeCard('first'), second = makeCard('second'), empty = makeCard('empty', 0);
library.querySelectorAll = selector => {
    if (selector === '.library-shuffle-form') return cards.map(card => card.form);
    if (selector === '.card-tile') return cards.map(card => card.tile);
    if (selector === 'summary') return cards.map(card => card.summary);
    if (selector === 'details') return cards.map(card => card.details);
    return [];
};
document.querySelector = selector => selector === '.sh-library' ? library : null;
context.FormData = class extends FormData {
    constructor(form) {super(); if (form) form.fields().forEach(([key, value]) => this.append(key, value));}
};
context.showNotification = (message, category) => notices.push({message, category});
vm.runInContext(fs.readFileSync('shuffify/static/js/library-shuffle.js', 'utf8'), context);
listeners.DOMContentLoaded[0]();
const direct = (index = 1, id = 'first') => ({success: true, message: 'Verified saved version.',
    playlist_state: {current_index: index}, operation: {kind: 'shuffle', aggregate_status: 'complete',
        resources: [resource(id, 'complete', {current_track_uris: [uri(1), uri(2)]})],
        bookkeeping: [{kind: 'session_history', status: 'complete'}]}});
async function review(click) { const pending = click(); await tick(); await tick(); return {done: pending}; }
"""
)


def test_library_keeps_options_validates_without_writes_and_preserves_empty_behavior(
    node,
):
    run_node(
        node,
        LIBRARY
        + """
(async () => {
    assert.equal(calls.length, 0); assert.equal(empty.quick.disabled, true);
    assert.equal(String(first.params.querySelectorAll()[0].value), '2');
    first.params.querySelectorAll()[0].value = '4';
    first.method.value = 'ArtistSpacingShuffle'; first.method.change();
    first.params.querySelectorAll()[0].value = '3';
    first.method.value = 'BasicShuffle'; first.method.change();
    assert.equal(first.params.querySelectorAll()[0].value, '4');
    first.method.value = ''; first.method.change(); await first.quick.click();
    assert.equal(document.activeElement, first.method); assert.equal(calls.length, 0);
    first.method.value = 'BasicShuffle'; first.method.change();
    first.params.querySelectorAll()[0].valid = false; await first.quick.click();
    assert.equal(first.params.querySelectorAll()[0].reported, true); assert.equal(calls.length, 0);
    first.done.click(); assert.equal(document.activeElement, first.summary); assert.equal(first.details.open, false);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_library_review_then_run_serializes_cards_and_updates_only_verified_history(
    node,
):
    run_node(
        node,
        LIBRARY
        + """
(async () => {
    replies.push(contextReply('shuffle', {}, 'first'));
    const pending = await review(() => first.quick.click());
    assert.equal(calls.length, 1); assert.equal(calls[0].url, '/playlist/first/live-context');
    assert.deepEqual(JSON.parse(calls[0].options.body),
        {kind: 'shuffle', form: {algorithm: 'BasicShuffle', keep_first: '2'}});
    // Reviewing is not dispatch: the card keeps its label until the final Run.
    assert.equal(first.quick.textContent, 'Shuffle now'); assert.equal(second.quick.disabled, false);
    let finish; replies.push(() => new Promise(resolve => {finish = resolve;}));
    runReview(); await tick(); await tick();
    assert.equal(first.quick.textContent, 'Shuffling…');
    assert.equal(second.quick.disabled, true); assert.equal(second.visibility.disabled, true);
    await second.quick.click(); await second.undo.click();
    assert.equal(await window.LibraryPlaylistActions.setVisibility('second', second.visibility), null);
    assert.equal(calls.length, 2); assert.equal(await guards[0]({kind: 'reload'}), false);
    assert.deepEqual(Object.fromEntries(calls[1].options.body),
        {csrf_token: 'test-csrf', algorithm: 'BasicShuffle', keep_first: '2', reviewed_scope: SCOPE});
    assert.equal(first.undo.classList.contains('hidden'), true);
    finish(response(direct())); await pending.done;
    assert.equal(first.quick.textContent, 'Shuffle now');
    assert.equal(first.undo.classList.contains('hidden'), false); assert.equal(second.quick.disabled, false);
    assert.equal(empty.quick.disabled, true); assert.equal(await guards[0]({kind: 'reload'}), true);
    replies.push(contextReply('undo', {}, 'first'), response(direct(0)));
    const focused = document.activeElement;
    const undo = await review(() => first.undo.click()); runReview(); await undo.done;
    assert.equal(calls[3].url, '/undo/first');
    assert.deepEqual(Object.fromEntries(calls[3].options.body), {csrf_token: 'test-csrf', reviewed_scope: SCOPE,
        reviewed_before_order: BEFORE, reviewed_history: HISTORY});
    // Undo hides when no earlier version remains; focus stays where the run left it.
    assert.equal(first.undo.classList.contains('hidden'), true); assert.equal(document.activeElement, focused);
    assert.equal(client.getRecords()[1].displayAttention, undefined);
    assert.equal(first.params.querySelectorAll()[0].value, 2);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_library_unknown_undo_blocks_that_playlist_and_survives_unlock(node):
    run_node(
        node,
        LIBRARY
        + """
(async () => {
    first.undo.classList.remove('hidden');
    const failed = direct(0); failed.success = false; failed.operation.aggregate_status = 'unknown';
    failed.operation.resources[0].status = 'unknown';
    replies.push(contextReply('undo', {}, 'first'), response(failed, 502));
    const undo = await review(() => first.undo.click()); runReview(); await undo.done;
    assert.equal(first.undo.disabled, true); assert.equal(first.quick.disabled, true);
    assert.equal(first.undo.classList.contains('hidden'), false);
    assert.equal(notices.filter(note => note.category === 'success').length, 0);
    await first.undo.click(); await first.quick.click(); assert.equal(calls.length, 2);
    // Its saved history is known complete, so another card can still review and run.
    replies.push(contextReply('shuffle', {}, 'second'), response(direct(1, 'second')));
    const other = await review(() => second.quick.click()); runReview(); await other.done;
    assert.equal(calls.length, 4); assert.equal(first.undo.disabled, true); assert.equal(first.quick.disabled, true);
    assert.equal(client.getRecords()[0].response.playlist_state.current_index, 0);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_library_lost_shuffle_blocks_track_actions_on_every_card_but_privacy_still_reviews(
    node,
):
    run_node(
        node,
        LIBRARY
        + """
(async () => {
    first.params.querySelectorAll()[0].value = '3';
    replies.push(contextReply('shuffle', {}, 'first'), () => {throw new Error('Lost shuffle response');});
    const lost = await review(() => first.quick.click()); runReview(); await lost.done;
    await first.quick.click(); await second.quick.click(); assert.equal(calls.length, 2);
    assert.equal(first.quick.disabled, true); assert.equal(second.quick.disabled, true);
    assert.equal(first.visibility.disabled, false);
    replies.push(contextReply('privacy', {}, 'first'), response({success: true, public: false,
        message: 'Playlist set to private',
        operation: {kind: 'privacy', aggregate_status: 'complete',
            resources: [resource('first', 'complete', {step: 'privacy', verification: 'metadata'})],
            bookkeeping: []}}));
    const privacy = await review(() => window.LibraryPlaylistActions.setVisibility('first', first.visibility));
    assert.deepEqual(JSON.parse(calls[2].options.body), {kind: 'privacy', public: false});
    runReview(); await privacy.done;
    assert.equal(calls.length, 4);
    assert.deepEqual(JSON.parse(calls[3].options.body), {public: false, reviewed_scope: SCOPE});
    assert.equal(first.visibility.dataset.public, 'false');
    assert.equal(first.visibilityLabel.textContent, 'Make public now');
    assert.equal(first.params.querySelectorAll()[0].value, '3'); assert.equal(first.quick.disabled, true);
    assert.equal(client.hasAttention(), true); assert.equal(client.getRecords().length, 2);
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )


def test_editing_options_or_cancelling_after_review_sends_nothing(node):
    run_node(
        node,
        LIBRARY
        + """
(async () => {
    replies.push(contextReply('shuffle', {}, 'first'));
    const edited = await review(() => first.quick.click());
    first.params.querySelectorAll()[0].value = '5'; first.form.input();
    runReview(); await edited.done;
    assert.equal(calls.length, 1); assert.equal(first.quick.textContent, 'Shuffle now');
    assert.match(element('live-operation-review').dataset.notice, /options changed/);
    replies.push(contextReply('shuffle', {}, 'first'));
    const cancelled = await review(() => first.quick.click());
    await window.AppPanels.requestClose('escape'); await cancelled.done;
    assert.equal(calls.length, 2); assert.equal(client.getRecords().length, 0);
    assert.deepEqual(JSON.parse(calls[1].options.body).form, {algorithm: 'BasicShuffle', keep_first: '5'});
})().catch(e => {console.error(e); process.exitCode = 1;});
""",
    )
