"""Static guards on client-side HTML escaping in templates and static JS.

These render paths build markup by string concatenation and assign it to
``innerHTML``, so the escaping is the only thing standing between a Spotify
playlist title -- which any Spotify account holder can set -- and markup in
another user's authenticated page.

Two distinct invariants are enforced:

1. ``escapeHtml()`` is a TEXT-node escape. Per the HTML fragment serialization
   algorithm a text node encodes ``&``, ``<`` and ``>`` but leaves ``"`` and
   ``'`` untouched, so its output can still terminate a quoted attribute and
   introduce a new one. Inside quotes the attribute-safe ``escapeAttr()`` is
   required.
2. The specific Spotify-supplied fields that reach ``innerHTML`` stay wrapped
   in an escape helper.

These are source assertions over the page templates and static scripts; they
fail as soon as the convention slips.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNED = sorted((REPO_ROOT / "shuffify" / "templates").glob("*.html")) + sorted(
    (REPO_ROOT / "shuffify" / "static" / "js").glob("*.js")
)

# True when the text emitted to the left of an interpolation leaves us inside an
# unclosed quoted attribute value.
_OPEN_ATTR = re.compile(r'\b[\w:-]+\s*=\s*["\'][^"\']*$')
# Literal chunks of a JS string-concatenation expression.
_JS_LITERAL = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def _emitted_prefix(js_prefix: str) -> str:
    """Reduce a JS concat expression to the HTML text it emits."""
    return "".join(a or b for a, b in _JS_LITERAL.findall(js_prefix))


def _escape_calls_in_attribute_context(text: str):
    """Yield (line_no, expr) for escapeHtml() used inside a quoted attribute."""
    for line_no, line in enumerate(text.split("\n"), 1):
        for match in re.finditer(r"\$\{([^}]*escapeHtml\([^}]*)\}", line):
            if _OPEN_ATTR.search(line[: match.start()]):
                yield line_no, match.group(1).strip()
        for match in re.finditer(r"\+\s*(escapeHtml\([^)]*\))\s*\+", line):
            if _OPEN_ATTR.search(_emitted_prefix(line[: match.start()])):
                yield line_no, match.group(1).strip()


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: p.name)
def test_no_text_escape_inside_quoted_attribute(path):
    """escapeHtml() inside quotes cannot neutralise a quote -- use escapeAttr()."""
    offenders = list(_escape_calls_in_attribute_context(path.read_text(encoding="utf-8")))
    assert not offenders, (
        f"{path.name}: escapeHtml() used inside a quoted attribute at "
        + ", ".join(f"line {n} ({expr})" for n, expr in offenders)
        + " -- use escapeAttr(), which also encodes quotes."
    )


# Field -> the file whose innerHTML render path consumes it. Each of these is
# populated from Spotify (or another user's) input and reaches innerHTML.
SPOTIFY_SUPPLIED_FIELDS = [
    ("shuffify/templates/schedules.html", "src.source_name || src.source_playlist_id"),
    ("shuffify/templates/schedules.html", "src.source_playlist_id"),
    ("shuffify/templates/schedules.html", "pair.archive_playlist_name || 'Archive'"),
    ("shuffify/templates/schedules.html", "h.error_message"),
    ("shuffify/templates/workshop.html", "p.name"),
    ("shuffify/templates/workshop.html", "p.id"),
]


# Scripts that write every name as text (textContent and DOM builders) and so need no
# escape helper. They must never parse a string as markup at all; their pages' runtime
# suites also run them on hostile names (tests/test_playlist_setup_runtime.py).
TEXT_ONLY_SCRIPTS = ["shuffify/static/js/playlist-setup.js"]
_MARKUP_SINK = re.compile(
    r"\b(?:innerHTML|outerHTML|insertAdjacentHTML|createContextualFragment|DOMParser)\b"
    r"|document\.write"
)


@pytest.mark.parametrize("rel_path", TEXT_ONLY_SCRIPTS)
def test_text_only_script_has_no_markup_sink(rel_path):
    """A text-only page script cannot hand a Spotify name to the HTML parser."""
    text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert not _MARKUP_SINK.search(text), (
        f"{rel_path} parses a string as markup; build the element and set textContent."
    )
    # The guard sees the sinks it exists to refuse.
    for sink in ("list.innerHTML = x", "row.insertAdjacentHTML('beforeend', x)", "document.write(x)"):
        assert _MARKUP_SINK.search(sink)


@pytest.mark.parametrize("rel_path,expr", SPOTIFY_SUPPLIED_FIELDS)
def test_spotify_supplied_field_is_escaped(rel_path, expr):
    """No bare ``${field}`` interpolation for an attacker-settable value."""
    text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    bare = "${" + expr + "}"
    assert bare not in text, (
        f"{rel_path}: '{bare}' is interpolated without an escape helper. "
        "This value originates from a Spotify playlist title, which any "
        "Spotify account holder can set."
    )


# Fields interpolated at the START of a src/href attribute. escapeAttr() escapes
# quotes but leaves a hostile scheme intact -- ``javascript:``/``data:`` survive
# attribute-escaping perfectly -- so each must additionally pass through safeUrl()
# (a scheme allowlist) before it reaches the URL sink. All live in workshop.html.
WORKSHOP = REPO_ROOT / "shuffify/templates/workshop.html"
URL_CONTEXT_FIELDS = [
    "p.image_url",
    "track.album_image_url",
    "pl.image_url",
    # trackResultRow's artwork: Add tracks, Inbox and Archive rows hand their image URL to it.
    "image",
]


@pytest.mark.parametrize("field", URL_CONTEXT_FIELDS)
def test_url_field_passes_through_scheme_allowlist(field):
    """A URL-context value reaches a src/href attribute only via safeUrl()."""
    text = WORKSHOP.read_text(encoding="utf-8")
    assert f"safeUrl({field}" in text, (
        f"'{field}' feeds a URL attribute but is not wrapped in safeUrl(); "
        "escapeAttr() alone does not stop a javascript:/data: scheme."
    )
    assert f"escapeAttr({field})" not in text, (
        f"'{field}' is escaped for a URL sink without safeUrl() at some site -- "
        "every URL site must go through the scheme allowlist."
    )


def test_safeurl_helper_is_defined():
    """The scheme-allowlist helper the URL sites depend on must exist."""
    assert "function safeUrl(" in WORKSHOP.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_safeurl_blocks_dangerous_schemes():
    """Run the shipped safeUrl() in node against hostile and benign URLs.

    Escaping stops attribute breakout; only the scheme allowlist stops a
    ``javascript:`` value that never breaks out. The tab/newline cases guard the
    classic bypass: browsers strip control characters before parsing the scheme,
    so ``java<TAB>script:`` must be rejected outright rather than by prefix.
    """
    html = WORKSHOP.read_text(encoding="utf-8")
    # safeUrl() is top-level, so its body closes on a column-0 '}'. Extract the
    # shipped source rather than a copy, so the behaviour under test cannot drift.
    fn = re.search(r"^function safeUrl\(.*?^\}", html, re.S | re.M).group(0)
    tab, lf = chr(9), chr(10)
    cases = [
        ("https://i.scdn.co/x", "https://i.scdn.co/x"),
        ("http://example.com/x.png", "http://example.com/x.png"),
        ("/static/images/placeholder.svg", "/static/images/placeholder.svg"),
        ("images/x.png", "images/x.png"),
        ("#frag", "#frag"),
        ("", ""),
        ("javascript:alert(1)", ""),
        ("JavaScript:alert(1)", ""),
        ("  javascript:alert(1)", ""),
        ("java" + tab + "script:alert(1)", ""),
        ("java" + lf + "script:alert(1)", ""),
        ("javascript" + tab + ":alert(1)", ""),
        ("data:text/html,x", ""),
        ("vbscript:msgbox(1)", ""),
    ]
    harness = fn + "\nconsole.log(JSON.stringify(%s.map((c) => safeUrl(c[0]))));" % json.dumps(cases)
    result = subprocess.run(["node", "-"], input=harness, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [want for _, want in cases]


# The ``${field}`` check above only sees template literals. A render path that
# builds markup by concatenation -- ``'<p>' + field + '</p>'`` -- is invisible
# to it, which is how a field can stay escaped in one file and bare in another.
_BARE_CONCAT_OPERAND = re.compile(r"\+\s*\(*\s*$")


def _function_source(text: str, name: str) -> str:
    """Source of a top-level ``function name(...)``; it closes on a column-0 '}'."""
    match = re.search(rf"^function {name}\(.*?^\}}", text, re.S | re.M)
    assert match, f"could not locate function {name}() -- this guard would pass vacuously"
    return match.group(0)


def _mentions(expr: str) -> re.Pattern:
    """Match ``expr`` as a whole expression, never as part of a longer name."""
    return re.compile(r"(?<![\w.])" + re.escape(expr) + r"(?!\w)")


def _bare_concat_lines(source: str, expr: str):
    """Yield line numbers where ``expr`` is a direct operand of '+' concatenation."""
    for match in _mentions(expr).finditer(source):
        if _BARE_CONCAT_OPERAND.search(source[: match.start()]):
            yield source.count("\n", 0, match.start()) + 1


# (render function in workshop.html, value it concatenates into innerHTML).
# The archive name is written from a Spotify playlist name, which the
# playlist's owner sets. The snapshot description holds the same rule so the
# path stays safe if a writer ever records Spotify-supplied text in it.
CONCAT_RENDERED_FIELDS = [
    ("renderSnapshotTimeline", "description"),
    # A saved draft's name is the user's own text; a snapshot's track titles come from Spotify.
    ("savedDraftRow", "draft.session_name"),
    ("snapshotViewMarkup", "title"),
    ("snapshotViewMarkup", "view.message"),
    # Add tracks, Inbox and Archive rows: Spotify track titles and artists, and source names.
    ("trackResultRow", "title"),
    ("trackResultRow", "line"),
]


@pytest.mark.parametrize("function,expr", CONCAT_RENDERED_FIELDS)
def test_concatenated_field_is_escaped(function, expr):
    """A concatenated value reaches innerHTML only through an escape helper."""
    source = _function_source(WORKSHOP.read_text(encoding="utf-8"), function)
    assert _mentions(expr).search(source), (
        f"{function}() no longer references {expr}; update CONCAT_RENDERED_FIELDS"
    )
    offenders = list(_bare_concat_lines(source, expr))
    assert not offenders, (
        f"workshop.html {function}(): '{expr}' is concatenated into markup without "
        f"escapeHtml()/escapeAttr() at function-relative line(s) {offenders}."
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_workshop_hub_renders_playlist_picker_with_safe_shared_helpers(db_app):
    """The hub must receive and use the helpers, without the playlist-only block."""
    from flask import render_template, session

    with db_app.test_request_context("/workshop"):
        session["spotify_token"] = {"access_token": "test-token"}
        html = render_template(
            "workshop.html", playlist=None, csp_nonce="hub-test-nonce"
        )

    assert 'class="sh-shell sh-app-shell"' in html
    scripts = re.findall(r'<script\b[^>]*>(.*?)</script>', html, re.S)
    rendered_js = "\n".join(scripts)
    helper_names = ("escapeHtml", "escapeAttr", "safeUrl")
    for name in helper_names:
        assert rendered_js.count(f"function {name}(") == 1
    helper_block = next(script for script in scripts if "function safeUrl(" in script)
    # One disclosure helper, in the same always-rendered block, serves the hub picker too.
    assert rendered_js.count("function createDisclosure(") == 1
    assert "function createDisclosure(" in helper_block
    assert "createDisclosure(document.getElementById('hub-playlist-dropdown')" in rendered_js
    assert re.search(
        r'<script nonce="hub-test-nonce">\s*' + re.escape(helper_block.strip()), html
    )

    functions = "\n".join(
        _function_source(rendered_js, name)
        for name in (*helper_names, "renderHubDropdown")
    )
    harness = r"""
const assert = require('node:assert/strict');
const list = {innerHTML: ''};
const document = {
  getElementById(id) {
    assert.equal(id, 'hub-dropdown-list');
    return list;
  },
  createTextNode(value) { return {text: String(value)}; },
  createElement(tag) {
    assert.equal(tag, 'div');
    return {
      text: '',
      appendChild(node) { this.text += node.text; },
      get innerHTML() {
        return this.text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      }
    };
  }
};
""" + functions + r"""
renderHubDropdown([{
  id: 'playlist" data-unsafe="yes',
  name: 'Mix <img src=x onerror=alert(1)> & friends',
  image_url: 'javascript:alert(1)',
  track_count: 12
}], 'mix');
assert.ok(list.innerHTML.includes('href="/workshop/playlist&quot; data-unsafe=&quot;yes"'));
assert.ok(list.innerHTML.includes('Mix &lt;img src=x onerror=alert(1)&gt; &amp; friends'));
assert.ok(!list.innerHTML.includes('/static/images/placeholder.svg'));
assert.ok(list.innerHTML.includes('<svg '));
assert.ok(list.innerHTML.includes('aria-hidden="true"'));
assert.ok(!list.innerHTML.includes('src="javascript:'));
assert.equal((list.innerHTML.match(/<img /g) || []).length, 0);
renderHubDropdown([{id: 'safe', name: 'Morning Mix', image_url: 'https://example.com/art.png', track_count: 12}], '');
assert.ok(list.innerHTML.includes('src="https://example.com/art.png"'));
assert.equal((list.innerHTML.match(/<img /g) || []).length, 1);
renderHubDropdown([], '');
assert.ok(list.innerHTML.includes('No playlists found'));
"""
    subprocess.run(["node", "-"], input=harness, capture_output=True, text=True, check=True)


def test_toggle_field_escapes_its_extra_attributes_and_names_only_a_submitted_toggle():
    """The shared toggle (settings and playlist setup) writes every value as an escaped attribute."""
    from jinja2 import Environment, FileSystemLoader

    environment = Environment(loader=FileSystemLoader(REPO_ROOT / "shuffify" / "templates"), autoescape=True)
    toggle = environment.get_template("macros/forms.html").module.toggle_field
    hostile = '"><img src=x onerror="alert(1)">'
    html = str(toggle(hostile, "pick", description=hostile, attributes={"data-action-change": hostile},
                      describedby="pick-error"))
    assert "<img" not in html
    assert html.count("&lt;img") == 3
    assert 'aria-describedby="pick-help pick-error"' in html
    # A toggle that a script reads is not submitted with a form.
    assert "name=" not in html
    named = str(toggle("Pick", "pick", "pick", checked=True))
    assert 'name="pick" value="true"' in named
    assert 'aria-describedby="pick-help"' not in named and "checked" in named
