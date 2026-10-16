"""React recipes share palette/font tokens while legacy CSS stays composition-only."""

import pytest

from tests import test_design_surface_contract as contract

REACT_RECIPES = [
    "web/src/design/index.css",
    "web/src/pages/example/example.css",
    "web/src/components/example/example.css",
]


def check_source(monkeypatch, tmp_path, path, css):
    source = tmp_path / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(css)
    monkeypatch.setattr(contract, "REPO_ROOT", tmp_path)
    contract.test_generic_css_cannot_add_palette_fonts_or_component_recipes()


@pytest.mark.parametrize("path", REACT_RECIPES)
def test_react_core_and_components_can_compose_shared_token_recipes(monkeypatch, tmp_path, path):
    check_source(monkeypatch, tmp_path, path, '''
        .sh-example { color: var(--sh-ink); background: var(--sh-surface);
          border: 1px solid var(--sh-border); font-family: var(--sh-font-ui);
          font-weight: var(--sh-weight-semibold); box-shadow: var(--sh-shadow-panel); }
    ''')


@pytest.mark.parametrize("css", [
    ".sh-example { color: #f00; }",
    ".sh-example { background: rgb(255 0 0); }",
    ".sh-example { border: 1px solid hsl(0 100% 50%); }",
    ".sh-example { color: oklch(50% 0.2 10); }",
    ".sh-example { color: red; }",
    ".sh-example { background: linear-gradient(red, blue); }",
    ".sh-example { color: var(--sh-ink, red); }",
    '.sh-example { font-family: "Different Font", sans-serif; }',
    '.sh-example { font: 16px "Different Font"; }',
    ".sh-example { font-family: var(--brand-font); }",
    '.sh-example { font-family: var(--sh-font-ui, "Different Font"); }',
    ":root { --sh-ink: red; }",
    ".sh-example { --local-color: red; color: var(--local-color); }",
    '@font-face { font-family: var(--sh-font-ui); src: url("/new-font.woff2"); }',
])
@pytest.mark.parametrize("path", REACT_RECIPES)
def test_react_recipe_mutations_cannot_add_palette_fonts_or_tokens(monkeypatch, tmp_path, path, css):
    with pytest.raises(AssertionError):
        check_source(monkeypatch, tmp_path, path, css)


@pytest.mark.parametrize("path", REACT_RECIPES)
def test_react_recipe_can_use_semantic_resets_and_current_color(monkeypatch, tmp_path, path):
    check_source(monkeypatch, tmp_path, path, '''
        .sh-example { border: 0; outline: none; background: transparent;
          color: inherit; border-color: currentColor; font-family: inherit; font: inherit; }
        @media (prefers-reduced-motion: reduce) { .sh-example { transition: none; animation: none; } }
    ''')


@pytest.mark.parametrize("path", [
    "new-theme.css", "shuffify/static/css/new-theme.css", "web/public/new-theme.css",
    "web/src/design/rogue.css", "web/src/design/generated/extra.css",
])
def test_other_css_does_not_acquire_react_recipe_permission(monkeypatch, tmp_path, path):
    with pytest.raises(AssertionError):
        check_source(monkeypatch, tmp_path, path, ".sh-example { color: var(--sh-ink); }")


@pytest.mark.parametrize("path", [
    "web/src/design/generated/theme.css", "web/src/design/generated/recipes.css",
])
def test_only_named_generated_react_outputs_use_generator_boundary(monkeypatch, tmp_path, path):
    # Generation checks, rather than this handwritten-source guard, own exact output bytes.
    check_source(monkeypatch, tmp_path, path, ":root { --sh-ink: #ffffff; }")


def test_react_scan_still_excludes_third_party_dependencies(monkeypatch, tmp_path):
    vendor = tmp_path / "web/node_modules/vendor/theme.css"
    vendor.parent.mkdir(parents=True)
    vendor.write_text(".vendor { color: red; font-family: Vendor; }")
    check_source(monkeypatch, tmp_path, REACT_RECIPES[1], ".sh-example { color: var(--sh-ink); }")
