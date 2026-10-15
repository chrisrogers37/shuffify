"""Keep every page and dynamic builder on the shared After Hours design system."""

import os
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask import flash, render_template, session

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "shuffify/templates"
STATIC = REPO_ROOT / "shuffify/static"
DESIGN = REPO_ROOT / "assets/design"

# Only these named outputs are exempt from handwritten-source checks. The
# theme generator's --check owns the two React outputs and rejects stale,
# missing and obsolete output; no directory-wide generated exemption exists.
GENERATED_CSS = {
    Path("shuffify/static/css/tailwind.css"),
    Path("web/src/design/generated/theme.css"),
    Path("web/src/design/generated/recipes.css"),
}
REACT_RECIPE_DIRECTORIES = (Path("web/src/pages"), Path("web/src/components"))
CSS_DECLARATION = re.compile(r"(?:^|[;{\s])([\w-]+)\s*:\s*([^;{}]+)")
SHARED_VARIABLE = re.compile(r"var\(\s*--sh-[\w-]+\s*\)")
PALETTE_PROPERTY = re.compile(
    r"color|background(?:-color|-image)?|(?:border|outline)(?:-(?:top|right|bottom|left))?(?:-color)?|"
    r"box-shadow|text-shadow|fill|stroke", re.I
)
PALETTE_SYNTAX = {
    "none", "transparent", "currentcolor", "inherit", "initial", "unset", "revert", "revert-layer",
    "solid", "dashed", "dotted", "double", "groove", "ridge", "inset", "outset", "auto",
    "linear-gradient", "radial-gradient", "conic-gradient", "repeating-linear-gradient",
    "repeating-radial-gradient", "repeating-conic-gradient", "to", "top", "right", "bottom", "left",
    "at", "center", "circle", "ellipse", "closest-side", "closest-corner", "farthest-side", "farthest-corner",
    "calc", "min", "max", "clamp",
}


COMMENTS = re.compile(r"/\*.*?\*/|<!--.*?-->|\{#.*?#\}", re.S)
STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.I | re.S)
LITERAL_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(", re.I)
VISUAL_PROPERTY = re.compile(
    r"(?:^|[;{\s])(?:--[\w-]+|(?:background|border|outline|font|text-shadow|"
    r"box-shadow|color|fill|stroke|line-height|letter-spacing|transition|animation)"
    r"(?:-[\w-]+)?)\s*:", re.I
)
CORE_DEFINITION = re.compile(r"\.sh-[\w-]+|--sh-[\w-]+\s*:")
VISUAL_UTILITY = re.compile(
    r"^(?:bg|from|via|to|font|rounded|shadow|border|outline|ring|"
    r"transition|duration|ease|animate)-|^(?:border|shadow|rounded|ring|transition)$"
)
TEXT_LAYOUT = {"text-left", "text-center", "text-right", "text-justify",
               "text-start", "text-end", "text-wrap", "text-nowrap",
               "text-balance", "text-pretty"}


def css_violations(text):
    css = COMMENTS.sub("", text)
    return [name for name, pattern in (
        ("literal palette", LITERAL_COLOR),
        ("visual recipe outside core", VISUAL_PROPERTY),
        ("core redefinition", CORE_DEFINITION),
    ) if pattern.search(css)]


def react_css_violations(text):
    """Component recipes may compose tokens, but cannot supply new palette or font sources."""
    css = COMMENTS.sub("", text)
    violations = []
    if LITERAL_COLOR.search(css):
        violations.append("literal palette")
    if re.search(r"--[\w-]+\s*:", css):
        violations.append("token definition outside generator")
    if re.search(r"@font-face\b", css, re.I):
        violations.append("font source outside generator")
    for property_name, value in CSS_DECLARATION.findall(css):
        property_name = property_name.lower()
        value = re.sub(r"\s*!important\s*$", "", value, flags=re.I).strip()
        if property_name in {"font", "font-family"}:
            if not re.fullmatch(
                r"var\(\s*--sh-font-[\w-]+\s*\)|inherit|initial|unset|revert|revert-layer", value, re.I
            ):
                violations.append("hardcoded font")
        if PALETTE_PROPERTY.fullmatch(property_name):
            # Remove only exact shared references: a literal fallback or local
            # custom property must remain visible to the palette check.
            remaining = SHARED_VARIABLE.sub("", value)
            words = re.findall(r"(?<![\w-])[a-zA-Z][\w-]*", remaining)
            if set(word.lower() for word in words) - PALETTE_SYNTAX:
                violations.append("palette outside shared tokens")
    return violations


def visual_utilities(text):
    # Inspect class tokens even inside JS HTML builders and Jinja alternatives.
    tokens = re.findall(r"[\w:/\[\]#().%-]+", COMMENTS.sub("", text))
    bad = []
    for token in tokens:
        utility = token.split(":")[-1]
        if VISUAL_UTILITY.search(utility) or (
            utility.startswith("text-") and utility not in TEXT_LAYOUT
        ):
            bad.append(token)
    return bad


class PublicMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stylesheets = []
        self.scripts = []
        self.classes = []
        self.inline_styles = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.classes.extend(attrs.get("class", "").split())
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.stylesheets.append(attrs.get("href"))
        if tag == "script" and "src" in attrs:
            self.scripts.append(attrs["src"])
        self._in_style = tag == "style" or self._in_style

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.inline_styles.append(data)


def public_violations(html):
    markup = PublicMarkup()
    markup.feed(html)
    violations = visual_utilities(" ".join(markup.classes))
    violations += css_violations("\n".join(markup.inline_styles))
    if markup.stylesheets != ["/static/css/tailwind.css"]:
        violations.append(f"non-core stylesheet: {markup.stylesheets}")
    if set(markup.scripts) - {
        "/static/js/actions.js", "/static/js/base.js", "/static/js/landing.js"
    }:
        violations.append(f"non-public script: {markup.scripts}")
    return violations


def test_templates_and_static_builders_cannot_define_migrated_recipes():
    sources = list(TEMPLATES.rglob("*.html"))
    sources += list((REPO_ROOT / "assets").rglob("*.html"))
    sources += list(DESIGN.glob("templates/*.j2"))
    sources += list((STATIC / "public").rglob("*.html"))
    sources += [REPO_ROOT / "scripts/build-public-pages.py"]
    for path in sources:
        text = path.read_text()
        for style in STYLE_BLOCK.findall(text):
            assert not CORE_DEFINITION.search(style), path
            assert not css_violations(style), path
        assert not visual_utilities(text), (path, visual_utilities(text))
        # CSS literals/font assignments embedded by a static builder also
        # fall outside core, even if no <style> tag encloses them.
        assert not LITERAL_COLOR.search(COMMENTS.sub("", text)), path
        assert not re.search(r"font-family\s*:|fontFamily\s*[=:]", text), path


def test_generic_css_cannot_add_palette_fonts_or_component_recipes():
    for directory, children, files in os.walk(REPO_ROOT):
        # Do not inspect third-party packages, local build caches, the
        # harness's ignored state (.harness) or nested checkouts (a directory
        # with its own .git, such as a submodule or a clone): Git does not
        # track their files here, so they are not this checkout's CSS. The
        # walk still discovers new repository CSS outside the assets/static
        # folders.
        children[:] = [name for name in children if name not in {
            ".git", ".harness", ".pytest_cache", "__pycache__", "node_modules", "build", "dist"
        } and not re.match(r"(?:\.?venv|env|virtualenv)(?:-|$)", name)
            and not (Path(directory) / name / ".git").exists()]
        for name in files:
            path = Path(directory) / name
            if not name.endswith(".css") or path.is_relative_to(DESIGN):
                continue
            relative = path.relative_to(REPO_ROOT)
            if relative in GENERATED_CSS:
                continue
            react_recipe = relative == Path("web/src/design/index.css") or any(
                relative.is_relative_to(directory) for directory in REACT_RECIPE_DIRECTORIES
            )
            checker = react_css_violations if react_recipe else css_violations
            violations = checker(path.read_text())
            assert not violations, (path, violations)


def test_static_js_cannot_build_new_visual_recipes_outside_core():
    for path in (STATIC / "js").rglob("*.js"):
        text = COMMENTS.sub("", path.read_text())
        assert not visual_utilities(text), (path, visual_utilities(text))
        assert not LITERAL_COLOR.search(text), path
        assert not re.search(
            r"(?:\.style\.(?:background\w*|color|font\w*|border\w*|boxShadow)|"
            r"fontFamily|\.style\.cssText)\s*=|"
            r"\.style\.setProperty\(\s*['\"](?:--sh-|color|background|font|border)",
            text
        ), path
        assert not VISUAL_PROPERTY.search(text), path
        for style in STYLE_BLOCK.findall(text):
            assert not css_violations(style), path


@pytest.mark.parametrize("token", [None, {"malformed": "nonempty"}])
def test_landing_intent_wins_over_token_presence(db_app, token):
    with db_app.test_client() as client:
        if token:
            with client.session_transaction() as stored:
                stored["spotify_token"] = token
        response = client.get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<html lang="en" class="sh-document">' in html
    assert '<body class="sh-shell sh-public-shell">' in html
    assert 'aria-label="Public navigation"' in html
    assert 'data-action-click="ui.settings-sidebar-open"' not in html
    assert not public_violations(html)


def test_authenticated_dashboard_uses_shared_shell(db_app):
    with db_app.test_request_context("/"):
        session["spotify_token"] = {
            "access_token": "valid-fixture", "refresh_token": "fixture-refresh",
            "expires_at": 4102444800, "token_type": "Bearer",
        }
        html = render_template(
            "dashboard.html", user={"id": "fixture", "display_name": "Fixture"},
            playlists=[], favorite_playlists=[], hidden_playlists=[], algorithms=[],
            dashboard={}, preferences={},
        )
    assert '<body class="sh-shell sh-app-shell">' in html
    assert 'href="/static/css/base.css"' not in html
    assert 'aria-label="Main navigation"' in html
    assert 'href="/settings"' in html
    assert 'id="settings-sidebar"' not in html
    assert 'src="/static/js/app-feedback.js"' in html
    # Page registrations run while content is parsed, before footer scripts.
    assert html.index('src="/static/js/app-panels.js"') < html.index("</head>")
    assert 'aria-label="Public navigation"' not in html
    assert '<html lang="en" class="sh-document">' in html


@pytest.mark.parametrize("template", ["error", "404", "500"])
@pytest.mark.parametrize("token", [None, {"access_token": "valid-fixture"},
                                   {"malformed": "nonempty"}])
def test_errors_share_presentation_and_preserve_session_navigation(db_app, template, token):
    with db_app.test_request_context("/missing"):
        if token:
            session["spotify_token"] = token
        flash("Fixture notice", "error")
        html = render_template(f"errors/{template}.html", error_code=400,
                               error_title="Fixture", error_message="Fixture message")
    if token:
        assert '<body class="sh-shell sh-app-shell">' in html
        assert 'href="/static/css/base.css"' not in html
        assert 'aria-label="Main navigation"' in html
        assert 'href="/settings"' in html
        assert 'id="settings-sidebar"' not in html
        assert 'aria-label="Public navigation"' not in html
        assert 'class="sh-toast sh-toast-error fade-out"' in html
    else:
        assert '<body class="sh-shell sh-public-shell">' in html
        assert 'aria-label="Public navigation"' in html
        assert 'class="sh-toast sh-toast-error fade-out"' in html
        assert not public_violations(html)
    if template == "500":
        assert 'data-action-click="err-001"' in html
        assert "window.location.reload()" in html


@pytest.mark.parametrize("page", ["terms", "privacy"])
def test_legal_surfaces_use_the_same_public_bundle(page):
    html = (STATIC / "public" / f"{page}.html").read_text()
    assert 'class="sh-document"' in html
    assert not public_violations(html)


@pytest.mark.parametrize("mutation", [
    '<style>button { background: var(--sh-accent); }</style>',
    '<style>.fresh { font-family: "Other Font"; }</style>',
    '<style>body { color: #ff0000; }</style>',
    '<link rel="stylesheet" href="/static/css/extra.css">',
    '<button class="bg-red-500 font-bold rounded-lg">New recipe</button>',
])
def test_public_guard_detects_in_memory_mutations(mutation):
    html = '<link rel="stylesheet" href="/static/css/tailwind.css">' + mutation
    assert public_violations(html)


def test_composition_layout_is_permitted():
    assert not css_violations('.composition { display: grid; gap: 10px; width: 100%; }')
    assert not visual_utilities('sh-button flex flex-col lg:flex-row gap-4 text-center')


@pytest.mark.parametrize("source", [
    'node.innerHTML = `<button class="bg-red-500">Go</button>`;',
    'node.style.fontFamily = "Different Font";',
    'node.style.setProperty("color", "red");',
    'const markup = `<style>button { background: var(--sh-accent); }</style>`;',
])
def test_js_builder_guard_detects_new_source_files(monkeypatch, tmp_path, source):
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "new-public-builder.js").write_text(source)
    monkeypatch.setattr(__name__ + ".STATIC", tmp_path)
    with pytest.raises(AssertionError):
        test_static_js_cannot_build_new_visual_recipes_outside_core()


def test_generic_css_guard_detects_new_source_files(monkeypatch, tmp_path):
    (tmp_path / "new-theme.css").write_text('button { background: red; font-size: 18px; }')
    monkeypatch.setattr(__name__ + ".REPO_ROOT", tmp_path)
    with pytest.raises(AssertionError):
        test_generic_css_cannot_add_palette_fonts_or_component_recipes()
