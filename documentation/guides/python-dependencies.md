# Python dependencies

Python packages install from two hashed locks:

- `requirements/prod.in` lists what production needs, as floors and pins. Its
  lock, `requirements/prod.txt`, is what the Docker image installs.
- `requirements/dev.in` takes `-r prod.in` and adds the test and lint tools.
  Its lock, `requirements/dev.txt`, is what CI and `scripts/harness.sh` install.

A lock pins every package, transitive ones included, to one version, and lists
the sha256 hash of each file published for that version. Every install runs
`pip install --require-hashes -r requirements/<lock>.txt`, so pip refuses a
file whose hash is not in the lock and a dependency the lock does not pin.
Never add `--no-deps`: it turns off that second check.

Edit the `.in` files, never the locks.

## Compiling the locks

`scripts/lock.sh` compiles `prod.in` into `prod.txt`, then `dev.in` into
`dev.txt`, with `uv pip compile --universal --generate-hashes --python-version 3.12`.
Run it with the uv version CI sets up (the "Set up uv" step in
`.github/workflows/ci.yml`): another version can write the same pins in a
different form.

- `--universal` writes markers for every platform and every Python from 3.12
  on, so one lock serves the image, CI and a laptop.
- Pins already in a lock stay while they still satisfy the `.in` files, so a
  compile moves only what it has to. A pin that stays keeps the hashes the
  lock lists for it, so a file uploaded later for that version enters the
  lock when the version changes.
- After compiling, the script checks that every `prod.txt` pin is in `dev.txt`
  unchanged, so the tests run against what production installs.

CI's "Python locks up to date" step, in the "Backend lint and PostgreSQL
integration tests" job, runs `scripts/lock.sh --check`. It compiles each lock
again into a new file, with the lock's own pins as constraints, so uv takes
every hash from the index. It fails unless each lock has the same pins
(versions and markers) as the new compile and every committed hash is in it.
The committed locks stay as they are, and CI installs those. A hash that only
the new compile has, for a file uploaded to PyPI since the lock was compiled,
does not fail the check: pip installs only files whose hashes the lock lists.
A committed hash that the new compile does not list fails it; delete both
locks and run `scripts/lock.sh` to compile them again.

## The 7-day cutoff

`[tool.uv.pip]` in `pyproject.toml` sets `exclude-newer = "7 days"`: uv only
considers files uploaded to PyPI at least 7 days before it runs. That holds for
every compile, by hand, in CI and in Dependabot's updates, and for every
package in the locks, not only the ones the `.in` files name.

Dependabot's uv reads the same table, so it may hold only keys and values that
uv version knows. With an unknown one, uv ignores all of `[tool.uv]` and logs a
warning, and the cutoff stops applying without any error.

## Dependabot

The `uv` entry in `.github/dependabot.yml` runs weekly from the repository
root, where it also reads `pyproject.toml` and `.python-version`.

- `cooldown` (7 days): a version update proposes only releases at least
  7 days old.
- `allow: dependency-type: all`: the pins of transitive dependencies are
  bumped too. Minor and patch updates outside the named groups arrive together
  in one pull request, the `other-dependencies` group. A major update gets its
  own.
- Each update recompiles the locks that pin the package, under the cutoff.

Security updates skip the cooldown but not the cutoff: a fix released less than
7 days ago gets no pull request until it is 7 days old.

If the lock check fails on a Dependabot pull request with the same versions on
both sides, Dependabot's uv wrote them in another form: move the uv version in
`ci.yml` to the one Dependabot ran, which its job log shows.

## Python version

`.python-version` holds the Python version of the image's base,
`python:3.12-slim`. Dependabot compiles with it; without it, Dependabot uses
the newest Python it has. Moving to another Python changes these together: the
Dockerfile's `FROM`, the `python-version` of each `actions/setup-python` step,
`.python-version`, `--python-version` in `scripts/lock.sh`, and both locks.

## Bumping a dependency by hand

1. Raise the floor or pin in the `.in` file. To move a package to its newest
   release without editing a file, pass its name: `scripts/lock.sh -P <name>`.
2. Run `scripts/lock.sh`.
3. Commit the `.in` file with both locks.

## A security fix younger than 7 days

Such a fix opens no Dependabot pull request, so the weekly review of
Dependabot's pull requests also reads the repository's open Dependabot alerts.
Wait for the fix to turn 7 days old, unless its advisory is high or critical
and shuffify reaches the affected code. Then exempt that one package from the
cutoff for the rest of the week, in `pyproject.toml`:

```toml
[tool.uv.pip]
exclude-newer = "7 days"
exclude-newer-package = { <name> = false }  # Remove on <date>: <advisory>
```

`<date>` is 7 days after the fixed release was uploaded. Run
`scripts/lock.sh -P <name>`, and have someone other than the author read the
release's changes before the pull request merges. On the removal date, delete
the line and run `scripts/lock.sh` again: the pins stay as they are.

## Not covered by the locks

- The base image: `python:3.12-slim` is a tag, not a digest.
- The Debian packages the Dockerfile installs with apt.
- Playwright's browsers, which `python -m playwright install` downloads.
- The build dependencies of a package installed from a source distribution:
  pip does not check their hashes.
