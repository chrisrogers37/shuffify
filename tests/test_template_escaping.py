"""Runtime strings stay in React text/attributes; only trusted legal copy is HTML."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web" / "src"
LEGAL = SOURCE / "pages" / "public" / "LegalPage.tsx"
SINK = re.compile(
    r"\.(?:innerHTML|outerHTML)\s*=|\b(?:insertAdjacentHTML|createContextualFragment|DOMParser)\b|document\.write"
)


def test_react_runtime_strings_cannot_enter_html_parsers():
    files = sorted(SOURCE.rglob("*.ts")) + sorted(SOURCE.rglob("*.tsx"))
    assert files
    raw_html = []
    for source in files:
        text = source.read_text()
        assert not SINK.search(text), source.relative_to(ROOT)
        if "dangerouslySetInnerHTML" in text:
            raw_html.append(source)
    assert raw_html == [LEGAL]
    legal = LEGAL.read_text()
    assert 'import { legalDocuments } from "./generated/legal"' in legal
    assert 'document: "terms" | "privacy"' in legal


@pytest.mark.parametrize("mutation", [
    "element.innerHTML = payload", "element.outerHTML = payload", "element.insertAdjacentHTML('beforeend', payload)",
    "document.createRange().createContextualFragment(payload)", "new DOMParser()", "document.write(payload)",
])
def test_markup_sink_guard_detects_runtime_parser_mutations(mutation):
    assert SINK.search(mutation)


@pytest.mark.parametrize("name", ['Mix <img src=x onerror=alert(1)> & friends', '\"><script>alert(1)</script>'])
def test_hostile_profile_names_remain_plain_json_session_data(auth_client, name):
    with auth_client.session_transaction() as session:
        session["user_data"]["display_name"] = name
    response = auth_client.get("/api/session")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.json["user"]["display_name"] == name
