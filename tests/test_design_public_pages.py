"""Shared public shell, preserved legal copy, and committed artifact contracts."""

import importlib.util
import shutil
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = REPO_ROOT / "shuffify" / "static" / "public"
COPY_DIR = REPO_ROOT / "assets" / "legal"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-public-pages.py"
# Independent baselines of the legal-copy regions: updating the generator
# cannot bless a legal-text change by merely rebuilding its outputs.
LEGAL_COPY_SHA256 = {
    "terms": "9f6afc24adeb83886365c5c3a7dce18125493f28f6d534590f5e6b8022224b14",
    "privacy": "f7c3f3e0c978c23f374b2d65e097af793352cf8769649aebdc70b1c0474c10a4",
}


@pytest.fixture(scope="module")
def public_page_builder():
    spec = importlib.util.spec_from_file_location("build_public_pages", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def public_source_tree(tmp_path):
    """A standalone build tree, without a Flask app or unrelated templates."""
    shutil.copytree(REPO_ROOT / "assets" / "legal", tmp_path / "assets" / "legal")
    shutil.copytree(
        REPO_ROOT / "assets" / "design" / "templates",
        tmp_path / "assets" / "design" / "templates",
    )
    macros = tmp_path / "shuffify" / "templates" / "macros"
    macros.mkdir(parents=True)
    shutil.copyfile(
        REPO_ROOT / "shuffify" / "templates" / "macros" / "design.html",
        macros / "design.html",
    )
    shutil.copytree(PUBLIC_DIR, tmp_path / "shuffify" / "static" / "public")
    return tmp_path


def _legal_copy(page: bytes) -> bytes:
    opening = b'            <div class="legal-copy">'
    following = b'            <div class="legal-back">'
    assert page.count(opening) == 1
    assert page.count(following) == 1
    return page[page.index(opening):page.index(following)]


def test_committed_public_pages_match_shared_sources():
    """The test suite gates generation drift: committed pages must match their sources."""
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", LEGAL_COPY_SHA256)
def test_legal_copy_remains_byte_for_byte_identical(name):
    expected = LEGAL_COPY_SHA256[name]
    source = (COPY_DIR / f"{name}.html").read_bytes()
    generated = (PUBLIC_DIR / f"{name}.html").read_bytes()
    assert sha256(source).hexdigest() == expected
    assert sha256(_legal_copy(generated)).hexdigest() == expected


@pytest.mark.parametrize("name", LEGAL_COPY_SHA256)
def test_legal_route_serves_committed_html_verbatim(db_app, name):
    with db_app.test_client() as client:
        response = client.get(f"/{name}")
        assert response.status_code == 200
        assert response.content_type.startswith("text/html")
        assert response.data == (PUBLIC_DIR / f"{name}.html").read_bytes()


@pytest.mark.parametrize(
    "name,title", [("terms", "Terms of Service"), ("privacy", "Privacy Policy")]
)
def test_legal_page_has_preserved_metadata_and_static_assets(name, title):
    page = (PUBLIC_DIR / f"{name}.html").read_text(encoding="utf-8")
    assert f"<title>{title} - Shuffify</title>" in page
    assert f'<h1 id="legal-title">{title}</h1>' in page
    assert 'class="sh-shell sh-public-shell"' in page
    assert f'href="/{name}" aria-current="page"' in page
    assert "© 2026 Shuffify. All rights reserved." in page
    assert 'href="/" class="sh-button-secondary"' in page
    assert page.count('rel="stylesheet"') == 1
    assert 'href="/static/css/tailwind.css"' in page
    assert "<script" not in page
    assert "csrf" not in page
    assert "{{" not in page
    assert "{%" not in page


def test_shell_macros_are_pure_and_autoescape_inputs():
    environment = Environment(
        loader=FileSystemLoader(REPO_ROOT / "shuffify" / "templates"),
        undefined=StrictUndefined,
        autoescape=True,
    )
    macros = environment.get_template("macros/design.html").module
    hostile = '\"><script>alert(1)</script>'
    for html in (
        macros.brand(hostile),
        macros.skip_link(hostile),
        macros.public_header(hostile, hostile, hostile, "terms"),
        macros.public_footer(hostile, hostile, hostile, "privacy"),
    ):
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


def test_legal_copy_is_literal_markup_not_a_jinja_template(
    public_page_builder, public_source_tree
):
    fragment = public_source_tree / "assets" / "legal" / "terms.html"
    fragment.write_text(
        '<p>{{ title }} {% include "unavailable.html" %}</p>\n', encoding="utf-8"
    )
    page = public_page_builder.render_public_pages(public_source_tree)["terms"]
    assert '<p>{{ title }} {% include "unavailable.html" %}</p>' in page


def test_check_detects_drift_without_rewriting_artifacts(
    public_page_builder, public_source_tree, monkeypatch, capsys
):
    artifact = public_source_tree / "shuffify" / "static" / "public" / "terms.html"
    artifact.write_bytes(b"stale committed page\n")
    monkeypatch.setattr(public_page_builder, "REPO_ROOT", public_source_tree)
    assert public_page_builder.main(["--check"]) == 1
    assert artifact.read_bytes() == b"stale committed page\n"
    assert "terms.html" in capsys.readouterr().err


def test_check_detects_a_missing_artifact(
    public_page_builder, public_source_tree, monkeypatch, capsys
):
    artifact = public_source_tree / "shuffify" / "static" / "public" / "privacy.html"
    artifact.unlink()
    monkeypatch.setattr(public_page_builder, "REPO_ROOT", public_source_tree)
    assert public_page_builder.main(["--check"]) == 1
    assert not artifact.exists()
    assert "privacy.html" in capsys.readouterr().err
