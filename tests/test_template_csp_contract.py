"""What CSP forbids in templates — both halves of the #372 nonce migration.

A CSP nonce authorises an **element**, never an **attribute**. `style-src` and
`script-src` carry a nonce and no `unsafe-inline`, so two things are inert no
matter how correct they look in the markup:

* `on*` event handler attributes  — migrated in #499 (114 of them)
* `style` attributes              — migrated in #537 (12, blank for ~10 weeks)

Neither half had a guard. #499's 114 handlers were unprotected against
reintroduction, and #537 happened because nobody looked at the style half of
the same defect. This is one contract for both, so the next person who adds
either one finds out from a test rather than from the live site.

The failure mode being guarded is silent by construction: the browser drops
the declaration and renders on. Nothing errors, nothing logs, and the page
looks plausible — the eight hero thumbnails were blank on the public landing
page for ten weeks before anyone noticed.

**The JS/CSSOM path is deliberately allowed.** `el.style.transform = '...'`
and `el.classList.add(...)` are not blocked and are the supported way to style
dynamically. Only the *attribute* is forbidden — including one written into an
HTML string that later reaches `innerHTML`, which is how the gradients got in.
"""

import re
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "shuffify" / "templates"

# A <style> block is nonced and legitimate, and its CSS body can mention
# `style="..."` in prose. Strip those regions before scanning so documentation
# of the rule does not trip the rule. <script> blocks are NOT stripped: a
# `style=` written into an HTML string there becomes a real attribute the
# moment it is assigned to innerHTML.
STYLE_BLOCK = re.compile(r"<style\b.*?</style>", re.DOTALL | re.IGNORECASE)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

STYLE_ATTR = re.compile(r"""\bstyle\s*=\s*["'\\]""")

# `on` + letters, immediately followed by `=`. Excludes data-on-*, and words
# such as `onchange_handler` because `=` must follow directly.
HANDLER_ATTR = re.compile(r"""\son[a-z]+\s*=\s*["'\\]""", re.IGNORECASE)

# Legitimate `on*=` spellings that are not handler attributes.
HANDLER_ALLOWED = re.compile(r"""\b(only|once|onto)\s*=""", re.IGNORECASE)

# #499 left `// was onclick="..."` breadcrumbs recording what each migrated
# handler used to be. Those are documentation, not handlers, and stripping
# them is why this guard reports zero on templates that are genuinely clean.
# `://` is excluded so a URL in a string is not mistaken for a comment start,
# which would truncate the line and could hide a real violation after it.
JS_LINE_COMMENT = re.compile(r"(?<!:)//.*$")
JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(text: str) -> str:
    text = JS_BLOCK_COMMENT.sub("", text)
    return "\n".join(JS_LINE_COMMENT.sub("", ln) for ln in text.splitlines())


def _templates():
    return sorted(TEMPLATE_DIR.rglob("*.html"))


def _scannable(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = STYLE_BLOCK.sub("", text)
    text = HTML_COMMENT.sub("", text)
    text = _strip_js_comments(text)
    return text


def _offending_lines(path: Path, pattern: re.Pattern):
    hits = []
    for lineno, line in enumerate(_scannable(path).splitlines(), start=1):
        if pattern.search(line) and not HANDLER_ALLOWED.search(line):
            hits.append((lineno, line.strip()[:120]))
    return hits


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_style_attribute(path):
    """`style="..."` is inert under this CSP — the #537 defect."""
    hits = _offending_lines(path, STYLE_ATTR)
    assert not hits, (
        f"{path.relative_to(TEMPLATE_DIR)} declares style via an attribute, "
        "which CSP drops silently:\n"
        + "\n".join(f"  line {n}: {t}" for n, t in hits)
        + "\nPut the declaration in the nonced <style> block as a class, or "
        "set it from JS (el.style.x / classList) — the CSSOM path is allowed."
    )


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_inline_event_handler(path):
    """Inline `on*=` handlers are inert under this CSP — the #499 defect."""
    hits = _offending_lines(path, HANDLER_ATTR)
    assert not hits, (
        f"{path.relative_to(TEMPLATE_DIR)} declares an inline event handler, "
        "which CSP drops silently:\n"
        + "\n".join(f"  line {n}: {t}" for n, t in hits)
        + '\nUse the delegated dispatcher: data-action-click="..." plus a '
        "handler registered in static/js/actions.js (#499)."
    )


def test_the_guard_can_actually_see_a_violation():
    """The patterns must match real violations, not merely fail to fire.

    A contract test that cannot fail is worse than no contract test: it
    reports safety it is not checking. These are the exact spellings that
    were live on the landing page.
    """
    assert STYLE_ATTR.search('<div class="x" style="background: red;">')
    assert STYLE_ATTR.search("""'<div class="a" style="background: ' + g + '">'""")
    assert STYLE_ATTR.search("<div style='color:red'>")
    assert HANDLER_ATTR.search('<button onclick="doThing()">')
    assert HANDLER_ATTR.search("<button onChange='x()'>")
    assert HANDLER_ATTR.search("""'<a onclick="go(' + id + ')">'""")

    # Comment stripping must remove #499's breadcrumbs...
    assert not HANDLER_ATTR.search(_strip_js_comments('// was onclick="doThing()"'))
    # ...without hiding a real handler that follows code on the same line,
    # and without treating a URL as a comment.
    assert HANDLER_ATTR.search(
        _strip_js_comments("""x = '<a onclick="go()">'; // was onclick="old()\"""")
    )
    assert HANDLER_ATTR.search(
        _strip_js_comments("""'<a href="https://x.test" onclick="go()">'""")
    )

    # And must NOT fire on the allowed forms.
    assert not STYLE_ATTR.search('<div class="shuffle-track-art--1">')
    assert not HANDLER_ATTR.search('<button data-action-click="card.open">')
    assert not HANDLER_ATTR.search("el.style.transform = 'translateY(0)'")
