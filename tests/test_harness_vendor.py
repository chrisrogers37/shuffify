"""Every script a page loads from another origin is pinned by subresource
integrity, with an algorithm browsers enforce, and has a vendored copy for
the harness's browser with exactly the pinned bytes."""

import base64
import hashlib
from html.parser import HTMLParser
from pathlib import Path

from harness.vendor import CDN_COPIES

SHUFFIFY = Path(__file__).resolve().parent.parent / "shuffify"
PAGES = [*(SHUFFIFY / "templates").rglob("*.html"), *(SHUFFIFY / "static" / "public").rglob("*.html")]
# The ones browsers check. Any other name (md5, sha1, SHA384, ...) is an
# unknown algorithm to Chromium, which then loads the script unchecked.
SRI_ALGORITHMS = {"sha256", "sha384", "sha512"}


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.tags.append(dict(attrs))


def cdn_scripts() -> list:
    """(page, src, integrity) for every script tag whose src is another origin."""
    found = []
    for page in PAGES:
        parser = _Scripts()
        parser.feed(page.read_text())
        for attrs in parser.tags:
            src = attrs.get("src") or ""
            if src.startswith(("https://", "http://", "//")):
                found.append((page.name, src, attrs.get("integrity")))
    return found


def test_every_cdn_script_is_pinned_and_vendored_byte_for_byte():
    scripts = cdn_scripts()
    assert scripts, "no CDN script found: has the template's markup changed?"
    assert {src for _, src, _ in scripts} == set(CDN_COPIES), "vendor a copy of each CDN script into harness/vendor/"
    for page, src, integrity in scripts:
        assert integrity, f"{page}: {src} needs an integrity attribute"
        algorithm, _, expected = integrity.partition("-")
        assert algorithm in SRI_ALGORITHMS, f"{page}: browsers do not check an {algorithm!r} integrity hash"
        digest = base64.b64encode(hashlib.new(algorithm, CDN_COPIES[src].read_bytes()).digest()).decode()
        assert digest == expected, f"{page}: {CDN_COPIES[src]} is not the file {src} pins"
