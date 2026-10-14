/**
 * Shared task visibility and navigation. Consumers retain values/save state.
 * open(id, {trigger, modal, beforeClose, onClose, nest, inheritTrigger}) and
 * requestClose(reason) return Promise<boolean>. beforeClose(reason) may return/resolve false.
 * A modal suspends one context panel, restoring its focus when dismissed.
 * nest: a contextual task opened from inside another (a setup step) hides it; closing the
 * new task for any reason but 'replace' brings it back with focus on the control that
 * opened the step, and opening an unrelated task drops the whole chain.
 * inheritTrigger: a task that replaces another (a destination chosen from an index task)
 * returns focus to the replaced task's trigger.
 * A trigger is never the page itself: focus falls to <body> when a pressed control disables
 * or hides itself, so such a task returns focus to its region or the page's fallback.
 * Body scroll positions survive suspension and responsive layout clamping. A task that
 * covers the primary content (a full-width task, or any task below the workspace breakpoint)
 * keeps the page's place over it, and the page goes back there when the primary content
 * returns. A task set aside comes back where the page stood in it, its focus on screen.
 * IMPORTANT: beforeClose may await an external decision, but must not call or
 * await AppPanels transitions. They reject while the hook is pending. Return
 * false and let the outer action/navigation guard open a confirmation instead;
 * allow its 'suspend' reason when that confirmation should replace the task.
 * AppNavigation.addGuard(fn) returns an unsubscribe function; request(detail)
 * returns boolean/Promise. Consumers navigate only after approval. Native
 * same-tab links/logout forms emit cancellable `app:before-navigate` as well.
 * AppNavigation.addUnloadCheck(fn) returns an unsubscribe function. The page's one
 * beforeunload listener asks every check fn(approved): true warns. approved is true only
 * for the navigation the guards just approved (a programmatic one, or a native link or
 * logout resumed after an asynchronous approval), so pages do not warn about it again.
 * AppPanels.reserveFooter(footer, target) keeps a sticky action footer's measured height in
 * --app-footer-space on target (assets/design/components/action-footer.css), and returns a
 * function that stops following it.
 */
(function () {
    'use strict';
    var active = null, suspended = null, parents = [];
    // Where the page stood over the primary content when a task covered it, or null.
    var primaryTop = null;
    var pendingTransition = Promise.resolve();
    var closeHookPending = false;
    var navigationGuards = [], navigationPending = false;
    // unloadApproved: the guards approved the navigation now leaving; the next unload uses it up.
    var unloadChecks = [], unloadApproved = false;
    var resubmitting = new WeakSet();
    var focusSelector = 'a[href], button, input, select, textarea, [tabindex], [contenteditable="true"]';

    function visible(element) {
        return element && element.isConnected &&
            !element.closest('[hidden], [inert]') && element.getClientRects().length > 0;
    }
    function focus(element, preventScroll) {
        if (!visible(element)) return false;
        element.focus({preventScroll: Boolean(preventScroll)});
        return document.activeElement === element;
    }
    // A place focus can return to: never the page itself.
    function returnable(element) {
        return element && element !== document.body && element !== document.documentElement ? element : null;
    }
    function pageTop() { return window.scrollY || 0; }
    function scrollPage(top) {
        if (typeof window.scrollTo === 'function') window.scrollTo({top: top, left: 0, behavior: 'instant'});
    }
    // The primary content shows again: the page goes back to where it stood over it. True
    // when it did.
    function returnPrimary() {
        if (primaryTop === null) return false;
        scrollPage(primaryTop);
        primaryTop = null;
        return true;
    }
    function focusHeading(state, preventScroll) {
        var target = state.element.querySelector('[data-panel-heading]') || state.element;
        if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
        focus(target, preventScroll);
    }
    function focusFallback(inPlace) {
        var fallback = document.querySelector('[data-panel-focus-fallback]') ||
            document.querySelector('[data-panel-primary]') || document.getElementById('main-content');
        if (fallback && !fallback.hasAttribute('tabindex')) fallback.setAttribute('tabindex', '-1');
        if (inPlace) focusInPlace(fallback);
        else focus(fallback);
    }
    // Scrolls an element into view only when none of it is on screen.
    function keepOnScreen(element) {
        var box = element.getBoundingClientRect();
        if ((box.bottom <= 0 || box.top >= window.innerHeight) && element.scrollIntoView) {
            element.scrollIntoView({block: 'nearest'});
        }
    }
    // Focus that must not move a page just put back where it stood: it scrolls only to an
    // element that is off-screen.
    function focusInPlace(element) {
        if (!focus(element, true)) return false;
        keepOnScreen(element);
        return true;
    }
    // A [data-panel-return] region (a card or section) takes focus itself, never one of
    // its controls: the first control there may write (a toggle, a create button).
    function focusRegion(region, inPlace) {
        if (!region || !visible(region)) return false;
        if (!region.hasAttribute('tabindex')) region.setAttribute('tabindex', '-1');
        return inPlace ? focusInPlace(region) : focus(region);
    }
    // Focus the element, or, when it is hidden or gone from view, its closest region.
    // True when focus moved there. inPlace: the page was just put back, so focus keeps it.
    function focusNear(element, inPlace) {
        if (!element) return false;
        return (inPlace ? focusInPlace(element) : focus(element)) ||
            focusRegion(element.isConnected && element.closest('[data-panel-return]'), inPlace);
    }
    // The page's one polite announcer (live_operation_announcer, rendered by base.html).
    // A live region exposes only its last text in a task, so messages from one task (a run
    // starting and the page status it changes) are joined and written once, after it. The
    // same words again are still news (a second unconfirmed change), so a repeat is varied
    // invisibly to be read again.
    var announcing = [];
    function announce(message) {
        if (!message) return;
        if (!announcing.length) {
            if (typeof setTimeout === 'function') setTimeout(flushAnnouncements, 0);
            else Promise.resolve().then(flushAnnouncements);
        }
        if (announcing.indexOf(message) < 0) announcing.push(message);
    }
    function flushAnnouncements() {
        var region = document.getElementById('live-operation-announcer');
        var message = announcing.join(' ');
        announcing = [];
        if (region && message) region.textContent = region.textContent === message ? message + '\u00a0' : message;
    }
    function scrollGeometry(body) {
        return [window.innerWidth, window.innerHeight, body.clientHeight, body.scrollHeight].join(':');
    }
    function rememberScroll(state) {
        var saved = state.bodyScroll;
        if (!saved || !visible(saved.element) || saved.geometry !== scrollGeometry(saved.element)) return;
        var current = saved.element.scrollTop;
        // Ignore the clamped offset written by restoration; it is not a new
        // preferred position. A user/browser scroll at stable geometry is.
        if (current !== saved.appliedTop) {
            saved.preferredTop = current;
            saved.appliedTop = current;
        }
    }
    function restoreScroll(state) {
        var saved = state.bodyScroll;
        if (!saved || !visible(saved.element)) return;
        var body = saved.element;
        body.scrollTop = Math.min(saved.preferredTop, Math.max(0, body.scrollHeight - body.clientHeight));
        saved.appliedTop = body.scrollTop;
        saved.geometry = scrollGeometry(body);
    }
    function restoreBackground(state) {
        state.backgroundStates.forEach(function (saved) {
            saved.element.inert = saved.inert;
            saved.element.hidden = saved.hidden;
        });
        state.backgroundStates = [];
        if (state.workspace) state.workspace.classList.remove('sh-workspace-panel-open', 'sh-panel-narrow');
    }
    function disableBackground(state, element, hide) {
        state.backgroundStates.push({element: element, inert: element.inert, hidden: element.hidden});
        element.inert = true;
        if (hide) element.hidden = true;
    }
    function syncLayout(state, restoring) {
        if (!restoring) rememberScroll(state);
        var top = pageTop(), coversPrimary = false;
        restoreBackground(state);
        if (state.modal) {
            // Inert siblings at every level, never the dialog's own ancestor.
            // App-wide live regions ([data-panel-live]) stay heard while a dialog is open.
            var branch = state.element;
            while (branch.parentElement && branch !== document.body) {
                Array.from(branch.parentElement.children).forEach(function (sibling) {
                    if (sibling !== branch && !sibling.hasAttribute('data-panel-live')) {
                        disableBackground(state, sibling, false);
                    }
                });
                branch = branch.parentElement;
            }
        } else if (state.workspace) {
            state.workspace.classList.add('sh-workspace-panel-open');
            var breakpoint = parseFloat(window.getComputedStyle(document.documentElement)
                .getPropertyValue('--sh-breakpoint-workspace'));
            // A full-width task ([data-panel-full], a multi-step setup) takes the content area at
            // every width, as every task does below the workspace breakpoint.
            var full = state.element.hasAttribute('data-panel-full');
            if (full || (Number.isFinite(breakpoint) && window.innerWidth < breakpoint)) {
                state.workspace.classList.add('sh-panel-narrow');
                state.workspace.querySelectorAll('[data-panel-primary]').forEach(function (primary) {
                    if (primary.contains(state.element)) return;
                    disableBackground(state, primary, true);
                    coversPrimary = true;
                });
            }
        }
        // The page's place over the primary content is kept while a task covers it (a task
        // opening, or a resize below the breakpoint) and goes back when it shows beside one.
        if (!state.modal) {
            state.covers = coversPrimary;
            if (coversPrimary && primaryTop === null) primaryTop = top;
            else if (!coversPrimary) returnPrimary();
        }
        restoreScroll(state);
        if (!visible(document.activeElement)) focusHeading(state, true);
    }
    function show(state, restoreFocus) {
        active = state;
        state.element.hidden = false;
        state.element.inert = false;
        if (!state.bodyScroll) {
            var body = state.element.querySelector('.sh-panel-body');
            if (body) state.bodyScroll = {element: body, preferredTop: body.scrollTop,
                appliedTop: body.scrollTop, geometry: scrollGeometry(body)};
        }
        state.element.setAttribute('role', state.modal ? 'dialog' : 'region');
        state.element.classList.toggle('sh-panel-modal', state.modal);
        if (state.modal) state.element.setAttribute('aria-modal', 'true');
        else state.element.removeAttribute('aria-modal');
        syncLayout(state, true);
        // A task set aside comes back where the page stood in it, when it is laid out as it was
        // then. Across a resize meanwhile, the page keeps the place its layout gave it.
        if (state.aside) {
            if (state.aside.covers === state.covers) scrollPage(state.aside.top);
            delete state.aside;
        }
        if (!restoreFocus || !focus(restoreFocus, true)) focusHeading(state, Boolean(restoreFocus));
        restoreScroll(state);
        // Its focus shows on screen even when the page's layout changed meanwhile.
        var restored = restoreFocus && returnable(document.activeElement);
        if (restored) keepOnScreen(restored);
    }
    function hide(state) {
        rememberScroll(state);
        restoreBackground(state);
        state.element.hidden = true;
        state.element.inert = true;
    }
    async function mayClose(state, reason) {
        if (!state.beforeClose) return true;
        closeHookPending = true;
        try {
            return (await state.beforeClose(reason)) !== false;
        } finally {
            closeHookPending = false;
        }
    }
    async function close(reason, returnFocus) {
        if (!active) return true;
        var previous = active;
        if (!await mayClose(previous, reason)) return false;
        // A replaced step closes every task it was nested in, so each of them is asked too.
        if (reason === 'replace' && !suspended) {
            for (var index = parents.length - 1; index >= 0; index--) {
                if (!await mayClose(parents[index], 'replace')) return false;
            }
        }
        hide(previous);
        active = null;
        if (suspended) {
            var restored = suspended;
            suspended = null;
            show(restored, restored.returnFocus);
        } else if (parents.length && reason !== 'replace') {
            // A nested step closed: its task comes back, on the control that opened the step.
            var parent = parents.pop();
            show(parent, parent.returnFocus);
        } else {
            // Replaced: every task the step was nested in closes with it.
            while (parents.length) {
                var dropped = parents.pop();
                if (dropped.onClose) dropped.onClose(reason);
            }
            // The task replacing this one may cover the primary content again.
            var placed = reason !== 'replace' && returnPrimary();
            if (returnFocus && !focusNear(previous.trigger, placed) &&
                    !focusRegion(previous.returnRegion, placed)) {
                // A trigger rebuilt while the task was open is found by the region it was in.
                focusFallback(placed);
            }
        }
        if (previous.onClose) previous.onClose(reason);
        return true;
    }
    // Hides the active task under a modal or a nested step, keeping the page's place in it and
    // the control to restore focus to: the one that opened the new task, else the focused one.
    async function setActiveAside(into, trigger) {
        if (!await mayClose(active, 'suspend')) return false;
        active.returnFocus = trigger || returnable(document.activeElement);
        active.aside = {top: pageTop(), covers: active.covers};
        if (into === parents) parents.push(active);
        else suspended = active;
        hide(active);
        active = null;
        return true;
    }
    function suspendActive(trigger) { return setActiveAside(null, trigger); }
    // A sticky action footer's space: its height to the next whole pixel (a fractional footer
    // never leaves a sliver of the last row covered), plus the bottom safe-area inset. It follows
    // the footer's size through ResizeObserver, else measures it once.
    function reserveFooter(footer, target) {
        if (!footer || !target) return function () {};
        var written = null;
        function measure() {
            var height = Math.ceil(footer.getBoundingClientRect().height);
            // An unchanged height writes nothing, so the page is not restyled for it.
            if (height === written) return;
            written = height;
            // A measured length, not a design token, so it sits outside the --sh- namespace.
            target.style.setProperty('--app-footer-space', 'calc(' + height + 'px + max(0px, env(safe-area-inset-bottom)))');
        }
        // The observer's first delivery takes the first measurement, so loading forces no layout.
        if (typeof window.ResizeObserver !== 'function') {
            measure();
            return function () {};
        }
        var observer = new window.ResizeObserver(measure);
        observer.observe(footer);
        return function () { observer.disconnect(); };
    }
    function enqueue(callback) {
        if (closeHookPending) {
            return Promise.reject(new Error(
                'AppPanels transitions cannot run while beforeClose is pending. ' +
                'Open confirmations from the outer action or navigation guard.'
            ));
        }
        var result = pendingTransition.then(callback);
        // A consumer exception must not permanently disable later actions.
        pendingTransition = result.catch(function () {});
        return result;
    }
    window.AppPanels = {
        open: function (id, options) {
            options = options || {};
            var trigger = returnable(options.trigger) || returnable(document.activeElement);
            return enqueue(async function () {
                var element = document.getElementById(id);
                if (!element || !element.hasAttribute('data-app-panel')) return false;
                if (active && active.element === element) return true;
                var modal = options.modal === undefined ?
                    element.hasAttribute('data-panel-modal') : Boolean(options.modal);
                var replaced = null;
                if (active && modal && !active.modal) {
                    if (!await suspendActive(trigger)) return false;
                } else if (active && options.nest && !modal && !active.modal) {
                    if (!await setActiveAside(parents, trigger)) return false;
                } else if (active) {
                    replaced = active;
                    if (!await close('replace', false)) return false;
                    // Closing a modal may have restored its contextual task, on its own focus.
                    if (active) {
                        if (modal) {
                            if (!await suspendActive(null)) return false;
                        } else if (!await close('replace', false)) return false;
                    }
                }
                if (options.inheritTrigger && replaced) trigger = replaced.trigger;
                show({element: element, trigger: trigger, modal: modal,
                    returnRegion: trigger && trigger.closest ? trigger.closest('[data-panel-return]') : null,
                    workspace: element.closest('[data-panel-workspace]'),
                    beforeClose: options.beforeClose, onClose: options.onClose, backgroundStates: []});
                return true;
            });
        },
        requestClose: function (reason) {
            return enqueue(function () { return close(reason || 'close', true); });
        },
        focusNear: focusNear,
        announce: announce,
        reserveFooter: reserveFooter,
        // Opens a modal and resolves once with the reason it closed for (a
        // [data-panel-close="reason"] control, 'escape', ...), or null when it
        // could not open or the same dialog is already asking.
        ask: function (id, options) {
            options = options || {};
            if (asking[id]) return Promise.resolve(null);
            asking[id] = true;
            return new Promise(function (resolve) {
                var settled = false;
                function settle(reason) {
                    if (settled) return;
                    settled = true;
                    delete asking[id];
                    resolve(reason);
                }
                window.AppPanels.open(id, {modal: true, trigger: options.trigger, onClose: settle})
                    .then(function (opened) { if (!opened) settle(null); },
                        function (error) { report(error); settle(null); });
            });
        }
    };
    var asking = {};
    function report(error) {
        if (window.console) window.console.error('Shared task action failed:', error);
    }
    // One shared modal confirmation for destructive choices; native dialogs are not used.
    // It resolves true only for its confirm control; Escape or any other close is no,
    // and so is a second request while one is open.
    window.AppConfirm = {
        ask: function (options) {
            options = options || {};
            if (!document.getElementById('app-confirm') || asking['app-confirm']) return Promise.resolve(false);
            document.getElementById('app-confirm-heading').textContent = options.title || 'Are you sure?';
            document.getElementById('app-confirm-text').textContent = options.message || '';
            var ok = document.getElementById('app-confirm-ok');
            ok.textContent = options.confirmLabel || 'Confirm';
            ok.className = options.tone === 'default' ? 'sh-button' : 'sh-button-danger';
            document.getElementById('app-confirm-cancel').textContent = options.cancelLabel || 'Cancel';
            return window.AppPanels.ask('app-confirm', {trigger: options.trigger})
                .then(function (reason) { return reason === 'confirm'; });
        }
    };
    // The seconds a refusal asks the page to wait: its Retry-After header, else its body's
    // retry_after; null when neither is a positive number.
    function retryAfter(reply, data) {
        var header = null;
        try {
            header = reply.headers && typeof reply.headers.get === 'function' ? reply.headers.get('Retry-After') : null;
        } catch (_) {
            header = null;
        }
        return [header, data.retry_after].map(function (value) { return Number.parseInt(value, 10); })
            .find(function (value) { return Number.isFinite(value) && value > 0; }) || null;
    }
    // One request through the page's fetch (and its CSRF wrapper). ok: the server confirmed
    // it; answered: it replied, with its reason when it refused; status: its HTTP status (0
    // without a reply); retryAfter: retryAfter() above. Without a readable reply the outcome
    // is unknown, and callers say so instead of "failed".
    // options are fetch's, plus json (a body sent as JSON, keeping X-Requested-With) and
    // timeoutMs (a time limit: the request is then aborted, and an abort is no reply).
    window.AppRequest = {
        send: function (url, options) {
            var init = Object.assign({headers: {'X-Requested-With': 'XMLHttpRequest'}}, options);
            var json = init.json, limit = init.timeoutMs, controller = null, timer = null;
            delete init.json;
            delete init.timeoutMs;
            if (json !== undefined) {
                init.headers = Object.assign({'X-Requested-With': 'XMLHttpRequest'}, init.headers,
                    {'Content-Type': 'application/json'});
                init.body = JSON.stringify(json);
            }
            if (limit && typeof AbortController === 'function') {
                controller = new AbortController();
                init.signal = controller.signal;
            }
            var noReply = {reply: null, data: null};
            var exchange = Promise.resolve().then(function () {
                return window.fetch(url, init);
            }).then(function (reply) {
                return Promise.resolve().then(function () { return reply.json(); })
                    .catch(function () { return null; })
                    .then(function (data) { return {reply: reply, data: data}; });
            }, function () { return noReply; });
            if (limit) {
                exchange = Promise.race([exchange, new Promise(function (resolve) {
                    timer = setTimeout(function () {
                        if (controller) controller.abort();
                        resolve(noReply);
                    }, limit);
                })]);
            }
            return exchange.then(function (result) {
                if (timer !== null) clearTimeout(timer);
                var reply = result.reply, data = result.data;
                var answered = Boolean(reply && data && typeof data === 'object');
                return {ok: answered && reply.ok && data.success === true, answered: answered,
                    status: reply ? Number(reply.status) || 0 : 0,
                    retryAfter: reply ? retryAfter(reply, answered ? data : {}) : null,
                    data: answered ? data : {}};
            });
        }
    };
    document.addEventListener('click', function (event) {
        var button = event.target.closest && event.target.closest('[data-panel-close]');
        if (!button || !active || !active.element.contains(button)) return;
        event.preventDefault();
        window.AppPanels.requestClose(button.getAttribute('data-panel-close') || 'close').catch(report);
    });
    document.addEventListener('keydown', function (event) {
        if (!active || event.defaultPrevented) return;
        if (event.key === 'Escape' && event.target.tagName !== 'SELECT' &&
                (active.modal || active.element.contains(event.target))) {
            event.preventDefault();
            window.AppPanels.requestClose('escape').catch(report);
        } else if (event.key === 'Tab' && active.modal) {
            var controls = Array.from(active.element.querySelectorAll(focusSelector)).filter(function (element) {
                return element.tabIndex >= 0 && !element.disabled && visible(element);
            });
            var first = controls[0], last = controls[controls.length - 1], current = document.activeElement;
            if (!first) {
                event.preventDefault();
                focusHeading(active);
            } else if (event.shiftKey && (current === first || !controls.includes(current))) {
                event.preventDefault();
                focus(last);
            } else if (!event.shiftKey && (current === last || !controls.includes(current))) {
                event.preventDefault();
                focus(first);
            }
        }
    });
    document.addEventListener('focusin', function (event) {
        if (active && active.modal && !active.element.contains(event.target)) focusHeading(active);
    });
    document.addEventListener('scroll', function (event) {
        if (active && active.bodyScroll && event.target === active.bodyScroll.element) rememberScroll(active);
    }, true);
    window.addEventListener('resize', function () {
        if (active) syncLayout(active);
    });

    function requestNavigation(detail) {
        var notification = new CustomEvent('app:before-navigate', {cancelable: true, detail: detail});
        if (!document.dispatchEvent(notification)) return false;
        var result = true;
        navigationGuards.slice().forEach(function (guard) {
            function check(allowed) { return allowed === false ? false : guard(detail); }
            result = result && typeof result.then === 'function' ? result.then(check) : check(result);
        });
        return result && typeof result.then === 'function' ?
            result.then(function (allowed) { return allowed !== false; }) : result !== false;
    }
    // Programmatic navigation asks the same guards as a native link, one at a time.
    function navigate(detail, proceed) {
        if (navigationPending) return Promise.resolve(false);
        navigationPending = true;
        return Promise.resolve().then(function () { return requestNavigation(detail); }).then(function (allowed) {
            if (allowed === false) return false;
            unloadApproved = true;
            proceed();
            return true;
        }).catch(function (error) {
            report(error);
            return false;
        }).finally(function () { navigationPending = false; });
    }
    window.AppNavigation = {
        addGuard: function (guard) {
            if (typeof guard !== 'function') throw new TypeError('Navigation guard must be a function');
            if (!navigationGuards.includes(guard)) navigationGuards.push(guard);
            return function () {
                navigationGuards = navigationGuards.filter(function (registered) { return registered !== guard; });
            };
        },
        addUnloadCheck: function (check) {
            if (typeof check !== 'function') throw new TypeError('Unload check must be a function');
            if (!unloadChecks.includes(check)) unloadChecks.push(check);
            return function () {
                unloadChecks = unloadChecks.filter(function (registered) { return registered !== check; });
            };
        },
        request: requestNavigation,
        assign: function (url, element) {
            return navigate({kind: 'link', url: url, element: element}, function () { window.location.assign(url); });
        },
        reload: function () {
            return navigate({kind: 'reload', url: window.location.href}, function () { window.location.reload(); });
        },
        // A change the page cannot show yet: name it in the page's persistent reload notice.
        showReloadNotice: function (id, reason) {
            var notice = document.getElementById(id);
            if (!notice) return;
            var reasons = reloadReasons[id] || (reloadReasons[id] = []);
            if (reason && reasons.indexOf(reason) < 0) reasons.push(reason);
            var message = notice.querySelector('[data-reload-notice-text]');
            if (message) message.textContent = reasons.join(' ');
            notice.hidden = false;
            // A region revealed already filled is not reliably read, so every reason is
            // announced, a repeated one too: it reports another change not shown yet.
            announce(reason);
        },
        // Reload through the guards; a declined reload leaves the notice instead.
        reloadOrNotice: function (id, reason) {
            return window.AppNavigation.reload().then(function (reloaded) {
                if (!reloaded) window.AppNavigation.showReloadNotice(id, reason);
                return reloaded;
            });
        }
    };
    var reloadReasons = {};
    document.addEventListener('click', function (event) {
        var button = event.target.closest && event.target.closest('[data-app-reload]');
        if (!button) return;
        event.preventDefault();
        window.AppNavigation.reload();
    });
    // The browser's own leave warning, one for the page: every check is asked, and any that would
    // lose something warns. A check that throws warns too (the page cannot tell what leaving
    // loses).
    window.addEventListener('beforeunload', function (event) {
        var approved = unloadApproved, warn = false;
        unloadApproved = false;
        unloadChecks.slice().forEach(function (check) {
            try {
                if (check(approved)) warn = true;
            } catch (error) {
                report(error);
                warn = true;
            }
        });
        if (!warn) return;
        event.preventDefault();
        event.returnValue = '';
    });
    function guardNative(event, detail, resume, beforeProceed) {
        if (navigationPending) {
            event.preventDefault();
            return;
        }
        try {
            var result = requestNavigation(detail);
            if (result && typeof result.then === 'function') {
                event.preventDefault();
                navigationPending = true;
                Promise.resolve(result).then(function (allowed) {
                    if (allowed === false) return;
                    if (beforeProceed) beforeProceed();
                    unloadApproved = true;
                    resume();
                }).catch(report).finally(function () { navigationPending = false; });
            } else if (result === false) event.preventDefault();
            else if (beforeProceed) beforeProceed();
        } catch (error) {
            event.preventDefault();
            report(error);
        }
    }
    document.addEventListener('click', function (event) {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
                event.shiftKey || event.altKey) return;
        var link = event.target.closest && event.target.closest('a[href]');
        if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
        var url = new URL(link.href, window.location.href);
        if (!/^https?:$/.test(url.protocol)) return;
        var current = new URL(window.location.href);
        if (url.origin === current.origin && url.pathname === current.pathname &&
                url.search === current.search && (url.hash || link.getAttribute('href') === '#')) return;
        guardNative(event, {kind: 'link', url: url.href, element: link, event: event}, function () {
            window.location.assign(url.href);
        });
    });
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (event.defaultPrevented || !form.matches('form[data-app-logout]')) return;
        if (resubmitting.has(form)) {
            resubmitting.delete(form);
            return;
        }
        guardNative(event, {kind: 'logout', url: form.action, element: form, event: event}, function () {
            resubmitting.add(form);
            form.requestSubmit(event.submitter || undefined);
            resubmitting.delete(form);
        }, function () {
            // Shared setup can leave a same-tab routine continuation behind while
            // the user visits Settings. Clear it only after logout guards accept.
            try { window.sessionStorage.removeItem('shuffify:routine-continuation:v1'); }
            catch (error) { /* Storage may be disabled; logout still proceeds. */ }
        });
    });
})();
