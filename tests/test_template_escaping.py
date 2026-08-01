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

Kept as source assertions rather than DOM tests because the project has no JS
test harness; these are the cheapest gate that fails when the convention slips.
"""

import re
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
    offenders = list(
        _escape_calls_in_attribute_context(path.read_text(encoding="utf-8"))
    )
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
