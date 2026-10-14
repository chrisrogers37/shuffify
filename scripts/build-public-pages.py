#!/usr/bin/env python3
"""Generate committed legal HTML from shared shell macros and literal legal copy.

Run with the existing Python environment, before rebuilding Tailwind CSS:
    python scripts/build-public-pages.py
    python scripts/build-public-pages.py --check

The routes continue serving these files verbatim. No app initialization, network,
or runtime template rendering is needed for generation or verification.
"""

import argparse
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE_TITLES = {
    "terms": "Terms of Service",
    "privacy": "Privacy Policy",
}
# A committed artifact must not drift when the clock crosses into a new year.
COPYRIGHT_YEAR = 2026


def render_public_pages(repo_root: Path | None = None) -> dict[str, str]:
    """Render the static artifacts without Flask or hidden template globals."""
    repo_root = REPO_ROOT if repo_root is None else repo_root
    environment = Environment(
        loader=FileSystemLoader(
            [
                repo_root / "assets" / "design" / "templates",
                repo_root / "shuffify" / "templates",
            ]
        ),
        autoescape=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    template = environment.get_template("legal.html.j2")
    pages = {}
    for name, title in PAGE_TITLES.items():
        # These repository-owned fragments are literal HTML, not templates.
        # Only their markup is trusted; shell parameters remain autoescaped.
        legal_copy = (repo_root / "assets" / "legal" / f"{name}.html").read_text(
            encoding="utf-8"
        )
        pages[name] = template.render(
            title=title,
            current_page=name,
            legal_copy=Markup(legal_copy),
            home_url="/",
            terms_url="/terms",
            privacy_url="/privacy",
            year=COPYRIGHT_YEAR,
        )
    return pages


def main(argv: list[str] | None = None) -> int:
    """Write artifacts, or fail without writing when --check finds drift."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify committed pages without writing"
    )
    args = parser.parse_args(argv)
    stale = []
    for name, html in render_public_pages().items():
        output = REPO_ROOT / "shuffify" / "static" / "public" / f"{name}.html"
        expected = html.encode("utf-8")
        if args.check:
            if not output.is_file() or output.read_bytes() != expected:
                stale.append(output.relative_to(REPO_ROOT))
        else:
            output.write_bytes(expected)
            print(f"build-public-pages: wrote {output.relative_to(REPO_ROOT)}")
    if stale:
        print(
            "build-public-pages: FAIL -- stale or missing: "
            + ", ".join(str(path) for path in stale),
            file=sys.stderr,
        )
        print(
            "Run python scripts/build-public-pages.py and commit the generated pages.",
            file=sys.stderr,
        )
        return 1
    if args.check:
        print("build-public-pages: OK -- committed pages match their sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
