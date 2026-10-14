"""What CSP forbids in templates: inline event handlers and style attributes.

A CSP nonce authorises an **element**, never an **attribute**. `style-src` and
`script-src` carry a nonce and no `unsafe-inline`, so two things are inert no
matter how correct they look in the markup:

* `on*` event handler attributes
* `style` attributes

This is one contract for both, so the next person who adds either one finds
out from a test rather than from the live site.

The failure mode being guarded is silent by construction: the browser drops
the declaration and renders on. Nothing errors, nothing logs, and the page
looks plausible while a thumbnail renders blank or a button does nothing.

**The JS/CSSOM path is deliberately allowed.** `el.style.transform = '...'`
and `el.classList.add(...)` are not blocked and are the supported way to style
dynamically. Only the *attribute* is forbidden — including one written into an
HTML string that later reaches `innerHTML`.
"""

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "shuffify" / "templates"
# Served by the /terms and /privacy routes with the same CSP, but outside
# templates/, so the checks scan this directory explicitly.
PUBLIC_DIR = REPO_ROOT / "shuffify" / "static" / "public"
JS_DIR = REPO_ROOT / "shuffify" / "static" / "js"

# A <style> block is nonced and legitimate, and its CSS body can mention
# `style="..."` in prose. Strip those regions before scanning so documentation
# of the rule does not trip the rule. <script> blocks are NOT stripped: a
# `style=` written into an HTML string there becomes a real attribute the
# moment it is assigned to innerHTML.
STYLE_BLOCK = re.compile(r"<style\b.*?</style>", re.DOTALL | re.IGNORECASE)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

STYLE_ATTR = re.compile(r"(?<![\w:-])style\s*=", re.IGNORECASE)

# `on` + letters, immediately followed by `=`. Excludes data-on-*, and words
# such as `onchange_handler` because `=` must follow directly.
HANDLER_ATTR = re.compile(r"(?<![\w:-])on[a-z]+\s*=", re.IGNORECASE)

# Legitimate `on*=` spellings that are not handler attributes.
HANDLER_ALLOWED = re.compile(r"""\b(only|once|onto)\s*=""", re.IGNORECASE)

# JS comments are stripped before scanning: handler markup quoted in a
# comment is documentation, not a handler.
# Quoted URLs/HTML remain intact so comment-like text inside a builder cannot
# hide a real attribute later in that string.
JS_COMMENTS_OR_STRINGS = re.compile(
    r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|"
    r"`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/", re.DOTALL
)


def _strip_js_comments(text: str) -> str:
    # Keep quoted HTML/URLs intact: // and /* inside a builder string are not
    # JS comments and must not hide attributes later in that string.
    return JS_COMMENTS_OR_STRINGS.sub(
        lambda match: "\n" * match[0].count("\n")
        if match[0].startswith(("//", "/*")) else match[0], text
    )


def _templates():
    return sorted(TEMPLATE_DIR.rglob("*.html")) + sorted(PUBLIC_DIR.rglob("*.html"))


def _html_sources():
    # HTML builders in shipped JS are subject to the same attribute policy.
    return _templates() + sorted(JS_DIR.rglob("*.js"))


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _scannable(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = STYLE_BLOCK.sub("", text)
    text = HTML_COMMENT.sub("", text)
    text = _strip_js_comments(text)
    return text


def _offending_lines(path: Path, pattern: re.Pattern):
    text = _scannable(path)
    lines = text.splitlines()
    hits = []
    for match in pattern.finditer(text):
        if pattern is HANDLER_ATTR and HANDLER_ALLOWED.fullmatch(match[0]):
            continue
        lineno = text.count("\n", 0, match.start()) + 1
        hits.append((lineno, lines[lineno - 1].strip()[:120]))
    return hits


@pytest.mark.parametrize("path", _html_sources(), ids=lambda p: p.name)
def test_no_style_attribute(path):
    """`style="..."` is inert under this CSP."""
    hits = _offending_lines(path, STYLE_ATTR)
    assert not hits, (
        f"{_rel(path)} declares style via an attribute, "
        "which CSP drops silently:\n"
        + "\n".join(f"  line {n}: {t}" for n, t in hits)
        + "\nPut the declaration in the nonced <style> block as a class, or "
        "set it from JS (el.style.x / classList) — the CSSOM path is allowed."
    )


@pytest.mark.parametrize("path", _html_sources(), ids=lambda p: p.name)
def test_no_inline_event_handler(path):
    """Inline `on*=` handlers are inert under this CSP."""
    hits = _offending_lines(path, HANDLER_ATTR)
    assert not hits, (
        f"{_rel(path)} declares an inline event handler, "
        "which CSP drops silently:\n"
        + "\n".join(f"  line {n}: {t}" for n, t in hits)
        + '\nUse the delegated dispatcher: data-action-click="..." plus a '
        "handler registered in static/js/actions.js."
    )


def test_the_guard_can_actually_see_a_violation():
    """The patterns must match real violations, not merely fail to fire.

    A contract test that cannot fail is worse than no contract test: it
    reports safety it is not checking. These are the spellings the guard
    must catch.
    """
    assert STYLE_ATTR.search('<div class="x" style="background: red;">')
    assert STYLE_ATTR.search("""'<div class="a" style="background: ' + g + '">'""")
    assert STYLE_ATTR.search("<div style='color:red'>")
    assert STYLE_ATTR.search('<div STYLE="color:red">')
    assert STYLE_ATTR.search("<div style=color:red>")
    assert HANDLER_ATTR.search('<button onclick="doThing()">')
    assert HANDLER_ATTR.search("<button onChange='x()'>")
    assert HANDLER_ATTR.search("<button ONCLICK=go()>")
    assert HANDLER_ATTR.search("""'<a onclick="go(' + id + ')">'""")

    # Comment stripping must remove handler markup quoted in a comment...
    assert not HANDLER_ATTR.search(_strip_js_comments('// onclick="doThing()"'))
    # ...without hiding a real handler that follows code on the same line,
    # and without treating a URL as a comment.
    assert HANDLER_ATTR.search(
        _strip_js_comments("""x = '<a onclick="go()">'; // onclick="old()\"""")
    )
    assert HANDLER_ATTR.search(
        _strip_js_comments("""'<a href="https://x.test" onclick="go()">'""")
    )

    # And must NOT fire on the allowed forms.
    assert not STYLE_ATTR.search('<div class="shuffle-track-art--1">')
    assert not STYLE_ATTR.search('<div data-style="quiet">')
    assert not HANDLER_ATTR.search('<button data-action-click="card.open">')
    assert not HANDLER_ATTR.search("el.style.transform = 'translateY(0)'")


# The two attribute checks above say nothing about whether a page's scripts
# run: an external script needs its host in script-src, and an inline script
# needs the request nonce, which a static public page cannot carry. The checks
# below cover both.
SCRIPT_SRC_URL = re.compile(r"""<script[^>]*\bsrc\s*=\s*["'](https?://[^"']+)""", re.I)


class _ExecutableElements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "style" or (tag == "script" and "src" not in attrs):
            self.inline.append((tag, attrs.get("nonce")))


@pytest.mark.parametrize("path", sorted(TEMPLATE_DIR.rglob("*.html")), ids=lambda p: p.name)
def test_template_inline_elements_bind_the_request_nonce(path):
    elements = _ExecutableElements()
    elements.feed(path.read_text())
    assert all(nonce == "{{ csp_nonce }}" for _, nonce in elements.inline), _rel(path)


def test_csp_source_guard_covers_js_builders_and_unquoted_attributes(tmp_path):
    source = tmp_path / "builder.js"
    source.write_text('target.innerHTML = `<div STYLE=color:red ONCLICK=go()>x</div>`;')
    assert _offending_lines(source, STYLE_ATTR)
    assert _offending_lines(source, HANDLER_ATTR)
    assert JS_DIR / "routine-management.js" in _html_sources()


def test_csp_guard_cannot_hide_attributes_with_urls_comments_or_allowed_words(tmp_path):
    source = tmp_path / "builder.js"
    source.write_text(
        'target.innerHTML = `<a href="//cdn.test/path//part" only=yes '
        'data-note="/* literal */" STYLE=color:red ONCLICK=go()>x</a>`;'
    )
    assert _offending_lines(source, STYLE_ATTR)
    assert _offending_lines(source, HANDLER_ATTR)


@pytest.mark.parametrize("markup,pattern", [
    ('<div style\n="color:red">', STYLE_ATTR),
    ('<button onclick\n="go()">', HANDLER_ATTR),
    ('const markup = `<div STYLE\n=color:red>`;', STYLE_ATTR),
    ('const markup = `<button ONCLICK\n=go()>`;', HANDLER_ATTR),
])
def test_csp_attribute_guard_detects_multiline_mutations(tmp_path, markup, pattern):
    source = tmp_path / "builder.js"
    source.write_text(markup)
    assert _offending_lines(source, pattern)


@pytest.mark.parametrize("markup", [
    '<script>alert(1)</script>',
    '<script nonce="fixed-pretend-nonce">alert(1)</script>',
    '<script\n nonce="fixed-pretend-nonce">\nalert(1)\n</script>',
])
def test_static_script_guard_rejects_even_multiline_fixed_nonces(monkeypatch, tmp_path, markup):
    source = tmp_path / "public.html"
    source.write_text(markup)
    monkeypatch.setattr(__name__ + ".REPO_ROOT", tmp_path)
    with pytest.raises(AssertionError, match="inline <script>"):
        test_static_public_pages_have_no_inline_script(source)


def test_static_script_guard_allows_external_script(tmp_path):
    source = tmp_path / "public.html"
    source.write_text('<script\n src="/static/js/landing.js"></script>')
    test_static_public_pages_have_no_inline_script(source)


def test_rendered_inline_script_uses_the_response_csp_nonce(db_app):
    from flask import render_template

    @db_app.route("/test-design-nonce")
    def nonce_page():
        return render_template("errors/500.html")

    with db_app.test_client() as client:
        first = client.get("/test-design-nonce")
        second = client.get("/test-design-nonce")
    nonces = []
    for response in [first, second]:
        nonce = re.search(r"'nonce-([^']+)'", response.headers["Content-Security-Policy"])[1]
        elements = _ExecutableElements()
        elements.feed(response.get_data(as_text=True))
        assert elements.inline, "The retry registration must exercise an inline script"
        assert all(value == nonce for _, value in elements.inline)
        nonces.append(nonce)
    assert nonces[0] != nonces[1], "A fixed or reused nonce must fail the rendered guard"


def _csp_script_src_sources():
    """External sources the shipped policy permits, read from the policy source."""
    src = (REPO_ROOT / "shuffify" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'"script-src ([^"]+)"', src)
    assert m, (
        "could not locate the script-src directive in shuffify/__init__.py -- "
        "this check silently permits everything if that regex stops matching"
    )
    sources = {t for t in m.group(1).split() if t.startswith("http")}
    assert sources, "script-src names no external source; expected at least one"
    return sources


def _source_permits(source: str, url: str) -> bool:
    """CSP host-source matching, reduced to the forms this policy uses.

    A source with no path permits any file on its host (``*.`` subdomain
    wildcards included); a path ending in '/' permits that directory; any
    other path permits exactly that one file.
    """
    src, target = urlsplit(source), urlsplit(url)
    host, want = target.hostname or "", src.hostname or ""
    host_ok = host == want or (want.startswith("*.") and host.endswith(want[1:]))
    if src.scheme != target.scheme or not host_ok:
        return False
    if src.path in ("", "/"):
        return True
    if src.path.endswith("/"):
        return target.path.startswith(src.path)
    return target.path == src.path


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_external_scripts_are_permitted_by_script_src(path):
    """A <script src> host absent from script-src never executes."""
    allowed = _csp_script_src_sources()
    bad = []
    text = _scannable(path)
    for match in SCRIPT_SRC_URL.finditer(text):
        url = match[1]
        if not any(_source_permits(source, url) for source in allowed):
            bad.append((text.count("\n", 0, match.start()) + 1, url))
    assert not bad, (
        f"{_rel(path)} loads a script that script-src does not permit, "
        "so it is refused and never runs:\n"
        + "\n".join(f"  line {n}: {o}" for n, o in bad)
        + f"\nPermitted: {sorted(allowed)}. Either serve the asset from "
        "'self' at build time or add the host deliberately."
    )


@pytest.mark.parametrize(
    "path", sorted(PUBLIC_DIR.rglob("*.html")), ids=lambda p: p.name
)
def test_static_public_pages_have_no_inline_script(path):
    """These files are sent verbatim -- nothing can stamp a nonce into them.

    A template can carry `<script nonce="{{ csp_nonce }}">`. A static file has
    no render step, so every inline script in one is unconditionally refused.
    """
    elements = _ExecutableElements()
    elements.feed(path.read_text(encoding="utf-8"))
    bad = [nonce for tag, nonce in elements.inline if tag == "script"]
    assert not bad, (
        f"{_rel(path)} contains an inline <script> that CSP refuses; a static "
        "file cannot be given a nonce. Serve its script from a same-origin file."
    )
