/**
 * Delegated event dispatch for markup that used to carry inline on* handlers.
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
 * argument are both the element carrying the attribute -- which lets a body
 * copied verbatim out of an old inline attribute keep working, including the
 * `this` that `onclick="return confirmNavigation(this)"` relied on. Returning
 * false calls preventDefault(), matching inline-handler semantics.
 */
(function () {
    "use strict";

    var registry = Object.create(null);

    /** Register handler functions by key. Safe to call repeatedly. */
    window.registerActions = function (map) {
        Object.keys(map).forEach(function (key) {
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

    // Handlers for the shared chrome (navbar + settings sidebar). These
    // partials appear on every page, so they are wired here rather than in any
    // single page's script block.
    window.registerActions({
        "ui.settings-sidebar-open": function () { toggleSettingsSidebar(); },
        "ui.settings-sidebar-close": function () { closeSettingsSidebar(); },
        "ui.settings-sidebar-save": function () { saveSettingsSidebar(); },
        "ui.confirm-logout": function () {
            return confirm("Are you sure you want to log out?");
        },
    });

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
