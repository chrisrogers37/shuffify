"""Static HTML and backend responses enforce a strict external-script CSP."""

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# A <style> block's CSS body can mention `style="..."` in prose. Strip those
# regions for attribute scanning; the executable-element guard checks the
# block itself. <script> blocks are NOT stripped: a
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
    return [REPO_ROOT / "web" / "index.html"]


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


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_style_attribute(path):
    """`style="..."` is inert under this CSP."""
    hits = _offending_lines(path, STYLE_ATTR)
    assert not hits, (
        f"{_rel(path)} declares style via an attribute, "
        "which CSP drops silently:\n"
        + "\n".join(f"  line {n}: {t}" for n, t in hits)
        + "\nPut the declaration in the external stylesheet as a class, or "
        "set it from JS (el.style.x / classList) — the CSSOM path is allowed."
    )


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_inline_event_handler(path):
    """Inline `on*=` handlers are inert under this CSP."""
    hits = _offending_lines(path, HANDLER_ATTR)
    assert not hits, (
        f"{_rel(path)} declares an inline event handler, "
        "which CSP drops silently:\n"
        + "\n".join(f"  line {n}: {t}" for n, t in hits)
        + "\nUse React event handlers instead of HTML event attributes."
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
# run: an external script needs its host in script-src, and the policy refuses
# inline scripts. The checks below cover both.
SCRIPT_SRC_URL = re.compile(r"""<script[^>]*\bsrc\s*=\s*["'](https?://[^"']+)""", re.I)


class _ExecutableElements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "style" or (tag == "script" and "src" not in attrs):
            self.inline.append((tag, attrs.get("nonce")))


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_static_documents_have_no_inline_executable_elements(path):
    elements = _ExecutableElements()
    elements.feed(path.read_text())
    assert not elements.inline, _rel(path)


def test_csp_source_guard_covers_js_builders_and_unquoted_attributes(tmp_path):
    source = tmp_path / "builder.js"
    source.write_text('target.innerHTML = `<div STYLE=color:red ONCLICK=go()>x</div>`;')
    assert _offending_lines(source, STYLE_ATTR)
    assert _offending_lines(source, HANDLER_ATTR)


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
    source = tmp_path / "index.html"
    source.write_text(markup)
    monkeypatch.setattr(__name__ + ".REPO_ROOT", tmp_path)
    with pytest.raises(AssertionError, match="index.html"):
        test_static_documents_have_no_inline_executable_elements(source)


def test_static_script_guard_allows_external_script(tmp_path):
    source = tmp_path / "index.html"
    source.write_text('<script\n src="/assets/app.js"></script>')
    test_static_documents_have_no_inline_executable_elements(source)


def test_backend_csp_does_not_authorize_inline_script_or_style(db_app):
    response = db_app.test_client().get("/api/session")
    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy and "style-src 'self'" in policy
    assert "'nonce-" not in policy and "'unsafe-inline'" not in policy


def _csp_script_src_sources():
    """External sources the shipped policy permits, read from the policy source."""
    src = (REPO_ROOT / "shuffify" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'"script-src ([^"]+)"', src)
    assert m, (
        "could not locate the script-src directive in shuffify/__init__.py -- "
        "this check silently permits everything if that regex stops matching"
    )
    sources = {t for t in m.group(1).split() if t.startswith("http")}
    assert "'self'" in m.group(1).rstrip("; ").split()
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
