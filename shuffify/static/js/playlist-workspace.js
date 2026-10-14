/**
 * Occurrences and tool context for the playlist workspace.
 * An occurrence is a row's client-only identity, so duplicated songs stay distinct while
 * the saved, locked and sourced order remains the plain ordered URI list (uris(list)).
 *
 * occurrences(uris) -> [{key, uri}], keys o1, o2, ... never reused on the page.
 * remap(previous, nextUris) -> occurrences: each URI keeps its earlier keys in order (a
 *   queue per URI), so only occurrences beyond its earlier count get new keys. Preview
 *   results, draft loads and adopted Spotify orders all go through it.
 * move(list, fromIndex, toIndex): 0-based, toIndex being the item's index afterwards (as
 *   SortableJS's oldIndex/newIndex), clamped to the list; an unknown fromIndex moves nothing.
 * moveKey(list, key, position): 1-based and clamped to 1..N. position is a number
 *   (input.valueAsNumber): NaN, a string or an unknown key moves nothing.
 * remove(list, key) drops exactly that occurrence. uris(list) -> the ordered URIs.
 *   move, moveKey and remove are pure and always return a new list.
 * announceMove(title, position) says "Moved <title> to position <N>." through
 *   AppPanels.announce when it is present, and returns the words.
 * focusOccurrence(container, key, selector) focuses the control matching selector in the
 *   row [data-occurrence="<key>"]: true when focus landed there, false when there is no
 *   such row or control or it cannot take focus (the caller picks a fallback).
 * rememberView(name, element) / restoreView(name, element) keep a tool body's scroll
 *   position by name; restoreView returns whether it had one to apply.
 * The draft footer's space is AppPanels.reserveFooter's (app-panels.js), shared with the
 * setup page's action footer.
 * Callers apply a move by reordering the DOM directly, without animation, whether or not
 * reduced motion is requested; then focusOccurrence keeps focus on the moved occurrence.
 */
(function () {
    'use strict';
    var issued = 0;
    var views = new Map();

    function newKey() {
        issued += 1;
        return 'o' + issued;
    }
    function copy(list) {
        return Array.from(list || []);
    }
    function isNumber(value) {
        return typeof value === 'number' && !Number.isNaN(value);
    }
    function indexOfKey(list, key) {
        return copy(list).findIndex(function (item) { return item.key === key; });
    }
    function occurrences(uris) {
        return copy(uris).map(function (uri) { return {key: newKey(), uri: uri}; });
    }
    function remap(previous, nextUris) {
        var queues = new Map();
        copy(previous).forEach(function (item) {
            if (!queues.has(item.uri)) queues.set(item.uri, []);
            queues.get(item.uri).push(item.key);
        });
        return copy(nextUris).map(function (uri) {
            var queue = queues.get(uri);
            return {key: queue && queue.length ? queue.shift() : newKey(), uri: uri};
        });
    }
    function move(list, fromIndex, toIndex) {
        var next = copy(list);
        if (!Number.isInteger(fromIndex) || fromIndex < 0 || fromIndex >= next.length || !isNumber(toIndex)) {
            return next;
        }
        var index = Math.min(Math.max(toIndex, 0), next.length - 1);
        next.splice(index, 0, next.splice(fromIndex, 1)[0]);
        return next;
    }
    function moveKey(list, key, position) {
        if (!isNumber(position)) return copy(list);
        return move(list, indexOfKey(list, key), position - 1);
    }
    function remove(list, key) {
        var next = copy(list), index = indexOfKey(next, key);
        if (index >= 0) next.splice(index, 1);
        return next;
    }
    function uris(list) {
        return copy(list).map(function (item) { return item.uri; });
    }
    function announceMove(title, position) {
        var message = 'Moved ' + (title || 'track') + ' to position ' + position + '.';
        if (window.AppPanels && window.AppPanels.announce) window.AppPanels.announce(message);
        return message;
    }
    function focusOccurrence(container, key, selector) {
        var row = container && container.querySelector('[data-occurrence="' + key + '"]');
        var control = row && selector ? row.querySelector(selector) : null;
        if (!control) return false;
        control.focus();
        return document.activeElement === control;
    }
    function rememberView(name, element) {
        if (element) views.set(name, element.scrollTop);
    }
    function restoreView(name, element) {
        if (!element || !views.has(name)) return false;
        element.scrollTop = views.get(name);
        return true;
    }
    window.PlaylistWorkspace = {
        occurrences: occurrences, remap: remap, move: move, moveKey: moveKey, remove: remove, uris: uris,
        announceMove: announceMove, focusOccurrence: focusOccurrence,
        rememberView: rememberView, restoreView: restoreView
    };
})();
