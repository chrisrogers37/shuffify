"""Guard the design boundary and silent failures in shared presentation."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_DIR = REPO_ROOT / "assets" / "design"
# Committed output of the frontend theme generator; `npm run theme:check` keeps it
# in step with assets/design/tokens.json.
THEME_CSS = REPO_ROOT / "web" / "src" / "design" / "generated" / "theme.css"


def test_design_css_has_one_palette_source():
    """A page composition must not acquire its own literal color palette."""
    literal_color = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(")
    for source in DESIGN_DIR.rglob("*.css"):
        # Comments may describe the palette without defining a second one.
        css = re.sub(r"/\*.*?\*/", "", source.read_text(), flags=re.DOTALL)
        assert not literal_color.search(css), source.relative_to(REPO_ROOT)


def test_every_core_variable_reference_exists_in_generated_theme():
    """Unknown custom properties silently discard styling in the browser."""
    generated = THEME_CSS.read_text()
    defined = set(re.findall(r"(--sh-[\w-]+)\s*:", generated))
    assert defined, "The theme generator must emit the design variables into theme.css"
    for source in DESIGN_DIR.rglob("*.css"):
        referenced = set(re.findall(r"var\(\s*(--sh-[\w-]+)", source.read_text()))
        assert referenced <= defined, (source.name, sorted(referenced - defined))
