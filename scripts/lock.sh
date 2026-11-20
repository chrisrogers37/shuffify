#!/usr/bin/env bash
# Compiles the hashed locks requirements/prod.txt and requirements/dev.txt from
# the .in files beside them, under the 7-day cutoff in pyproject.toml.
# Arguments go to each uv pip compile: -P <package> moves one package to its
# newest release before the cutoff. Keep the uv options below as they are:
# Dependabot rebuilds its command from the lock headers and keeps only some
# options, so a new resolver setting goes in [tool.uv.pip] instead.
#
# --check, which CI runs, compiles each lock again into a new file, with the
# lock's own pins as constraints, and leaves the committed locks as they are.
# It fails unless each lock has the same pins as the new compile and every
# committed hash is in it. The new file matters: a compile over a lock keeps
# the hashes it lists for every version that stays, and only a compile into a
# new file takes them from the index.
# See documentation/guides/python-dependencies.md.
set -euo pipefail
export LC_ALL=C

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# A lock's pins (name==version and any markers), and its hashes, one
# "pin<TAB>hash" per line. Comments are left out.
pins() { grep -E '^[A-Za-z0-9]' "$1" | sed -e 's/ *\\$//' | sort -u; }
hashes() {
  awk '/^[A-Za-z0-9]/ { pin = $0; sub(/ *\\$/, "", pin); next }
       /^ *--hash=/ { print pin "\t" $1 }' "$1" | sort -u
}

check=false
if [[ "${1:-}" == "--check" ]]; then
  check=true
  shift
  new="$(mktemp -d)"
  trap 'rm -rf "${new}"' EXIT
fi

for name in prod dev; do
  if "${check}"; then
    pins "requirements/${name}.txt" > "${new}/${name}.pins"
    uv pip compile --quiet --universal --generate-hashes --python-version 3.12 "$@" \
      --constraint "${new}/${name}.pins" --output-file "${new}/${name}.txt" "requirements/${name}.in"
  else
    uv pip compile --quiet --universal --generate-hashes --python-version 3.12 "$@" \
      --output-file "requirements/${name}.txt" "requirements/${name}.in"
  fi
done

status=0
fail() {
  printf '%s\n' "$@" >&2
  status=1
}

# The image installs prod.txt and CI tests dev.txt: every prod.txt pin must be
# in dev.txt unchanged.
missing="$(comm -23 <(pins requirements/prod.txt) <(pins requirements/dev.txt))"
[[ -z "${missing}" ]] ||
  fail "These requirements/prod.txt pins are missing from requirements/dev.txt or differ there:" "${missing}"

if "${check}"; then
  for name in prod dev; do
    changed="$(diff <(pins "requirements/${name}.txt") <(pins "${new}/${name}.txt") || true)"
    if [[ -n "${changed}" ]]; then
      fail "requirements/${name}.txt has other pins than a new compile (<: committed, >: new). Run scripts/lock.sh." "${changed}"
      continue
    fi
    dropped="$(comm -23 <(hashes "requirements/${name}.txt") <(hashes "${new}/${name}.txt"))"
    [[ -z "${dropped}" ]] ||
      fail "requirements/${name}.txt has hashes that a new compile does not list. Delete both locks and run scripts/lock.sh." "${dropped}"
  done
fi
exit "${status}"
