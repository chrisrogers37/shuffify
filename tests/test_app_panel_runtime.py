"""Run the shipped task/navigation controller; browser checks cover real layout.

This DOM fixture checks transition contracts, not browser rendering or inert's
native implementation. It deliberately has no task state/network implementation.
"""

import shutil
from pathlib import Path

import pytest

from tests.node_runtime import run_scenario
from tests.test_library_page_runtime import NATIVE_DIALOG, _code

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is needed for the shipped JavaScript runtime checks")
    return executable


def run_node(node, body):
    run_scenario(node, FIXTURE + "\n(async () => {\n" + body
                 + "\n})().catch(error => { console.error(error); process.exitCode = 1; });", REPO_ROOT)


FIXTURE = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('shuffify/static/js/app-panels.js', 'utf8');
// globals: what the script's own realm has besides the page (timers for a request's time limit).
function fixture(globals = {}) {
    let document, window;
    const scrolled = [];
    class Element {
        constructor(tag, attrs = {}) {
            this.tagName = tag.toUpperCase(); this.attrs = {...attrs};
            this.children = []; this.parentElement = null; this.isConnected = true;
            this.hidden = 'hidden' in attrs; this.inert = 'inert' in attrs;
            this.disabled = false; this.tabIndex = ['BUTTON', 'INPUT', 'A'].includes(this.tagName) ? 0 : -1;
            this.value = ''; this._scrollTop = 0;
            const classes = new Set();
            this.classList = {add: (...names) => names.forEach(n => classes.add(n)),
                remove: (...names) => names.forEach(n => classes.delete(n)),
                contains: name => classes.has(name),
                toggle: (name, on) => on ? classes.add(name) : classes.delete(name)};
        }
        append(...children) {
            children.forEach(child => { child.parentElement = this; this.children.push(child); });
            return this;
        }
        hasAttribute(name) { return name in this.attrs; }
        getAttribute(name) { return this.attrs[name] ?? null; }
        setAttribute(name, value) {
            this.attrs[name] = String(value);
            if (name === 'tabindex') this.tabIndex = Number(value);
        }
        removeAttribute(name) { delete this.attrs[name]; }
        matches(selector) {
            return selector.split(',').some(part => {
                part = part.trim();
                if (part.startsWith('#')) return this.attrs.id === part.slice(1);
                if (part.startsWith('.')) return this.classList.contains(part.slice(1));
                if (part === '[hidden]') return this.hidden;
                if (part === '[inert]') return this.inert;
                const match = part.match(/^(\w+)?(?:\[([^=\]]+)(?:="([^"]*)")?\])?$/);
                return match && (!match[1] || this.tagName === match[1].toUpperCase()) &&
                    (!match[2] || this.hasAttribute(match[2])) &&
                    (match[3] === undefined || this.attrs[match[2]] === match[3]);
            });
        }
        closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
        contains(child) { return child === this || this.children.some(el => el.contains(child)); }
        querySelectorAll(selector) {
            return this.children.flatMap(child => [
                ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        getClientRects() { return this.isConnected && !this.closest('[hidden]') ? [{}] : []; }
        get clientHeight() {
            if (this.closest('[hidden]')) return 0;
            return window.innerWidth < 1024 && !this.closest('[data-panel-modal]') ? 1100 : window.innerHeight - 356;
        }
        get scrollHeight() { return this.closest('[hidden]') ? 0 : 1100; }
        get scrollTop() {
            this._scrollTop = Math.min(this._scrollTop, Math.max(0, this.scrollHeight - this.clientHeight));
            return this._scrollTop;
        }
        set scrollTop(value) {
            this._scrollTop = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight));
        }
        scrollIntoView(options) { scrolled.push([this, options]); }
        // On screen unless a test moves it (box), as a browser's layout would.
        getBoundingClientRect() { return this.box || {top: 100, bottom: 140}; }
        focus(options = {}) {
            this.lastFocus = options;
            if (!this.closest('[hidden], [inert]')) {
                document.activeElement = this;
                const body = this.closest('.sh-panel-body');
                if (body && !options.preventScroll) body.scrollTop = 0;
            }
        }
    }
    const element = (tag, attrs) => new Element(tag, attrs);
    const body = element('body'), html = element('html').append(body);
    const nav = element('nav'), main = element('main', {id: 'main-content'}), footer = element('footer');
    const workspace = element('div', {'data-panel-workspace': ''});
    const primary = element('div', {'data-panel-primary': ''}), trigger = element('button');
    primary.append(trigger); workspace.append(primary); main.append(workspace); body.append(nav, main, footer);
    function panel(id, modal = false) {
        const el = element('section', {id, 'data-app-panel': '', hidden: '', inert: '',
            ...(modal ? {'data-panel-modal': ''} : {})});
        const heading = element('h2', {'data-panel-heading': '', tabindex: '-1'});
        const input = element('input'), close = element('button', {'data-panel-close': ''});
        const body = element('div'); body.classList.add('sh-panel-body'); body.append(input);
        el.append(heading, body, close); workspace.append(el);
        return {el, heading, input, close, body};
    }
    const first = panel('first'), second = panel('second'), modal = panel('modal', true);
    const listeners = {}, windowListeners = {}, navigated = [], errors = [];
    document = {body, documentElement: html, activeElement: trigger,
        getElementById: id => [html, ...html.querySelectorAll('[id]')].find(el => el.getAttribute('id') === id),
        querySelector: selector => html.querySelector(selector),
        addEventListener: (name, fn) => (listeners[name] ||= []).push(fn),
        dispatchEvent: event => {
            (listeners[event.type] || []).forEach(fn => fn(event));
            return !event.defaultPrevented;
        }};
    function event(type, target, options = {}) {
        return {type, target, button: 0, defaultPrevented: false,
            preventDefault() { this.defaultPrevented = true; }, ...options};
    }
    const location = {href: 'https://example.test/workshop/a', assign: url => navigated.push(url)};
    // The page's scroll: tests move it as the browser would (a user scroll, a clamp).
    window = {location, innerWidth: 1440, innerHeight: 900, scrollY: 0,
        scrollTo: options => { window.scrollY = options.top; },
        console: {error: (...args) => errors.push(args)},
        getComputedStyle: () => ({getPropertyValue: () => '1024px'}),
        addEventListener: (name, fn) => (windowListeners[name] ||= []).push(fn)};
    class CustomEvent {
        constructor(type, options) { Object.assign(this, event(type), options); }
    }
    vm.runInNewContext(source, Object.assign({window, document, URL, CustomEvent}, globals));
    function send(type, target, options) {
        const e = event(type, target, options); document.dispatchEvent(e); return e;
    }
    function resize(width, height = 900) {
        window.innerWidth = width; window.innerHeight = height;
        // Browser layout can clamp the real scroll body before resize delivery.
        for (const panel of [first, second, modal]) {
            panel.body.scrollTop = panel.body.scrollTop;
            send('scroll', panel.body);
        }
        (windowListeners.resize || []).forEach(fn => fn());
        for (const panel of [first, second, modal]) send('scroll', panel.body);
    }
    function scrollBody(panel, top) { panel.body.scrollTop = top; send('scroll', panel.body); }
    // The browser is about to unload the page: the event its listeners got.
    function unload() {
        const e = event('beforeunload', window);
        (windowListeners.beforeunload || []).forEach(fn => fn(e));
        return e;
    }
    return {window, document, element, body, nav, main, footer, workspace, primary, trigger,
        first, second, modal, send, resize, scrollBody, unload, navigated, errors, scrolled};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
"""


def test_context_panel_resizes_without_changing_values_or_focus(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
assert.equal(f.first.el.hidden, true);
assert.equal(await api.open('missing'), false);
assert.equal(await api.open('first', {trigger: f.trigger}), true);
assert.equal(f.first.el.getAttribute('role'), 'region');
assert.equal(f.first.el.hasAttribute('aria-modal'), false);
assert.equal(f.document.activeElement, f.first.heading);
assert.equal(f.primary.hidden, false);
f.first.input.value = 'keep these settings'; f.first.input.focus();
f.scrollBody(f.first, 180);
f.resize(375);
assert.equal(f.primary.hidden, true); assert.equal(f.primary.inert, true);
assert.equal(f.document.activeElement, f.first.input);
assert.equal(f.workspace.classList.contains('sh-panel-narrow'), true);
assert.equal(f.first.body.scrollTop, 0);
f.resize(1024);
f.resize(1440);
assert.equal(f.primary.hidden, false); assert.equal(f.primary.inert, false);
assert.equal(f.first.input.value, 'keep these settings');
assert.equal(f.first.body.scrollTop, 180);
f.resize(1440, 1350);
assert.equal(f.first.body.scrollTop, 106);
f.resize(1440, 900);
assert.equal(f.first.body.scrollTop, 180);
f.scrollBody(f.first, 90);
f.resize(375); f.resize(1440);
assert.equal(f.first.body.scrollTop, 90);
assert.equal(f.document.activeElement, f.first.input);
assert.equal(f.send('keydown', f.first.input, {key: 'Tab'}).defaultPrevented, false);
assert.equal(await api.requestClose(), true);
assert.equal(f.first.el.hidden, true); assert.equal(f.first.el.inert, true);
assert.equal(f.document.activeElement, f.trigger);
""")


def test_replacement_respects_async_veto_and_escape_returns_focus(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels, reasons = [], closed = [];
let allow = false;
await api.open('first', {trigger: f.trigger,
    beforeClose: async reason => { reasons.push(reason); return allow; },
    onClose: reason => closed.push(reason)});
assert.equal(await api.open('second'), false);
assert.equal(f.first.el.hidden, false); assert.equal(f.second.el.hidden, true);
assert.deepEqual(reasons, ['replace']); assert.deepEqual(closed, []);
allow = true;
assert.equal(await api.open('second', {trigger: f.trigger}), true);
assert.deepEqual(closed, ['replace']);
assert.equal(f.first.el.hidden, true); assert.equal(f.second.el.hidden, false);
assert.equal(f.send('keydown', f.second.heading, {key: 'ArrowLeft'}).defaultPrevented, false);
assert.equal(f.send('keydown', f.second.heading, {key: 'Escape'}).defaultPrevented, true);
await tick();
assert.equal(f.second.el.hidden, true); assert.equal(f.document.activeElement, f.trigger);
assert.equal(f.send('keydown', f.trigger, {key: 'Escape'}).defaultPrevented, false);
""")


def test_modal_suspends_context_and_restores_inert_and_focus(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels, reasons = [];
f.footer.inert = true;
await api.open('first', {beforeClose: reason => { reasons.push(reason); return true; }});
f.first.input.value = 'preserved'; f.first.input.focus();
f.scrollBody(f.first, 140);
await api.open('modal');
assert.equal(f.first.body.scrollTop, 0);
f.send('scroll', f.first.body);
assert.deepEqual(reasons, ['suspend']);
assert.equal(f.first.el.hidden, true); assert.equal(f.modal.el.hidden, false);
assert.equal(f.modal.el.getAttribute('role'), 'dialog');
assert.equal(f.modal.el.getAttribute('aria-modal'), 'true');
assert.equal(f.primary.inert, true); assert.equal(f.nav.inert, true);
f.modal.close.focus();
assert.equal(f.send('keydown', f.modal.close, {key: 'Tab'}).defaultPrevented, true);
assert.equal(f.document.activeElement, f.modal.input);
f.send('keydown', f.modal.input, {key: 'Tab', shiftKey: true});
assert.equal(f.document.activeElement, f.modal.close);
f.document.activeElement = f.trigger;
f.send('focusin', f.trigger);
assert.equal(f.document.activeElement, f.modal.heading);
f.send('click', f.modal.close); await tick();
assert.equal(f.modal.el.hidden, true); assert.equal(f.first.el.hidden, false);
assert.equal(f.document.activeElement, f.first.input);
assert.equal(f.first.input.value, 'preserved');
assert.equal(f.first.body.scrollTop, 140);
assert.equal(f.nav.inert, false); assert.equal(f.primary.inert, false);
assert.equal(f.footer.inert, true);
""")


def test_async_navigation_guard_runs_once_and_resumes_only_when_allowed(node):
    run_node(node, """
const f = fixture(), nav = f.window.AppNavigation, details = [];
const link = f.element('a', {href: '/settings'}); link.href = 'https://example.test/settings';
f.nav.append(link);
let resolve;
const remove = nav.addGuard(detail => { details.push(detail); return new Promise(done => { resolve = done; }); });
assert.equal(f.send('click', link).defaultPrevented, true);
assert.equal(f.send('click', link).defaultPrevented, true);
assert.equal(details.length, 1); assert.equal(details[0].kind, 'link');
resolve(false); await tick(); assert.deepEqual(f.navigated, []);
f.send('click', link); resolve(true); await tick();
assert.deepEqual(f.navigated, ['https://example.test/settings']);
remove();
assert.equal(f.send('click', link).defaultPrevented, false);
nav.addGuard(() => undefined);
assert.equal(nav.request({kind: 'link'}), true);
""")


def test_one_unload_listener_asks_every_check_and_spares_only_the_navigation_just_approved(node):
    run_node(node, """
const f = fixture(), nav = f.window.AppNavigation, asked = [];
const approvals = () => { const seen = asked.map(([, approved]) => approved); asked.length = 0; return seen; };
assert.throws(() => nav.addUnloadCheck('no'), {name: 'TypeError', message: 'Unload check must be a function'});
let draft = false, broken = false;
const first = nav.addUnloadCheck(approved => { asked.push(['first', approved]); return draft; });
nav.addUnloadCheck(approved => {
    asked.push(['second', approved]);
    if (broken) throw new Error('unreadable');
    return false;
});
// Nothing to lose: no warning. Something to lose: the browser's warning; every check is asked.
assert.equal(f.unload().defaultPrevented, false);
assert.deepEqual(approvals(), [false, false]);
draft = true;
const warned = f.unload();
assert.equal(warned.defaultPrevented, true); assert.equal(warned.returnValue, '');
assert.deepEqual(approvals(), [false, false]);
// A check that cannot tell warns, and is reported.
draft = false; broken = true;
assert.equal(f.unload().defaultPrevented, true); assert.equal(f.errors.length, 1);
broken = false; approvals();
// A programmatic navigation the guards approve is the next unload's, once; a declined one is not.
nav.addGuard(() => true);
assert.equal(await nav.assign('https://example.test/settings'), true);
f.unload(); assert.deepEqual(approvals(), [true, true]);
f.unload(); assert.deepEqual(approvals(), [false, false]);
const refuse = nav.addGuard(() => false);
assert.equal(await nav.reload(), false);
f.unload(); assert.deepEqual(approvals(), [false, false]);
refuse();
// A native link or logout resumed after a guard's later approval is approved too.
const wait = nav.addGuard(() => Promise.resolve(true));
const link = f.element('a', {href: '/settings'}); link.href = 'https://example.test/settings';
f.nav.append(link);
assert.equal(f.send('click', link).defaultPrevented, true); await tick();
assert.equal(f.navigated.at(-1), 'https://example.test/settings');
f.unload(); assert.deepEqual(approvals(), [true, true]);
const form = f.element('form', {'data-app-logout': ''}); form.action = 'https://example.test/logout';
let submitted = 0;
form.requestSubmit = () => { if (!f.send('submit', form).defaultPrevented) submitted++; };
f.main.append(form);
assert.equal(f.send('submit', form).defaultPrevented, true); await tick();
assert.equal(submitted, 1);
f.unload(); assert.deepEqual(approvals(), [true, true]);
wait();
// Approved at once, the browser follows the link itself: nothing was waiting to be approved.
assert.equal(f.send('click', link).defaultPrevented, false);
f.unload(); assert.deepEqual(approvals(), [false, false]);
// Unsubscribing stops asking that check.
first();
f.unload(); assert.deepEqual(asked.map(([name]) => name), ['second']);
""")


def test_programmatic_navigation_shares_the_guards_and_one_pending_request(node):
    run_node(node, """
const f = fixture(), nav = f.window.AppNavigation, details = [];
let reloads = 0, resolve;
f.window.location.reload = () => { reloads++; };
nav.addGuard(detail => { details.push(detail); return new Promise(done => { resolve = done; }); });
const first = nav.assign('https://example.test/workshop/b', f.trigger);
// One navigation at a time: another programmatic request and a native link both wait.
assert.equal(await nav.reload(), false);
const link = f.element('a', {href: '/settings'}); link.href = 'https://example.test/settings';
f.nav.append(link);
assert.equal(f.send('click', link).defaultPrevented, true);
await tick();
assert.equal(details.length, 1); assert.equal(details[0].kind, 'link');
assert.equal(details[0].element, f.trigger);
resolve(false);
assert.equal(await first, false); assert.deepEqual(f.navigated, []);
const second = nav.reload(); await tick(); resolve(true);
assert.equal(await second, true); assert.equal(reloads, 1);
assert.equal(details[1].kind, 'reload'); assert.equal(details[1].url, 'https://example.test/workshop/a');
const third = nav.assign('https://example.test/workshop/c'); await tick(); resolve(true);
assert.equal(await third, true); assert.deepEqual(f.navigated, ['https://example.test/workshop/c']);
""")


def test_app_confirm_resolves_once_and_any_other_close_means_no(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels, confirm = f.window.AppConfirm;
const panel = f.element('section', {id: 'app-confirm', 'data-app-panel': '', 'data-panel-modal': '', hidden: '',
    inert: ''});
const heading = f.element('h2', {id: 'app-confirm-heading', 'data-panel-heading': '', tabindex: '-1'});
const text = f.element('p', {id: 'app-confirm-text'});
const ok = f.element('button', {id: 'app-confirm-ok', 'data-panel-close': 'confirm'});
const cancel = f.element('button', {id: 'app-confirm-cancel', 'data-panel-close': 'cancel'});
panel.append(heading, text, ok, cancel); f.body.append(panel);
// The caller's copy and tone; confirm resolves true once and returns focus.
let decision = confirm.ask({title: 'Remove archive pairing?', message: 'Restorations stay.', confirmLabel: 'Remove',
    trigger: f.trigger});
await tick();
assert.equal(panel.hidden, false); assert.equal(heading.textContent, 'Remove archive pairing?');
assert.equal(text.textContent, 'Restorations stay.');
assert.equal(ok.textContent, 'Remove'); assert.equal(ok.className, 'sh-button-danger');
assert.equal(cancel.textContent, 'Cancel');
// A second request while one is open is refused, and leaves the open question's words alone.
assert.equal(await confirm.ask({title: 'Again?', message: 'Other words.', confirmLabel: 'Other'}), false);
assert.equal(heading.textContent, 'Remove archive pairing?'); assert.equal(text.textContent, 'Restorations stay.');
assert.equal(ok.textContent, 'Remove');
f.send('click', ok); assert.equal(await decision, true); await tick();
assert.equal(panel.hidden, true); assert.equal(f.document.activeElement, f.trigger);
// Cancel and Escape both mean no.
decision = confirm.ask({title: 'Delete?', tone: 'default', trigger: f.trigger}); await tick();
assert.equal(ok.className, 'sh-button');
f.send('click', cancel); assert.equal(await decision, false); await tick();
decision = confirm.ask({title: 'Delete?', trigger: f.trigger}); await tick();
f.send('keydown', heading, {key: 'Escape'}); assert.equal(await decision, false); await tick();
// An open contextual task is suspended for the decision and restored after it.
await api.open('first', {trigger: f.trigger});
decision = confirm.ask({title: 'Delete?', trigger: f.first.input}); await tick();
assert.equal(panel.hidden, false);
f.send('click', ok); assert.equal(await decision, true); await tick();
assert.equal(f.first.el.hidden, false); assert.equal(panel.hidden, true);
// Without the shared panel there is nothing to confirm.
panel.attrs.id = 'gone';
assert.equal(await confirm.ask({title: 'Delete?'}), false);
""")


def test_ask_resolves_a_modal_choice_once_and_live_regions_stay_heard(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
const live = f.element('div', {id: 'announcer', 'data-panel-live': ''}); f.body.append(live);
const choice = f.element('button', {'data-panel-close': 'leave'}); f.modal.el.append(choice);
const decision = api.ask('modal', {trigger: f.trigger}); await tick();
// The dialog inerts the page but never an app-wide live region.
assert.equal(f.nav.inert, true); assert.equal(live.inert, false);
assert.equal(await api.ask('modal'), null);
f.send('click', choice);
assert.equal(await decision, 'leave'); await tick();
assert.equal(f.document.activeElement, f.trigger);
const escaped = api.ask('modal', {trigger: f.trigger}); await tick();
f.send('keydown', f.modal.heading, {key: 'Escape'});
assert.equal(await escaped, 'escape');
assert.equal(await api.ask('missing'), null);
""")


def test_a_declined_reload_keeps_one_notice_naming_every_unshown_change(node):
    run_node(node, """
const f = fixture(), nav = f.window.AppNavigation;
let reloads = 0; f.window.location.reload = () => { reloads++; };
// A region revealed already filled is not reliably read, so each new reason is announced.
const said = [], announcer = f.element('div', {id: 'live-operation-announcer', 'data-panel-live': ''});
Object.defineProperty(announcer, 'textContent',
    {get() { return said.at(-1) || ''; }, set(value) { said.push(value); }});
f.body.append(announcer);
const notice = f.element('div', {id: 'page-reload-notice', hidden: ''});
const message = f.element('p', {'data-reload-notice-text': ''});
const button = f.element('button', {'data-app-reload': ''});
notice.append(message, button); f.main.append(notice);
let allow = false; nav.addGuard(() => allow);
assert.equal(await nav.reloadOrNotice('page-reload-notice', 'First change is saved.'), false);
assert.equal(notice.hidden, false); assert.equal(message.textContent, 'First change is saved.');
await tick();
nav.showReloadNotice('page-reload-notice', 'Second change is saved.'); await tick();
nav.showReloadNotice('page-reload-notice', 'First change is saved.'); await tick();
assert.equal(message.textContent, 'First change is saved. Second change is saved.');
// Each report is read, a repeated reason too: it is another change not shown yet.
assert.deepEqual(said, ['First change is saved.', 'Second change is saved.', 'First change is saved.']);
// The same words twice in a row are varied invisibly, so they are read again.
f.window.AppPanels.announce('First change is saved.'); await tick();
assert.equal(said.at(-1), 'First change is saved.\u00a0');
// A live region exposes only its last text in a task, so one task's messages are written
// once, joined, after it (a run starting and the page status it changes are both heard).
f.window.AppPanels.announce('Running: Shuffle now, Mix.'); f.window.AppPanels.announce('Busy — running.');
assert.equal(said.at(-1), 'First change is saved.\u00a0');
await tick();
assert.equal(said.at(-1), 'Running: Shuffle now, Mix. Busy — running.');
// The same words twice in one task are said once.
f.window.AppPanels.announce('Saved.'); f.window.AppPanels.announce('Saved.'); await tick();
assert.equal(said.at(-1), 'Saved.');
assert.equal(reloads, 0);
// The notice's own control reloads through the same guard.
allow = true; f.send('click', button); await tick(); await tick();
assert.equal(reloads, 1);
""")


def test_focus_returns_near_a_trigger_that_left_the_view(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
// A card (a [data-panel-return] region) whose trigger is hidden while its results are open:
// the card itself takes focus, never its first control, which may write (a favorite toggle).
const card = f.element('div', {'data-panel-return': ''});
const star = f.element('button'), trigger = f.element('button');
card.append(star, trigger); f.primary.append(card);
assert.equal(await api.open('first', {trigger}), true);
trigger.hidden = true; trigger.attrs.hidden = '';
assert.equal(await api.requestClose(), true);
assert.equal(f.document.activeElement, card); assert.equal(card.getAttribute('tabindex'), '-1');
// A trigger rebuilt while the task was open is found by the region it was in.
const list = f.element('section', {'data-panel-return': ''}), old = f.element('button');
list.append(old); f.primary.append(list);
await api.open('first', {trigger: old});
list.children = []; old.isConnected = false; old.parentElement = null;
list.append(f.element('button'));
await api.requestClose();
assert.equal(f.document.activeElement, list);
// A visible trigger is focused itself; one with no region falls back to the primary content.
assert.equal(api.focusNear(star), true); assert.equal(f.document.activeElement, star);
const loose = f.element('button'); f.primary.append(loose);
await api.open('first', {trigger: loose});
loose.hidden = true; loose.attrs.hidden = '';
await api.requestClose();
assert.equal(f.document.activeElement, f.primary);
""")


def test_a_full_width_task_takes_the_content_area_at_every_width(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
// A multi-step setup ([data-panel-full]) hides and inerts the primary content even at 1440px.
f.first.el.attrs['data-panel-full'] = '';
assert.equal(f.window.innerWidth, 1440);
assert.equal(await api.open('first', {trigger: f.trigger}), true);
assert.equal(f.primary.hidden, true); assert.equal(f.primary.inert, true);
assert.equal(f.workspace.classList.contains('sh-panel-narrow'), true);
assert.equal(f.document.activeElement, f.first.heading);
assert.equal(await api.requestClose(), true);
assert.equal(f.primary.hidden, false); assert.equal(f.primary.inert, false);
assert.equal(f.document.activeElement, f.trigger);
// An ordinary contextual task still sits beside the primary content at desktop widths.
assert.equal(await api.open('second', {trigger: f.trigger}), true);
assert.equal(f.primary.hidden, false); assert.equal(f.workspace.classList.contains('sh-panel-narrow'), false);
""")


def test_a_nested_step_returns_to_its_task_and_a_replacement_drops_the_chain(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
const inner = f.element('section', {id: 'inner', 'data-app-panel': '', hidden: '', inert: ''});
const innerHeading = f.element('h2', {'data-panel-heading': '', tabindex: '-1'});
inner.append(innerHeading); f.workspace.append(inner);
const opener = f.first.input, parentClosed = [];
assert.equal(await api.open('first', {trigger: f.trigger, onClose: reason => parentClosed.push(reason)}), true);
opener.focus();
// A setup step opened from inside a task hides it ...
assert.equal(await api.open('inner', {trigger: opener, nest: true}), true);
assert.equal(f.first.el.hidden, true); assert.equal(inner.hidden, false);
// ... and closing the step brings the task back, on the control that opened the step.
assert.equal(await api.requestClose(), true);
assert.equal(f.first.el.hidden, false); assert.equal(inner.hidden, true);
assert.equal(f.document.activeElement, opener); assert.deepEqual(parentClosed, []);
// Escape does the same.
await api.open('inner', {trigger: opener, nest: true});
f.send('keydown', innerHeading, {key: 'Escape'}); await tick(); await tick();
assert.equal(f.first.el.hidden, false); assert.equal(f.document.activeElement, opener);
// A modal over the step restores the step, and closing the step then restores the task.
await api.open('inner', {trigger: opener, nest: true});
assert.equal(await api.open('modal', {trigger: innerHeading}), true);
assert.equal(inner.hidden, true); assert.equal(f.first.el.hidden, true);
await api.requestClose();
assert.equal(inner.hidden, false); assert.equal(f.first.el.hidden, true);
await api.requestClose();
assert.equal(f.first.el.hidden, false); assert.equal(f.document.activeElement, opener);
// An unrelated task replaces the step and every task it was nested in.
await api.open('inner', {trigger: opener, nest: true});
assert.equal(await api.open('second', {trigger: f.trigger}), true);
assert.equal(inner.hidden, true); assert.equal(f.first.el.hidden, true); assert.equal(f.second.el.hidden, false);
assert.deepEqual(parentClosed, ['replace']);
await api.requestClose();
assert.equal(f.document.activeElement, f.trigger);
// A destination chosen from an index task returns focus to the index's own trigger.
await api.open('first', {trigger: f.trigger});
assert.equal(await api.open('second', {trigger: f.first.input, inheritTrigger: true}), true);
assert.equal(f.first.el.hidden, true);
await api.requestClose();
assert.equal(f.first.el.hidden, true); assert.equal(f.document.activeElement, f.trigger);
""")


def test_the_page_returns_to_the_list_when_a_task_that_covered_it_closes(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
// Beside the primary content the page is left where the user put it.
f.window.scrollY = 700;
await api.open('first', {trigger: f.trigger});
f.window.scrollY = 760;
await api.requestClose();
assert.equal(f.window.scrollY, 760);
// Below the workspace breakpoint a task covers the list and scrolls with the page; closing
// it brings the list back at its place, with focus on the trigger.
f.resize(375);
f.window.scrollY = 900;
await api.open('first', {trigger: f.trigger});
assert.equal(f.primary.hidden, true);
f.window.scrollY = 40;
await api.requestClose();
assert.equal(f.primary.hidden, false); assert.equal(f.window.scrollY, 900);
// Focus returns to the trigger without moving the page again (a sticky footer button counts as
// covered by the page's scroll padding, so a scrolling focus would move it).
assert.equal(f.document.activeElement, f.trigger); assert.equal(f.trigger.lastFocus.preventScroll, true);
assert.equal(f.scrolled.length, 0);
// A trigger off-screen at the list's place is scrolled into view.
await api.open('first', {trigger: f.trigger});
f.trigger.box = {top: 2000, bottom: 2040};
await api.requestClose();
assert.equal(f.document.activeElement, f.trigger); assert.equal(f.scrolled.length, 1);
assert.equal(f.scrolled[0][0], f.trigger); assert.equal(f.scrolled[0][1].block, 'nearest');
delete f.trigger.box;
// A trigger gone by then hands focus to its region, which keeps the list's place too.
const section = f.element('section', {'data-panel-return': ''}), gone = f.element('button');
section.append(gone); f.primary.append(section);
await api.open('first', {trigger: gone});
gone.hidden = true; gone.attrs.hidden = '';
await api.requestClose();
assert.equal(f.document.activeElement, section); assert.equal(section.lastFocus.preventScroll, true);
// One with no region falls back to the primary content, still in place.
const loose = f.element('button'); f.primary.append(loose);
await api.open('first', {trigger: loose});
loose.hidden = true; loose.attrs.hidden = '';
await api.requestClose();
assert.equal(f.document.activeElement, f.primary); assert.equal(f.primary.lastFocus.preventScroll, true);
// A task replaced by another keeps the list's place for the last one to close, with no
// jump to the list in between.
f.window.scrollY = 1200;
await api.open('first', {trigger: f.trigger});
f.window.scrollY = 10;
await api.open('second', {trigger: f.first.input, inheritTrigger: true});
assert.equal(f.window.scrollY, 10);
await api.requestClose();
assert.equal(f.window.scrollY, 1200); assert.equal(f.document.activeElement, f.trigger);
// A resize shows the list beside the task again at its place; covering it again keeps the
// place the user scrolled to meanwhile.
f.window.scrollY = 300;
await api.open('first', {trigger: f.trigger});
f.window.scrollY = 0;
f.resize(1440);
assert.equal(f.primary.hidden, false); assert.equal(f.window.scrollY, 300);
f.window.scrollY = 450;
f.resize(375);
assert.equal(f.primary.hidden, true);
f.window.scrollY = 0;
await api.requestClose();
assert.equal(f.window.scrollY, 450);
""")


def test_a_task_set_aside_comes_back_where_the_page_stood_with_its_focus_in_view(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
const inner = f.element('section', {id: 'inner', 'data-app-panel': '', 'data-panel-full': '',
    hidden: '', inert: ''});
const innerHeading = f.element('h2', {'data-panel-heading': '', tabindex: '-1'}), remove = f.element('button');
inner.append(innerHeading, remove); f.workspace.append(inner);
f.resize(375);
f.window.scrollY = 900;
await api.open('first', {trigger: f.trigger});
// Scrolled into the task, the user opens its setup step by a click that leaves focus on
// the page (Safari, Firefox on macOS): the step still returns to that control.
f.window.scrollY = 250;
const opener = f.first.input;
f.document.activeElement = f.body;
assert.equal(await api.open('inner', {trigger: opener, nest: true}), true);
f.window.scrollY = 2900;
f.send('keydown', innerHeading, {key: 'Escape'}); await tick(); await tick();
assert.equal(f.first.el.hidden, false); assert.equal(f.document.activeElement, opener);
assert.equal(f.window.scrollY, 250); assert.equal(f.scrolled.length, 0);
// When the task's layout changed meanwhile and the opener is off-screen, it is scrolled to.
await api.open('inner', {trigger: opener, nest: true});
opener.box = {top: -2850, bottom: -2806};
f.send('keydown', innerHeading, {key: 'Escape'}); await tick(); await tick();
assert.equal(f.document.activeElement, opener);
assert.equal(f.scrolled.at(-1)[0], opener); assert.equal(f.scrolled.at(-1)[1].block, 'nearest');
delete opener.box;
// A confirmation over the step leaves a shorter page behind it; cancelling returns to the
// place in the step, on the control that asked.
await api.open('inner', {trigger: opener, nest: true});
f.window.scrollY = 3700;
f.document.activeElement = f.body;
assert.equal(await api.open('modal', {trigger: remove}), true);
f.window.scrollY = 0; remove.box = {top: 3000, bottom: 3044};
f.send('keydown', f.modal.heading, {key: 'Escape'}); await tick(); await tick();
assert.equal(inner.hidden, false); assert.equal(f.document.activeElement, remove);
assert.equal(f.window.scrollY, 3700);
assert.equal(f.scrolled.at(-1)[0], remove); assert.equal(f.scrolled.at(-1)[1].block, 'nearest');
// Closing the step and then its task shows the list at its place again.
await api.requestClose();
assert.equal(f.document.activeElement, opener);
await api.requestClose();
assert.equal(f.primary.hidden, false); assert.equal(f.window.scrollY, 900);
assert.equal(f.document.activeElement, f.trigger);
""")


def test_a_task_set_aside_across_a_resize_leaves_the_page_to_its_new_layout(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
// At 768 a task covers the list (at 3000) and scrolls with the page; a confirmation opens over
// it, and the window widens past the breakpoint (a tablet turned).
f.resize(768);
f.window.scrollY = 3000;
await api.open('first', {trigger: f.trigger});
f.window.scrollY = 1500;
await api.open('modal', {trigger: f.first.input});
f.resize(1440);
f.send('keydown', f.modal.heading, {key: 'Escape'}); await tick(); await tick();
// The task comes back beside the list, which is at its own place, not the task's.
assert.equal(f.first.el.hidden, false); assert.equal(f.primary.hidden, false);
assert.equal(f.window.scrollY, 3000); assert.equal(f.document.activeElement, f.first.input);
await api.requestClose();
// A nested step resized wide: its task comes back beside the list, which keeps the place the
// user read on to.
f.resize(768);
f.window.scrollY = 3000;
await api.open('first', {trigger: f.trigger});
f.window.scrollY = 1500;
await api.open('second', {trigger: f.first.input, nest: true});
f.resize(1440);
assert.equal(f.window.scrollY, 3000);
f.window.scrollY = 3200;
await api.requestClose();
assert.equal(f.first.el.hidden, false); assert.equal(f.window.scrollY, 3200);
assert.equal(f.document.activeElement, f.first.input);
""")


def test_the_page_itself_is_never_a_trigger(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
// A pressed control that disabled itself leaves focus on the page; a task opened then
// returns focus to the page's fallback, never to <body>.
f.document.activeElement = f.body;
assert.equal(await api.open('first'), true);
await api.requestClose();
assert.equal(f.document.activeElement, f.primary);
f.document.activeElement = f.body;
await api.open('first', {trigger: f.body});
await api.requestClose();
assert.equal(f.document.activeElement, f.primary);
// A modal over a task opened from the page restores the task on its own heading.
await api.open('first', {trigger: f.trigger});
f.document.activeElement = f.body;
await api.open('modal');
await api.requestClose();
assert.equal(f.first.el.hidden, false); assert.equal(f.document.activeElement, f.first.heading);
""")


def test_tasks_a_step_is_nested_in_are_asked_before_it_opens_or_is_replaced(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
const inner = f.element('section', {id: 'inner', 'data-app-panel': '', hidden: '', inert: ''});
inner.append(f.element('h2', {'data-panel-heading': '', tabindex: '-1'})); f.workspace.append(inner);
const verdicts = {suspend: false}, asked = [], closed = [];
await api.open('first', {trigger: f.trigger, onClose: reason => closed.push(reason),
    beforeClose: reason => { asked.push(reason); return verdicts[reason] ?? true; }});
f.first.input.focus();
// A task that cannot be set aside keeps its step from opening.
assert.equal(await api.open('inner', {trigger: f.first.input, nest: true}), false);
assert.equal(f.first.el.hidden, false); assert.equal(inner.hidden, true);
// One that cannot close keeps an unrelated task from replacing the step it holds.
verdicts.suspend = true; verdicts.replace = false;
assert.equal(await api.open('inner', {trigger: f.first.input, nest: true}), true);
assert.equal(await api.open('second', {trigger: f.trigger}), false);
assert.equal(inner.hidden, false); assert.equal(f.second.el.hidden, true);
assert.deepEqual(asked, ['suspend', 'suspend', 'replace']); assert.deepEqual(closed, []);
// The step still returns to its task, and a replacement it allows drops the chain.
await api.requestClose();
assert.equal(f.first.el.hidden, false); assert.equal(f.document.activeElement, f.first.input);
verdicts.replace = true;
await api.open('inner', {trigger: f.first.input, nest: true});
assert.equal(await api.open('second', {trigger: f.trigger}), true);
assert.deepEqual(closed, ['replace']); assert.equal(f.first.el.hidden, true);
""")


def test_a_question_replacing_a_question_keeps_the_task_focus_beneath(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
const other = f.element('section', {id: 'other', 'data-app-panel': '', 'data-panel-modal': '',
    hidden: '', inert: ''});
other.append(f.element('h2', {'data-panel-heading': '', tabindex: '-1'})); f.workspace.append(other);
await api.open('first', {trigger: f.trigger});
f.first.input.focus();
await api.open('modal', {trigger: f.first.input});
// A second question opened from inside the first replaces it; the task stays set aside.
assert.equal(await api.open('other', {trigger: f.modal.input}), true);
assert.equal(f.modal.el.hidden, true); assert.equal(f.first.el.hidden, true);
await api.requestClose();
assert.equal(f.first.el.hidden, false); assert.equal(f.document.activeElement, f.first.input);
""")


def test_no_template_or_shipped_script_opens_a_native_dialog():
    """Checks every template and static script, not a fixed list."""
    root = REPO_ROOT / "shuffify"
    paths = sorted([*root.glob("templates/**/*.html"), *root.glob("static/js/*.js")])
    assert len(paths) > 20
    offenders = [str(path.relative_to(REPO_ROOT)) for path in paths if NATIVE_DIALOG.search(_code(path.read_text()))]
    assert offenders == []


def test_request_send_separates_confirmed_refused_and_unknown_replies(node):
    run_node(node, """
const f = fixture(), request = f.window.AppRequest;
const replies = [
    {ok: true, status: 200, json: async () => ({success: true, value: 1})},
    {ok: false, status: 400, json: async () => ({success: false, message: 'No.'})},
    {ok: true, status: 200, json: async () => { throw new Error('not JSON'); }},
];
const sent = [];
f.window.fetch = async (url, options) => { sent.push({url, options}); const reply = replies.shift();
    if (!reply) throw new Error('offline'); return reply; };
const plain = value => JSON.parse(JSON.stringify(value));
assert.deepEqual(plain(await request.send('/a', {method: 'POST'})),
    {ok: true, answered: true, status: 200, retryAfter: null, data: {success: true, value: 1}});
assert.deepEqual(plain(await request.send('/b', {method: 'PUT'})),
    {ok: false, answered: true, status: 400, retryAfter: null, data: {success: false, message: 'No.'}});
assert.deepEqual(plain(await request.send('/c', {method: 'POST'})),
    {ok: false, answered: false, status: 200, retryAfter: null, data: {}});
assert.deepEqual(plain(await request.send('/d', {method: 'DELETE'})),
    {ok: false, answered: false, status: 0, retryAfter: null, data: {}});
assert.equal(sent[0].options.method, 'POST');
assert.equal(sent[0].options.headers['X-Requested-With'], 'XMLHttpRequest');
// Without a time limit nothing is aborted.
assert.equal('signal' in sent[0].options, false);
""")


def test_request_send_takes_json_and_a_time_limit_and_reports_status_and_wait(node):
    run_node(node, """
// The script's realm records its timers: each is cleared once its request settles.
const timers = new Map();
const f = fixture({AbortController,
    setTimeout: (callback, delay) => { const id = setTimeout(callback, delay); timers.set(id, delay); return id; },
    clearTimeout: id => { timers.delete(id); clearTimeout(id); }});
const request = f.window.AppRequest, sent = [];
let answer;
f.window.fetch = (url, options) => { sent.push({url, options}); return answer(options); };
const plain = value => JSON.parse(JSON.stringify(value));
const headers = values => ({get: name => values[name] ?? null});
const reply = (status, body, wait = {}) => async () => ({ok: status < 400, status, headers: headers(wait),
    json: async () => body});
// json: the body as JSON with its type, keeping X-Requested-With and any header given.
answer = reply(201, {success: true});
assert.deepEqual(plain(await request.send('/a', {method: 'POST', json: {name: 'Mix', count: 1}})),
    {ok: true, answered: true, status: 201, retryAfter: null, data: {success: true}});
assert.equal(sent[0].options.body, '{"name":"Mix","count":1}');
assert.deepEqual(plain(sent[0].options.headers),
    {'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/json'});
assert.equal('json' in sent[0].options, false);
// Without a time limit nothing is aborted.
assert.equal('signal' in sent[0].options, false);
await request.send('/b', {method: 'PUT', json: [], headers: {'X-Extra': '1'}});
assert.equal(sent[1].options.body, '[]');
assert.deepEqual(plain(sent[1].options.headers),
    {'X-Requested-With': 'XMLHttpRequest', 'X-Extra': '1', 'Content-Type': 'application/json'});
// retryAfter: the Retry-After header in seconds, else the body's retry_after, else null.
answer = reply(429, {success: false, retry_after: 5}, {'Retry-After': '30'});
assert.deepEqual(plain(await request.send('/c', {method: 'POST'})),
    {ok: false, answered: true, status: 429, retryAfter: 30, data: {success: false, retry_after: 5}});
answer = reply(429, {success: false, retry_after: 5}, {'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'});
assert.equal((await request.send('/d', {method: 'POST'})).retryAfter, 5);
answer = reply(429, {success: false, retry_after: 4}, {'Retry-After': '-3'});
assert.equal((await request.send('/e', {method: 'POST'})).retryAfter, 4);
answer = reply(429, {success: false, retry_after: -2}, {'Retry-After': '0'});
assert.equal((await request.send('/e', {method: 'POST'})).retryAfter, null);
// A reply whose body cannot be read keeps its status and its header's wait.
answer = async () => ({ok: false, status: 503, headers: headers({'Retry-After': '7'}),
    json: async () => { throw new Error('not JSON'); }});
assert.deepEqual(plain(await request.send('/f', {method: 'POST'})),
    {ok: false, answered: false, status: 503, retryAfter: 7, data: {}});
// timeoutMs: past the limit the request is aborted, and an abort is no reply.
let signal;
answer = options => { signal = options.signal; return new Promise(() => {}); };
assert.deepEqual(plain(await request.send('/g', {method: 'POST', timeoutMs: 20})),
    {ok: false, answered: false, status: 0, retryAfter: null, data: {}});
assert.equal(signal.aborted, true);
// A reply still being read at the limit is no reply either.
answer = async options => { signal = options.signal;
    return {ok: true, status: 200, headers: headers({}), json: () => new Promise(() => {})}; };
assert.deepEqual(plain(await request.send('/h', {timeoutMs: 20})),
    {ok: false, answered: false, status: 0, retryAfter: null, data: {}});
assert.equal(signal.aborted, true);
// A reply inside the limit stands and aborts nothing.
answer = async options => { signal = options.signal; return reply(200, {success: true})(); };
assert.equal((await request.send('/i', {timeoutMs: 60000})).ok, true);
assert.equal(signal.aborted, false); assert.equal('timeoutMs' in sent.at(-1).options, false);
assert.deepEqual([...timers.values()], []);
""")


# A footer whose height the test sets, a target that keeps what is written to its style (and
# accepts nothing but custom properties), and a ResizeObserver whose deliveries the test drives.
FOOTER = """
const space = height => 'calc(' + height + 'px + max(0px, env(safe-area-inset-bottom)))';
function box(height = 0) {
    const node = {height, written: {}, getBoundingClientRect() { return {top: 0, height: node.height}; }};
    node.style = Object.freeze({setProperty: (name, value) => { node.written[name] = value; }});
    return node;
}
const observers = [];
class ResizeObserver {
    constructor(callback) { this.callback = callback; this.observed = []; observers.push(this); }
    observe(target) { this.observed.push(target); }
    disconnect() { this.observed = []; }
    deliver() { if (this.observed.length) this.callback(this.observed.map(target => ({target})), this); }
}
"""


def test_reserve_footer_follows_the_footer_height_until_stopped(node):
    run_node(node, FOOTER + """
const f = fixture(), api = f.window.AppPanels;
f.window.ResizeObserver = ResizeObserver;
const footer = box(56.4), page = box();
let reads = 0;
const measured = footer.getBoundingClientRect;
footer.getBoundingClientRect = () => { reads += 1; return measured.call(footer); };
const stop = api.reserveFooter(footer, page);
// The observer's first delivery takes the first measurement: loading forces no layout.
assert.equal(reads, 0); assert.deepEqual(page.written, {});
assert.equal(observers.length, 1); assert.deepEqual(observers[0].observed, [footer]);
observers[0].deliver();
// Rounded up, with the inset added in CSS; nothing is written to the footer.
assert.deepEqual(page.written, {'--app-footer-space': space(57)});
assert.deepEqual(footer.written, {});
// An unchanged height writes nothing.
let writes = 0;
const write = page.style.setProperty;
page.style = Object.freeze({setProperty: (name, value) => { writes += 1; write(name, value); }});
observers[0].deliver();
assert.equal(writes, 0);
// It re-measures when the footer's box changes (wrapped text, hidden).
footer.height = 104; observers[0].deliver();
assert.equal(page.written['--app-footer-space'], space(104));
footer.height = 0; observers[0].deliver();
assert.equal(page.written['--app-footer-space'], space(0));
// Stopping disconnects, so later size changes are not followed.
stop();
assert.deepEqual(observers[0].observed, []);
footer.height = 72; observers[0].deliver();
assert.equal(page.written['--app-footer-space'], space(0));
""")


def test_reserve_footer_measures_once_without_resize_observer(node):
    run_node(node, FOOTER + """
const f = fixture(), api = f.window.AppPanels;
const footer = box(48), page = box();
const stop = api.reserveFooter(footer, page);
assert.equal(page.written['--app-footer-space'], space(48));
footer.height = 96;
assert.equal(typeof stop, 'function'); stop();
assert.equal(page.written['--app-footer-space'], space(48));
// A page without the footer or the target reserves nothing and still gets a stop function.
for (const [watched, target] of [[null, page], [footer, null]]) {
    const none = api.reserveFooter(watched, target);
    assert.equal(typeof none, 'function'); none();
}
assert.equal(page.written['--app-footer-space'], space(48));
""")


def test_native_link_exceptions_and_cancelled_event_do_not_prompt(node):
    run_node(node, """
const f = fixture(); let prompts = 0;
f.window.AppNavigation.addGuard(() => { prompts++; return false; });
const link = f.element('a', {href: '/settings'}); link.href = 'https://example.test/settings';
f.nav.append(link);
for (const opts of [{ctrlKey: true}, {metaKey: true}, {button: 1}, {shiftKey: true}]) {
    assert.equal(f.send('click', link, opts).defaultPrevented, false);
}
link.target = '_blank'; f.send('click', link); link.target = '';
link.attrs.download = ''; f.send('click', link); delete link.attrs.download;
link.href = 'https://example.test/workshop/a#tracks'; f.send('click', link);
assert.equal(prompts, 0);
link.href = 'https://example.test/settings';
f.document.addEventListener('app:before-navigate', event => event.preventDefault());
assert.equal(f.send('click', link).defaultPrevented, true);
assert.equal(prompts, 0);
""")


def test_logout_stays_native_or_resubmits_once_after_async_approval(node):
    run_node(node, """
const f = fixture(), form = f.element('form', {'data-app-logout': ''});
form.action = 'https://example.test/logout'; f.main.append(form);
assert.equal(f.send('submit', form).defaultPrevented, false);
let guards = 0, submitted = 0;
form.requestSubmit = () => { const event = f.send('submit', form); if (!event.defaultPrevented) submitted++; };
f.window.AppNavigation.addGuard(async detail => { assert.equal(detail.kind, 'logout'); guards++; return true; });
assert.equal(f.send('submit', form).defaultPrevented, true); await tick();
assert.equal(guards, 1); assert.equal(submitted, 1);
assert.deepEqual(f.navigated, []);
""")


def test_confirmed_logout_clears_only_routine_continuation_on_any_page(node):
    run_node(node, """
const f = fixture(), key = 'shuffify:routine-continuation:v1';
const records = new Map([[key, '{"targetId":"a"}'], ['unrelated', 'keep']]);
f.window.location.href = 'https://example.test/settings';
f.window.sessionStorage = {removeItem: name => records.delete(name)};
const form = f.element('form', {'data-app-logout': ''});
form.action = 'https://example.test/logout'; f.main.append(form);
assert.equal(f.send('submit', form).defaultPrevented, false);
assert.equal(records.has(key), false);
assert.equal(records.get('unrelated'), 'keep');
""")


def test_logout_retains_continuation_until_all_async_guards_accept(node):
    run_node(node, """
const f = fixture(), key = 'shuffify:routine-continuation:v1';
const records = new Map([[key, 'local choices']]), removed = [];
f.window.sessionStorage = {removeItem: name => { removed.push(name); records.delete(name); }};
const form = f.element('form', {'data-app-logout': ''});
form.action = 'https://example.test/logout'; f.main.append(form);
let submitted = 0, approve;
form.requestSubmit = () => { if (!f.send('submit', form).defaultPrevented) submitted++; };
f.window.AppNavigation.addGuard(() => new Promise(resolve => { approve = resolve; }));
assert.equal(f.send('submit', form).defaultPrevented, true);
assert.equal(records.get(key), 'local choices');
approve(false); await tick();
assert.equal(records.get(key), 'local choices'); assert.equal(submitted, 0);
assert.equal(f.send('submit', form).defaultPrevented, true);
assert.equal(records.get(key), 'local choices');
approve(true); await tick();
assert.equal(records.has(key), false); assert.deepEqual(removed, [key]);
assert.equal(submitted, 1);
""")


def test_refused_logout_and_ordinary_navigation_keep_continuation(node):
    run_node(node, """
const f = fixture(), removed = [];
f.window.sessionStorage = {removeItem: name => removed.push(name)};
const form = f.element('form', {'data-app-logout': ''});
form.action = 'https://example.test/logout'; f.main.append(form);
f.window.AppNavigation.addGuard(detail => detail.kind !== 'logout');
assert.equal(f.send('submit', form).defaultPrevented, true);
assert.equal(await f.window.AppNavigation.assign('/settings'), true);
assert.deepEqual(removed, []);
""")


def test_blocked_session_storage_does_not_prevent_logout(node):
    run_node(node, """
const f = fixture(), form = f.element('form', {'data-app-logout': ''});
form.action = 'https://example.test/logout'; f.main.append(form);
Object.defineProperty(f.window, 'sessionStorage', {get() { throw new Error('Storage disabled'); }});
assert.equal(f.send('submit', form).defaultPrevented, false);
assert.equal(f.errors.length, 0);
""")


def test_nonmodal_escape_leaves_primary_editor_alone(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
await api.open('first');
f.trigger.focus();
assert.equal(f.send('keydown', f.trigger, {key: 'Escape'}).defaultPrevented, false);
await tick();
assert.equal(f.first.el.hidden, false);
assert.equal(f.document.activeElement, f.trigger);
f.first.input.focus();
assert.equal(f.send('keydown', f.first.input, {key: 'Escape'}).defaultPrevented, true);
await tick();
assert.equal(f.first.el.hidden, true);
await api.open('modal');
assert.equal(f.send('keydown', f.trigger, {key: 'Escape'}).defaultPrevented, true);
await tick();
assert.equal(f.modal.el.hidden, true);
""")


@pytest.mark.parametrize("nested", ["api.open('modal')", "api.requestClose()"])
def test_reentrant_close_hook_rejects_and_controller_recovers(node, nested):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
let reenter = true;
await api.open('first', {beforeClose: async () => {
    if (reenter) await NESTED_CALL;
    return true;
}});
let timeout;
try {
    await assert.rejects(Promise.race([
        api.requestClose(),
        new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('deadlocked')), 1000); })
    ]), /AppPanels transitions cannot run while beforeClose is pending/);
} finally { clearTimeout(timeout); }
assert.equal(f.first.el.hidden, false);
assert.equal(f.modal.el.hidden, true);
reenter = false;
assert.equal(await api.requestClose(), true);
assert.equal(await api.open('modal'), true);
assert.equal(f.modal.el.hidden, false);
""".replace("NESTED_CALL", nested))


def test_external_close_decision_can_be_awaited_without_losing_task(node):
    run_node(node, """
const f = fixture(), api = f.window.AppPanels;
let decide;
await api.open('first', {beforeClose: () => new Promise(resolve => { decide = resolve; })});
const closing = api.requestClose();
await tick();
assert.equal(f.first.el.hidden, false);
decide(false);
assert.equal(await closing, false);
assert.equal(f.first.el.hidden, false);
const accepted = api.requestClose();
await tick(); decide(true);
assert.equal(await accepted, true);
assert.equal(f.first.el.hidden, true);
""")
