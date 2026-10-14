"""Guard the design boundary and silent failures in shared presentation."""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_DIR = REPO_ROOT / "assets" / "design"


def test_design_css_has_one_palette_source():
    """A page composition must not acquire its own literal color palette."""
    literal_color = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(")
    for source in DESIGN_DIR.rglob("*.css"):
        # Comments may describe the palette without defining a second one.
        css = re.sub(r"/\*.*?\*/", "", source.read_text(), flags=re.DOTALL)
        assert not literal_color.search(css), source.relative_to(REPO_ROOT)


def test_every_core_variable_reference_exists_in_compiled_css():
    """Unknown custom properties silently discard styling in the browser."""
    compiled = (REPO_ROOT / "shuffify/static/css/tailwind.css").read_text()
    defined = set(re.findall(r"(--sh-[\w-]+)\s*:", compiled))
    assert defined, "The design adapter must emit its variables into the bundle"
    for source in DESIGN_DIR.rglob("*.css"):
        referenced = set(re.findall(r"var\(\s*(--sh-[\w-]+)", source.read_text()))
        assert referenced <= defined, (source.name, sorted(referenced - defined))


def test_core_styles_cannot_be_redefined_in_templates():
    """Presentation belongs in the core module, rather than page style blocks."""
    for source in (REPO_ROOT / "shuffify/templates").rglob("*.html"):
        for style in re.findall(r"<style\b[^>]*>(.*?)</style>", source.read_text(), re.DOTALL):
            assert not re.search(r"\.sh-[\w-]+|--sh-[\w-]+\s*:", style), source.name


def test_error_state_is_pure_and_autoescapes_every_input():
    environment = Environment(
        loader=FileSystemLoader(REPO_ROOT / "shuffify/templates"),
        autoescape=True,
        undefined=StrictUndefined,
    )
    macro = environment.get_template("macros/design_states.html").module.error_panel
    hostile = '\"><script>alert(1)</script>'
    html = macro(hostile, hostile, hostile, hostile, retry=True)
    assert "<script>" not in html
    assert html.count("&lt;script&gt;") == 4
    assert 'data-action-click="err-001"' in html
