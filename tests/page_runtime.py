"""Run a rendered page's own scripts under Node, over a small DOM built from its markup.

Flask renders the real template with fixture data, so every id, class, label and data
attribute a script relies on comes from the shipped markup, not from a stub written to
match the script. Node then runs the page's scripts the way a browser orders them:
classic scripts in document order, deferred ones after parsing, then DOMContentLoaded.
The shipped actions.js, app-panels.js and playlist-operations.js run as they are;
fetch, notifications, page reloads and the shared confirmation dialog are test doubles.

The DOM models what these pages use: selectors, events with capture and bubbling,
focus with the focus fixup rule (a removed, hidden, inert or disabled element loses
focus, and so does a moved one), ``hidden`` and the ``hidden`` utility class, closed ``<details>``, forms and
``innerHTML``. It checks behavior contracts, not layout or rendering; the real-browser pass in
tests/e2e/test_pages.py covers those.
"""

import json
from html.parser import HTMLParser
from types import SimpleNamespace

from tests.node_runtime import run_scenario
from tests.test_playlist_operations_runtime import ROOT

VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _Tree(HTMLParser):
    """Rendered HTML as nested ``{"t": tag, "a": [[name, value]], "c": [...]}`` nodes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"t": "#document", "a": [], "c": []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = {
            "t": tag,
            "a": [[name, "" if value is None else value] for name, value in attrs],
            "c": [],
        }
        self.stack[-1]["c"].append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1]["c"].append(
            {
                "t": tag,
                "a": [[name, "" if value is None else value] for name, value in attrs],
                "c": [],
            }
        )

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]["t"] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if data:
            self.stack[-1]["c"].append(data)


def page_tree(html):
    parser = _Tree()
    parser.feed(html)
    parser.close()
    return parser.root["c"]


def render_page(db_app, template, path="/", **context):
    """Render ``template`` for a signed-in session, as its route would."""
    from flask import render_template, session

    with db_app.test_request_context(path):
        session["spotify_token"] = {
            "access_token": "valid-fixture",
            "refresh_token": "fixture-refresh",
            "expires_at": 4102444800,
            "token_type": "Bearer",
        }
        session["playlist_states"] = context.pop("playlist_states", {})
        return render_template(template, **context)


def pref(pinned=False, hidden=False):
    return SimpleNamespace(is_pinned=pinned, is_hidden=hidden)


# The DOM, the page loader and the test doubles. Scenarios use `page` and `tick`.
PAGE_DOM = r"""
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
    'param', 'source', 'track', 'wbr']);
const NATIVE_FOCUS = new Set(['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY']);
const tick = () => new Promise(resolve => setImmediate(resolve));
// Enough turns for a click to reach the network, a reply to arrive and the page to update.
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };
// The shared announcer writes once after the task that spoke (AppPanels.announce); wait for it.
const heard = () => new Promise(resolve => setTimeout(resolve, 5));
// Every request that could change something (a review's context read is a POST too).
const writes = () => page.calls.filter(call => call.method !== 'GET');
let page;

const kebab = name => name.replace(/[A-Z]/g, letter => '-' + letter.toLowerCase());
const camel = name => name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
const entities = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', middot: '·'};
const decode = text => text.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (whole, code) => code[0] === '#'
    ? String.fromCodePoint(code[1] === 'x' || code[1] === 'X' ? parseInt(code.slice(2), 16) : Number(code.slice(1)))
    : entities[code] !== undefined ? entities[code] : whole);
const escapeText = text => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

class PageText {
    constructor(text) { this.nodeType = 3; this.data = String(text); this.parentNode = null; }
    get textContent() { return this.data; }
    set textContent(value) { this.data = String(value); }
    get isConnected() { return Boolean(this.parentNode && this.parentNode.isConnected); }
    get nextSibling() { return nextNode(this); }
}

// The node after this one in its parent, text included.
function nextNode(node) {
    const nodes = node.parentNode ? node.parentNode.childNodes : [];
    return nodes[nodes.indexOf(node) + 1] || null;
}

class PageElement {
    constructor(tag, attrs) {
        this.nodeType = 1; this.localName = tag.toLowerCase(); this.tagName = tag.toUpperCase();
        this.attributes = new Map(attrs || []); this.childNodes = []; this.parentNode = null;
        this.listeners = []; this.state = {}; this.scrollTop = 0;
        // An inline style that custom properties can be written to (a measured footer space).
        this.style = {setProperty(name, value) { this[name] = String(value); },
            getPropertyValue(name) { return this[name] || ''; }};
        this.dataset = new Proxy({}, {
            get: (_, key) => typeof key === 'string' ? this.getAttribute('data-' + kebab(key)) ?? undefined : undefined,
            set: (_, key, value) => { this.setAttribute('data-' + kebab(key), value); return true; },
            has: (_, key) => this.hasAttribute('data-' + kebab(key)),
            deleteProperty: (_, key) => { this.removeAttribute('data-' + kebab(key)); return true; },
            ownKeys: () => [...this.attributes.keys()].filter(name => name.startsWith('data-'))
                .map(name => camel(name.slice(5))),
            getOwnPropertyDescriptor: (_, key) => this.hasAttribute('data-' + kebab(key))
                ? {enumerable: true, configurable: true, value: this.getAttribute('data-' + kebab(key))} : undefined,
        });
        const classes = () => new Set((this.getAttribute('class') || '').split(/\s+/).filter(Boolean));
        const write = set => this.setAttribute('class', [...set].join(' '));
        this.classList = {
            contains: name => classes().has(name),
            add: (...names) => { const set = classes(); names.forEach(name => set.add(name)); write(set); },
            remove: (...names) => { const set = classes(); names.forEach(name => set.delete(name)); write(set); },
            toggle: (name, force) => {
                const set = classes(), on = force === undefined ? !set.has(name) : Boolean(force);
                if (on) set.add(name); else set.delete(name);
                write(set); return on;
            },
        };
    }
    // Attributes, and the properties that reflect them.
    getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    removeAttribute(name) { this.attributes.delete(name); }
    hasAttribute(name) { return this.attributes.has(name); }
    toggleAttribute(name, force) {
        const on = force === undefined ? !this.hasAttribute(name) : Boolean(force);
        if (on) this.setAttribute(name, ''); else this.removeAttribute(name);
        return on;
    }
    flag(name, value) { this.toggleAttribute(name, Boolean(value)); }
    get id() { return this.getAttribute('id') || ''; }
    set id(value) { this.setAttribute('id', value); }
    get className() { return this.getAttribute('class') || ''; }
    set className(value) { this.setAttribute('class', value); }
    get title() { return this.getAttribute('title') || ''; }
    set title(value) { this.setAttribute('title', value); }
    get name() { return this.getAttribute('name') || ''; }
    set name(value) { this.setAttribute('name', value); }
    get type() {
        return (this.getAttribute('type') || (this.localName === 'button' ? 'submit' : 'text')).toLowerCase();
    }
    set type(value) { this.setAttribute('type', value); }
    get href() { return this.getAttribute('href') || ''; }
    set href(value) { this.setAttribute('href', value); }
    get target() { return this.getAttribute('target') || ''; }
    set target(value) { this.setAttribute('target', value); }
    get rel() { return this.getAttribute('rel') || ''; }
    set rel(value) { this.setAttribute('rel', value); }
    get min() { return this.getAttribute('min') || ''; }
    set min(value) { this.setAttribute('min', value); }
    get max() { return this.getAttribute('max') || ''; }
    set max(value) { this.setAttribute('max', value); }
    get step() { return this.getAttribute('step') || ''; }
    set step(value) { this.setAttribute('step', value); }
    get htmlFor() { return this.getAttribute('for') || ''; }
    set htmlFor(value) { this.setAttribute('for', value); }
    get required() { return this.hasAttribute('required'); }
    set required(value) { this.flag('required', value); }
    get hidden() { return this.hasAttribute('hidden'); }
    set hidden(value) { this.flag('hidden', value); }
    get disabled() { return this.hasAttribute('disabled'); }
    set disabled(value) { this.flag('disabled', value); }
    get inert() { return this.hasAttribute('inert'); }
    set inert(value) { this.flag('inert', value); }
    get open() { return this.hasAttribute('open'); }
    set open(value) { this.flag('open', value); }
    get draggable() { return this.getAttribute('draggable') === 'true'; }
    set draggable(value) { this.setAttribute('draggable', String(Boolean(value))); }
    get tabIndex() {
        if (this.hasAttribute('tabindex')) return Number(this.getAttribute('tabindex'));
        return NATIVE_FOCUS.has(this.tagName) || (this.tagName === 'A' && this.hasAttribute('href')) ? 0 : -1;
    }
    set tabIndex(value) { this.setAttribute('tabindex', value); }
    get checked() { return 'checked' in this.state ? this.state.checked : this.hasAttribute('checked'); }
    set checked(value) { this.state.checked = Boolean(value); }
    get selected() { return 'selected' in this.state ? this.state.selected : this.hasAttribute('selected'); }
    set selected(value) { this.state.selected = Boolean(value); }
    get options() { return this.querySelectorAll('option'); }
    get selectedIndex() {
        const options = this.options, index = options.findIndex(option => option.selected);
        return index >= 0 ? index : options.length ? 0 : -1;
    }
    get selectedOptions() { return this.selectedIndex >= 0 ? [this.options[this.selectedIndex]] : []; }
    // A number field reads as a number: NaN when empty, as a number input does.
    get valueAsNumber() { const value = this.value; return value.trim() === '' ? NaN : Number(value); }
    get value() {
        if (this.localName === 'select') {
            const option = this.selectedOptions[0];
            return option ? option.value : '';
        }
        if (this.localName === 'option') {
            return this.hasAttribute('value') ? this.getAttribute('value') : this.textContent;
        }
        if ('value' in this.state) return this.state.value;
        return this.getAttribute('value') || '';
    }
    set value(value) {
        if (this.localName !== 'select') { this.state.value = String(value); return; }
        this.options.forEach(option => { option.selected = option.value === String(value); });
    }
    get form() {
        const owner = this.getAttribute('form');
        return owner ? page.document.getElementById(owner) : this.closest('form');
    }
    checkValidity() { return true; }
    reportValidity() { return true; }
    // The tree.
    get children() { return this.childNodes.filter(node => node.nodeType === 1); }
    get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
    get isConnected() { return Boolean(this.parentNode && this.parentNode.isConnected); }
    get firstElementChild() { return this.children[0] || null; }
    get nextElementSibling() { return this.sibling(1); }
    get previousElementSibling() { return this.sibling(-1); }
    get nextSibling() { return nextNode(this); }
    sibling(step) {
        if (!this.parentNode) return null;
        const siblings = this.parentNode.children;
        return siblings[siblings.indexOf(this) + step] || null;
    }
    insertBefore(node, reference) {
        if (node.parentNode) node.parentNode.removeChild(node);
        const index = reference ? this.childNodes.indexOf(reference) : -1;
        if (index >= 0) this.childNodes.splice(index, 0, node); else this.childNodes.push(node);
        node.parentNode = this;
        return node;
    }
    appendChild(node) { return this.insertBefore(node, null); }
    append(...nodes) { nodes.forEach(node => this.appendChild(typeof node === 'string' ? new PageText(node) : node)); }
    prepend(...nodes) {
        const first = this.childNodes[0] || null;
        nodes.forEach(node => this.insertBefore(typeof node === 'string' ? new PageText(node) : node, first));
    }
    removeChild(node) {
        const index = this.childNodes.indexOf(node);
        if (index >= 0) this.childNodes.splice(index, 1);
        node.parentNode = null;
        // As in browsers, removing a node takes focus out of it, even when the same node is
        // put straight back (a move): a page that keeps focus there must focus it again.
        if (page && page.focused && node.nodeType === 1 && node.contains(page.focused)) page.focused = null;
        return node;
    }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    replaceChildren(...nodes) {
        this.childNodes.slice().forEach(node => this.removeChild(node));
        this.append(...nodes);
    }
    contains(node) {
        for (let current = node; current; current = current.parentNode) if (current === this) return true;
        return false;
    }
    get textContent() { return this.childNodes.map(node => node.textContent).join(''); }
    set textContent(value) { this.replaceChildren(...(String(value) ? [new PageText(value)] : [])); }
    get innerHTML() { return this.childNodes.map(serialize).join(''); }
    set innerHTML(html) { this.replaceChildren(...parseHTML(String(html))); }
    // Selectors.
    matches(selector) { return parseSelector(selector).some(parts => matchesComplex(this, parts)); }
    closest(selector) {
        for (let element = this; element; element = element.parentElement) {
            if (element.matches(selector)) return element;
        }
        return null;
    }
    querySelectorAll(selector) { return descendants(this).filter(element => element.matches(selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    // Rendering, focus and events.
    getClientRects() { return rendered(this) ? [{top: 0, left: 0, width: 1, height: 1}] : []; }
    getBoundingClientRect() { return {top: 0, left: 0, bottom: 1, right: 1, width: 1, height: 1}; }
    get clientHeight() { return rendered(this) ? 400 : 0; }
    get scrollHeight() { return rendered(this) ? 800 : 0; }
    scrollIntoView() {}
    focus(options = {}) {
        if (!focusable(this)) return;
        const previous = page.focused;
        page.focused = this; page.focusOptions = options;
        if (previous !== this) this.dispatchEvent(page.event('focusin', {bubbles: true}));
    }
    blur() { if (page.document.activeElement === this) page.focused = null; }
    click() {
        if (this.disabled) return;
        const box = this.localName === 'input' && ['checkbox', 'radio'].includes(this.type);
        if (box) this.checked = !this.checked;
        const event = page.event('click', {bubbles: true, cancelable: true, button: 0});
        this.dispatchEvent(event);
        if (event.defaultPrevented) { if (box) this.checked = !this.checked; return; }
        if (box) this.dispatchEvent(page.event('change', {bubbles: true}));
        if (this.localName === 'summary' && this.parentElement && this.parentElement.localName === 'details') {
            this.parentElement.open = !this.parentElement.open;
        }
        const form = this.localName === 'button' && this.type === 'submit' ? this.form : null;
        if (form) {
            const submit = page.event('submit', {bubbles: true, cancelable: true, submitter: this});
            form.dispatchEvent(submit);
            if (!submit.defaultPrevented) page.nativeSubmits.push(form.id);
        }
    }
    addEventListener(type, listener, options) {
        this.listeners.push({type, listener, capture: options === true || Boolean(options && options.capture)});
    }
    removeEventListener(type, listener) {
        this.listeners = this.listeners.filter(entry => entry.type !== type || entry.listener !== listener);
    }
    dispatchEvent(event) { return page.dispatch(this, event); }
}

function serialize(node) {
    if (node.nodeType === 3) return escapeText(node.data);
    const quoted = value => '="' + value.replace(/&/g, '&amp;').replace(/"/g, '&quot;') + '"';
    const attrs = [...node.attributes].map(([name, value]) => ' ' + name + (value === '' ? '' : quoted(value)))
        .join('');
    const inner = VOID.has(node.localName) ? '' : node.childNodes.map(serialize).join('') + '</' + node.localName + '>';
    return '<' + node.localName + attrs + '>' + inner;
}

// Enough of an HTML parser for the markup page scripts build: well-formed tags,
// quoted or bare attributes, void elements, text and comments.
function parseHTML(html) {
    const root = new PageElement('template'), stack = [root];
    const token = new RegExp([/<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>/.source,
        /|<([a-zA-Z][\w-]*)((?:\s+[^\s"'>\/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*)\s*(\/?)>/.source,
        /|([^<]+)/.source].join(''), 'g');
    let match;
    while ((match = token.exec(html))) {
        if (match[1]) {
            for (let index = stack.length - 1; index > 0; index--) {
                if (stack[index].localName === match[1].toLowerCase()) { stack.length = index; break; }
            }
        } else if (match[2]) {
            const attrs = [];
            const attribute = /([^\s"'>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
            let part;
            while ((part = attribute.exec(match[3] || ''))) {
                attrs.push([part[1].toLowerCase(), decode(part[2] ?? part[3] ?? part[4] ?? '')]);
            }
            const element = new PageElement(match[2], attrs);
            stack[stack.length - 1].appendChild(element);
            if (!match[4] && !VOID.has(element.localName)) stack.push(element);
        } else if (match[5]) {
            stack[stack.length - 1].appendChild(new PageText(decode(match[5])));
        }
    }
    return root.childNodes.slice();
}

function descendants(root) {
    const found = [];
    (function walk(node) {
        node.childNodes.forEach(child => {
            if (child.nodeType !== 1) return;
            found.push(child);
            walk(child);
        });
    })(root);
    return found;
}

// Selectors: tag, #id, .class, [name], [name="value"], :checked, :not(...), and the
// descendant and child combinators, in comma-separated groups.
const selectorCache = new Map();
function splitTop(text, separator) {
    const parts = [];
    let depth = 0, quote = null, start = 0;
    for (let index = 0; index < text.length; index++) {
        const ch = text[index];
        if (quote) { if (ch === quote) quote = null; }
        else if (ch === '"' || ch === "'") quote = ch;
        else if (ch === '(' || ch === '[') depth++;
        else if (ch === ')' || ch === ']') depth--;
        else if (depth === 0 && ch === separator) { parts.push(text.slice(start, index)); start = index + 1; }
    }
    parts.push(text.slice(start));
    return parts;
}
function parseSelector(selector) {
    if (selectorCache.has(selector)) return selectorCache.get(selector);
    const groups = splitTop(selector, ',').map(group => {
        const parts = [];
        let text = group.trim().replace(/\s*>\s*/g, ' > '), combinator = null;
        splitTop(text, ' ').filter(Boolean).forEach(piece => {
            if (piece === '>') { combinator = '>'; return; }
            parts.push({compound: parseCompound(piece), combinator: parts.length ? combinator || ' ' : null});
            combinator = null;
        });
        return parts;
    });
    selectorCache.set(selector, groups);
    return groups;
}
function parseCompound(text) {
    const tests = [];
    const token = new RegExp([/^(\*|[a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)/.source,
        /|\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]/.source,
        /|:not\(([^)]*)\)|:([\w-]+)/.source].join(''), 'g');
    let match, consumed = 0;
    while ((match = token.exec(text))) {
        if (match.index !== consumed) throw new Error('Unsupported selector: ' + text);
        consumed = token.lastIndex;
        if (match[1]) { const tag = match[1].toLowerCase(); if (tag !== '*') tests.push(el => el.localName === tag); }
        else if (match[2]) { const id = match[2]; tests.push(el => el.getAttribute('id') === id); }
        else if (match[3]) { const name = match[3]; tests.push(el => el.classList.contains(name)); }
        else if (match[4]) {
            const name = match[4], value = match[5] ?? match[6] ?? match[7];
            tests.push(value === undefined ? el => el.hasAttribute(name) : el => el.getAttribute(name) === value);
        } else if (match[8] !== undefined) {
            const inner = parseCompound(match[8]);
            tests.push(el => !inner(el));
        } else if (match[9] === 'checked') tests.push(el => el.localName === 'option' ? el.selected : el.checked);
        else throw new Error('Unsupported pseudo-class: ' + match[9]);
    }
    if (consumed !== text.length) throw new Error('Unsupported selector: ' + text);
    return el => tests.every(test => test(el));
}
function matchesComplex(element, parts) {
    function from(el, index) {
        if (!parts[index].compound(el)) return false;
        if (index === 0) return true;
        if (parts[index].combinator === '>') return Boolean(el.parentElement) && from(el.parentElement, index - 1);
        for (let ancestor = el.parentElement; ancestor; ancestor = ancestor.parentElement) {
            if (from(ancestor, index - 1)) return true;
        }
        return false;
    }
    return from(element, parts.length - 1);
}

// Shown: connected, with no hidden attribute or hidden utility class on it or any
// ancestor, and not inside a closed details element other than through its summary.
function rendered(element) {
    if (!element.isConnected) return false;
    for (let node = element; node; node = node.parentElement) {
        if (node.hasAttribute('hidden') || node.classList.contains('hidden')) return false;
        const parent = node.parentElement;
        if (parent && parent.localName === 'details' && !parent.open && node.localName !== 'summary') return false;
    }
    return true;
}
function focusable(element) {
    return rendered(element) && !element.disabled && !element.closest('[inert]') &&
        (element.hasAttribute('tabindex') || element.tabIndex >= 0);
}

class PageDocument {
    constructor() {
        this.nodeType = 9; this.childNodes = []; this.parentNode = null; this.listeners = [];
        this.readyState = 'loading';
    }
    get isConnected() { return true; }
    get documentElement() { return this.childNodes.find(node => node.nodeType === 1) || null; }
    get body() { return this.documentElement.querySelector('body'); }
    // The focus fixup rule: an element that is removed, hidden, inert or disabled loses focus.
    get activeElement() {
        if (page.focused && !focusable(page.focused)) page.focused = null;
        return page.focused || this.body;
    }
    appendChild(node) { node.parentNode = this; this.childNodes.push(node); return node; }
    removeChild(node) { this.childNodes.splice(this.childNodes.indexOf(node), 1); node.parentNode = null; return node; }
    getElementById(id) { return descendants(this).find(element => element.getAttribute('id') === id) || null; }
    querySelectorAll(selector) { return descendants(this).filter(element => element.matches(selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    createElement(tag) { return new PageElement(tag, []); }
    createTextNode(text) { return new PageText(text); }
    addEventListener(...args) { PageElement.prototype.addEventListener.apply(this, args); }
    removeEventListener(...args) { PageElement.prototype.removeEventListener.apply(this, args); }
    dispatchEvent(event) { return page.dispatch(this, event); }
}

function build(node) {
    if (typeof node === 'string') return new PageText(node);
    const element = new PageElement(node.t, node.a);
    node.c.forEach(child => element.appendChild(build(child)));
    return element;
}

function makeEvent(type, init = {}) {
    return Object.assign({type, bubbles: false, cancelable: false, defaultPrevented: false, stopped: false,
        preventDefault() { if (this.cancelable) this.defaultPrevented = true; },
        stopPropagation() { this.stopped = true; },
        stopImmediatePropagation() { this.stopped = true; }}, init);
}

// Builds the page at options.path and runs its scripts. Scripts in `replaced` do not run;
// test doubles stand in for them (app-feedback.js keeps timers, base.js wraps fetch and
// menus). The shared confirmation is a double too: page.asked and page.answer.
// options.before(window) runs before the page's scripts, to give the window what a scenario's
// browser has (a ResizeObserver, say).
function loadPage(tree, options = {}) {
    const document = new PageDocument();
    tree.forEach(node => { if (typeof node !== 'string') document.appendChild(build(node)); });
    const calls = [], replies = [], notices = [], navigations = [], errors = [];
    const window = {document, innerWidth: 1440, innerHeight: 900, console,
        // Page timers (a run's five-minute request limit) never hold Node open after a scenario.
        setTimeout: (callback, delay, ...args) => setTimeout(callback, delay, ...args).unref(),
        clearTimeout, AbortController, URL,
        location: {href: 'https://example.test' + (options.path || '/'),
            reload: () => { navigations.push('reload'); }, assign: url => { navigations.push(url); }},
        getComputedStyle: () => ({getPropertyValue: name => name === '--sh-breakpoint-workspace' ? '1024px' : ''}),
        matchMedia: () => ({matches: true, addEventListener() {}}),
        listeners: [],
        addEventListener(...args) { PageElement.prototype.addEventListener.apply(this, args); },
        removeEventListener(...args) { PageElement.prototype.removeEventListener.apply(this, args); },
        dispatchEvent(event) { return page.dispatch(this, event); },
        // A toast whose words were announced elsewhere is recorded as spoken: false.
        showNotification: (message, type, options) => {
            notices.push(Object.assign({message, type}, options && options.spoken === false ? {spoken: false} : {}));
        },
        fetch: async (url, init = {}) => {
            calls.push({url, method: (init.method || 'GET').toUpperCase(), body: init.body, init});
            // A request no scenario queued a reply for is answered by the page's own route table
            // (options.route), when it has one: its reads on load and on a tool's first opening.
            const reply = replies.shift() || (options.route && options.route(url, init));
            if (!reply) throw new Error('No reply queued for ' + url);
            return typeof reply === 'function' ? reply(url, init) : reply;
        }};
    window.window = window; window.self = window;
    window.CustomEvent = class {
        constructor(type, init = {}) { Object.assign(this, makeEvent(type, init)); this.detail = init.detail; }
    };
    window.Event = class { constructor(type, init = {}) { Object.assign(this, makeEvent(type, init)); } };
    window.FormData = class extends FormData {
        constructor(form) {
            super();
            if (!form) return;
            form.querySelectorAll('input, select, textarea').forEach(field => {
                if (!field.name || field.disabled) return;
                if (['checkbox', 'radio'].includes(field.type) && !field.checked) return;
                this.append(field.name, field.value);
            });
        }
    };
    page = {document, window, calls, replies, notices, navigations, errors, focused: null, focusOptions: null,
        nativeSubmits: [],
        event: makeEvent,
        dispatch(target, event) {
            event.target = target;
            const path = [];
            for (let node = target; node; node = node.parentNode) path.push(node);
            if (path[path.length - 1] === document) path.push(window);
            // Capture from the outside in, every listener at the target, then bubble out.
            const run = (node, phase) => node.listeners.slice().forEach(entry => {
                if (entry.type !== event.type || event.stopped) return;
                if (phase !== 'target' && entry.capture !== (phase === 'capture')) return;
                event.currentTarget = node;
                try { entry.listener.call(node, event); } catch (error) { errors.push(error); console.error(error); }
            });
            for (let index = path.length - 1; index > 0 && !event.stopped; index--) run(path[index], 'capture');
            if (!event.stopped) run(target, 'target');
            if (event.bubbles) {
                for (let index = 1; index < path.length && !event.stopped; index++) run(path[index], 'bubble');
            }
            return !event.defaultPrevented;
        },
        $: selector => document.querySelector(selector),
        $$: selector => document.querySelectorAll(selector),
        byId: id => document.getElementById(id),
        text: element => element.textContent.replace(/\s+/g, ' ').trim(),
        reply(data, status = 200) {
            return {ok: status < 400, status, json: async () => data, headers: {get: () => 'application/json'},
                clone() { return this; }, arrayBuffer: async () => new ArrayBuffer(0)};
        },
        key(target, key, init = {}) {
            const event = makeEvent('keydown', Object.assign({key, bubbles: true, cancelable: true}, init));
            target.dispatchEvent(event);
            return event;
        },
        change(target) { target.dispatchEvent(makeEvent('change', {bubbles: true})); },
        input(target) { target.dispatchEvent(makeEvent('input', {bubbles: true})); },
    };
    if (options.before) options.before(window);
    vm.createContext(window);
    const replaced = new Set(['/static/js/app-feedback.js', '/static/js/base.js']);
    const scripts = document.querySelectorAll('script'), deferred = [];
    const run = (code, name) => vm.runInContext(code, window, {filename: name});
    scripts.forEach(script => {
        const source = script.getAttribute('src');
        if (source && replaced.has(source)) return;
        const job = source ? () => run(fs.readFileSync('shuffify' + source, 'utf8'), source)
            : () => run(script.textContent, 'inline script');
        if (source && script.hasAttribute('defer')) deferred.push(job); else job();
    });
    deferred.forEach(job => job());
    // The shared confirmation records each request and answers page.answer (yes by default).
    page.asked = []; page.answer = true;
    window.AppConfirm = {ask: async request => { page.asked.push(request); return page.answer; }};
    document.readyState = 'interactive';
    document.dispatchEvent(makeEvent('DOMContentLoaded'));
    return page;
}
"""


def run_page(node, html, scenario):
    """Run ``scenario``, an async body that calls ``loadPage(TREE, options)``, on ``html``."""
    script = (
        PAGE_DOM
        + "\nconst TREE = "
        + json.dumps(page_tree(html))
        + ";\n(async () => {\n"
        + scenario
        + "\n})().catch(error => { console.error(error); process.exitCode = 1; });"
    )
    return run_scenario(node, script, ROOT)
