"""The browser harness loads application scripts from its own origin only."""

from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "web" / "index.html"]


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.sources.append(dict(attrs).get("src"))


def test_every_application_script_is_external_and_same_origin():
    found = []
    for page in PAGES:
        parser = Scripts()
        parser.feed(page.read_text())
        for source in parser.sources:
            assert source and source.startswith("/") and not source.startswith("//"), (page.name, source)
            found.append(source)
    assert found, "The frontend entry must exercise the script boundary"
