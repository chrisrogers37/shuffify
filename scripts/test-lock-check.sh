#!/usr/bin/env bash
# Runs scripts/lock.sh --check on two copies of the locks that it must fail on,
# and fails unless it fails on each with the message for that case:
# - a stale pin: requirements/dev.in without PyYAML while dev.txt still pins
#   it, as when a requirement leaves an .in file and its lock is not compiled
#   again;
# - an unknown hash: one requirements/prod.txt hash replaced by one that no
#   file has.
# The copies are made in a temporary directory, and the committed files stay
# as they are. CI runs this after scripts/lock.sh --check passes on the locks;
# run it after changing scripts/lock.sh, with the uv version CI sets up.
set -euo pipefail
export LC_ALL=C

cd "$(dirname "${BASH_SOURCE[0]}")/.."

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# copy NAME: what scripts/lock.sh --check reads, in a new directory.
copy() {
  mkdir -p "${work}/$1/scripts" "${work}/$1/requirements"
  cp scripts/lock.sh "${work}/$1/scripts/"
  cp requirements/*.in requirements/*.txt "${work}/$1/requirements/"
  cp pyproject.toml .python-version "${work}/$1/"
}

# fails NAME MESSAGE PATTERN EXPECTED: scripts/lock.sh --check in the copy NAME
# must fail and print MESSAGE, and the lines of its output that match PATTERN
# must be exactly EXPECTED.
fails() {
  local name="$1" message="$2" pattern="$3" expected="$4" out
  if out="$("${work}/${name}/scripts/lock.sh" --check 2>&1)"; then
    printf '%s: scripts/lock.sh --check passed\n' "${name}" >&2
    return 1
  fi
  printf -- '--- %s: scripts/lock.sh --check failed\n%s\n' "${name}" "${out}"
  if ! grep -qxF -- "${message}" <<< "${out}"; then
    printf '%s: no line reads: %s\n' "${name}" "${message}" >&2
    return 1
  fi
  if [[ "$(grep -E -- "${pattern}" <<< "${out}" || true)" != "${expected}" ]]; then
    printf '%s: the lines matching %s are not exactly: %s\n' \
      "${name}" "${pattern}" "${expected}" >&2
    return 1
  fi
}

# A stale pin: PyYAML leaves dev.in, and dev.txt keeps its pin.
copy stale-pin
dev_in="${work}/stale-pin/requirements/dev.in"
grep -v -i -E '^pyyaml([^a-z0-9._-]|$)' requirements/dev.in > "${dev_in}" || true
if (( $(wc -l < requirements/dev.in) - $(wc -l < "${dev_in}") != 1 )); then
  echo "requirements/dev.in needs exactly one PyYAML line for the stale pin" >&2
  exit 1
fi
stale="$(grep -i -E '^pyyaml==' requirements/dev.txt | sed -e 's/ *\\$//')" || {
  echo "requirements/dev.txt has no PyYAML pin for the stale pin" >&2
  exit 1
}
fails stale-pin \
  "requirements/dev.txt has other pins than a new compile (<: committed, >: new). Run scripts/lock.sh." \
  '^[<>] ' "< ${stale}"

# An unknown hash: the first hash in prod.txt becomes 64 zeros.
copy unknown-hash
zeros="$(printf '0%.0s' {1..64})"
awk -v hash="--hash=sha256:${zeros}" \
  '!done && sub(/--hash=sha256:[0-9a-f]+/, hash) { done = 1 } { print }' \
  requirements/prod.txt > "${work}/unknown-hash/requirements/prod.txt"
first="$(grep -m 1 -E '^[A-Za-z0-9]' requirements/prod.txt | sed -e 's/ *\\$//')"
fails unknown-hash \
  "requirements/prod.txt has hashes that a new compile does not list. Delete both locks and run scripts/lock.sh." \
  '--hash=' "${first}"$'\t'"--hash=sha256:${zeros}"

echo "scripts/lock.sh --check failed on both copies, each with its own message."
