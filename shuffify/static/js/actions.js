/**
 * Delegated event dispatch: elements declare handlers in data-action-* attributes.
 *
 * A CSP nonce authorises an *element*, not an *attribute*, so under a
 * `script-src` without 'unsafe-inline' every `onclick="..."` attribute is
 * refused by the browser. Elements therefore declare intent with a data
 * attribute and the behaviour lives in a registered function:
 *
 *     <button data-action-click="workshop.undoPreview">Undo</button>
 *
 *     registerActions({
 *         'workshop.undoPreview': function (el, event) { undoPreview(); }
 *     });
 *
 * Listeners are attached once, at the document, and resolve the target with
 * `closest()`. That is deliberate: markup injected later via innerHTML is
 * matched by the same listener, so dynamically rendered controls need no
 * rebinding step after every render.
 *
 * Handlers are invoked as `fn.call(el, el, event)`, so `this` and the first
 * argument are both the element carrying the attribute. Returning false calls
 * preventDefault(), matching inline-handler semantics.
 */
(function () {
    "use strict";

    var registry = Object.create(null);

    /**
     * Register handler functions by key. Safe to call repeatedly.
     *
     * A duplicate key is always a wiring bug, never intentional: the later
     * registration wins and the earlier handler stops working, which is the
     * same silent-failure shape this whole module exists to remove. Keys are
     * spread across page templates, partials and macros, so a collision is
     * reported loudly rather than letting one control quietly stop responding.
     *
     * Last-write-wins follows plain object-assignment semantics; the error is
     * what makes a collision findable.
     */
    window.registerActions = function (map) {
        Object.keys(map).forEach(function (key) {
            if (key in registry && window.console && console.error) {
                console.error(
                    'registerActions: duplicate action key "' + key + '". The ' +
                    "earlier handler has been replaced and will no longer run. " +
                    "Action keys must be unique across every template, partial " +
                    "and macro on the page."
                );
            }
            registry[key] = map[key];
        });
    };

    // focus/blur do not bubble, so they are listened for in the capture phase;
    // the rest bubble to the document normally.
    var EVENTS = {
        click: false,
        change: false,
        input: false,
        submit: false,
        keydown: false,
        keyup: false,
        focus: true,
        blur: true,
    };

    Object.keys(EVENTS).forEach(function (type) {
        var attr = "data-action-" + type;
        document.addEventListener(
            type,
            function (event) {
                var start = event.target;
                if (!start || typeof start.closest !== "function") {
                    return;
                }
                var el = start.closest("[" + attr + "]");
                if (!el) {
                    return;
                }
                var key = el.getAttribute(attr);
                var fn = registry[key];
                if (typeof fn !== "function") {
                    // A missing handler is a wiring bug, not a user-facing
                    // error: log it rather than throwing inside dispatch.
                    if (window.console && console.warn) {
                        console.warn("No registered action for " + attr + '="' + key + '"');
                    }
                    return;
                }
                if (fn.call(el, el, event) === false) {
                    event.preventDefault();
                }
            },
            EVENTS[type]
        );
    });
})();
