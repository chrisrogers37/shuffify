#!/usr/bin/env python3
"""Assert the Alembic revision graph is unambiguous.

Two parallel branches can each hand-pick a revision id, pick the *same* one,
and git will merge them without a word -- to git they are two new files that
do not overlap. Alembic then has two revisions sharing an id, and nothing in
lint, review or the merge says so (#527).

That matters more than a migration warning because the app refuses to boot
unless the schema is exactly at head (#503, SR-018). An ambiguous head is not
a nagging warning at deploy time; it is a container that will not start.

Pure text analysis on purpose: no Alembic import, no database, no app context,
no installed package. It runs in the lint step, before anything expensive, and
a human can run it directly on a checkout with nothing set up.

Exit 0 when the graph is sound, 1 when it is not. Every failure names the
specific ids and files involved -- someone hitting this at 2am should not have
to reconstruct what happened from a bare non-zero exit.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REVISION_RE = re.compile(r"^revision\s*(?::\s*str\s*)?=\s*['\"]([^'\"]+)['\"]", re.M)
DOWN_RE = re.compile(r"^down_revision\s*(?::[^=]*)?=\s*(?:['\"]([^'\"]+)['\"]|(None))", re.M)


def parse_migrations(versions_dir: pathlib.Path):
    """Return [(revision, down_revision_or_None, filename)] for each migration."""
    entries = []
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text()
        rev_match = REVISION_RE.search(text)
        if not rev_match:
            # A file in versions/ with no revision id is not a migration
            # Alembic will load, but it is worth surfacing rather than skipping
            # silently -- a typo in the assignment looks exactly like this.
            entries.append((None, None, path.name))
            continue
        down_match = DOWN_RE.search(text)
        down = None
        if down_match and down_match.group(1):
            down = down_match.group(1)
        entries.append((rev_match.group(1), down, path.name))
    return entries


def check(versions_dir: pathlib.Path):
    """Return a list of human-readable problems; empty means the graph is sound."""
    entries = parse_migrations(versions_dir)
    problems: list[str] = []

    unparsed = [f for rev, _, f in entries if rev is None]
    if unparsed:
        problems.append(
            "Migration files with no parseable `revision =` assignment:\n"
            + "".join(f"    {f}\n" for f in unparsed)
            + "  Alembic would ignore these. Check for a typo in the assignment."
        )

    migrations = [(r, d, f) for r, d, f in entries if r is not None]
    if not migrations:
        problems.append(f"No migrations found under {versions_dir}.")
        return problems

    # --- 1. Duplicate revision ids -------------------------------------
    by_id: dict[str, list[str]] = {}
    for rev, _down, filename in migrations:
        by_id.setdefault(rev, []).append(filename)

    for rev, files in sorted(by_id.items()):
        if len(files) > 1:
            problems.append(
                f"Duplicate revision id {rev!r} in {len(files)} files:\n"
                + "".join(f"    {f}\n" for f in files)
                + "  Two branches picked the same id. Git merges this cleanly\n"
                "  because the filenames differ. Re-parent the newer migration\n"
                "  onto a fresh id and point its down_revision at the other."
            )

    # --- 2. down_revision pointing at nothing ---------------------------
    known = set(by_id)
    for rev, down, filename in migrations:
        if down is not None and down not in known:
            problems.append(
                f"Migration {filename} ({rev}) has down_revision {down!r},\n"
                "  which is not any migration in this directory.\n"
                "  The chain is broken: nothing can walk past this revision."
            )

    # --- 3. Forks: one parent claimed by several children ---------------
    children: dict[str, list[str]] = {}
    for rev, down, filename in migrations:
        if down is not None:
            children.setdefault(down, []).append(f"{rev} ({filename})")

    for parent, kids in sorted(children.items()):
        if len(kids) > 1:
            problems.append(
                f"Fork: revision {parent!r} is the parent of {len(kids)} migrations:\n"
                + "".join(f"    {k}\n" for k in kids)
                + "  Alembic cannot pick between them. Chain them in sequence so\n"
                "  each has exactly one child."
            )

    # --- 4. Exactly one head --------------------------------------------
    claimed_as_parent = set(children)
    heads = sorted((rev, filename) for rev, _d, filename in migrations if rev not in claimed_as_parent)
    if len(heads) != 1:
        listed = "".join(f"    {rev}  ({f})\n" for rev, f in heads)
        problems.append(
            f"Expected exactly 1 head, found {len(heads)}:\n"
            + (listed or "    (none -- every revision is someone's parent)\n")
            + "  The app refuses to boot unless the schema is exactly at head\n"
            "  (#503), so an ambiguous head is a failed deploy, not a warning."
        )

    # --- 5. Cycles -------------------------------------------------------
    # A cycle makes "walk to the root" non-terminating, so check before anyone
    # writes a tool that does. Also produces zero heads, which case 4 reports
    # less specifically.
    for start_rev, _down, filename in migrations:
        seen = set()
        cur = start_rev
        down_of = {r: d for r, d, _f in migrations}
        while cur is not None:
            if cur in seen:
                problems.append(
                    f"Cycle in the revision graph, reachable from {filename}:\n"
                    f"    {' -> '.join(list(seen))} -> {cur}\n"
                    "  A migration chain must be a line, not a loop."
                )
                break
            seen.add(cur)
            cur = down_of.get(cur)
        else:
            continue
        break

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--versions-dir",
        default=None,
        help="Path to migrations/versions (default: alongside this repo)",
    )
    args = parser.parse_args()

    if args.versions_dir:
        versions_dir = pathlib.Path(args.versions_dir)
    else:
        versions_dir = pathlib.Path(__file__).resolve().parent.parent / ("migrations/versions")

    if not versions_dir.is_dir():
        print(f"ERROR: no such directory: {versions_dir}", file=sys.stderr)
        return 1

    problems = check(versions_dir)
    count = len(list(versions_dir.glob("*.py")))

    if problems:
        print("Alembic revision graph is ambiguous:\n", file=sys.stderr)
        for problem in problems:
            print(f"  * {problem}\n", file=sys.stderr)
        print(
            f"Checked {count} file(s) in {versions_dir}. {len(problems)} problem(s) found.",
            file=sys.stderr,
        )
        return 1

    print(f"Alembic revision graph OK: {count} migrations, single head, no duplicates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
