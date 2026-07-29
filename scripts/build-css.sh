#!/usr/bin/env bash
# Compile shuffify's Tailwind stylesheet.
#
# Uses Tailwind's standalone CLI: a single self-contained binary, no Node and no
# npm, which is why this repo stays a pure-Python project (issue #482).
#
# The compiled output is COMMITTED to the repo (shuffify/static/css/tailwind.css).
# DigitalOcean deploys on push, so what the repo contains is what serves --
# keeping the artifact in-tree means the deploy never depends on a build-time
# download, and a reviewer sees the exact CSS that will ship.
#
# Run this whenever you change a template, a static .js file, or tailwind.config.js,
# then commit the regenerated stylesheet. CI fails if you forget (see ci.yml).
#
#   ./scripts/build-css.sh            compile
#   ./scripts/build-css.sh --check    verify the committed file is up to date
set -euo pipefail

TAILWIND_VERSION="v3.4.19"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Tailwind resolves the `content` globs in tailwind.config.js relative to the
# working directory. Running from anywhere else silently matches no templates
# and emits a near-empty stylesheet, so pin the cwd to the repo root.
cd "${REPO_ROOT}"
INPUT="${REPO_ROOT}/assets/tailwind.src.css"
OUTPUT="${REPO_ROOT}/shuffify/static/css/tailwind.css"
CONFIG="${REPO_ROOT}/tailwind.config.js"
CACHE_DIR="${TAILWIND_CACHE_DIR:-${TMPDIR:-/tmp}/tailwind-cli}"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)   ASSET="tailwindcss-linux-x64" ;;
  Linux-aarch64)  ASSET="tailwindcss-linux-arm64" ;;
  Darwin-x86_64)  ASSET="tailwindcss-macos-x64" ;;
  Darwin-arm64)   ASSET="tailwindcss-macos-arm64" ;;
  *) echo "build-css: unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

BIN="${CACHE_DIR}/${ASSET}-${TAILWIND_VERSION}"
if [[ ! -x "${BIN}" ]]; then
  echo "build-css: fetching Tailwind standalone CLI ${TAILWIND_VERSION} (${ASSET})"
  mkdir -p "${CACHE_DIR}"
  curl -fsSL -o "${BIN}" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/${ASSET}"
  chmod +x "${BIN}"
fi

if [[ "${1:-}" == "--check" ]]; then
  TMP="$(mktemp)"
  trap 'rm -f "${TMP}"' EXIT
  "${BIN}" -c "${CONFIG}" -i "${INPUT}" -o "${TMP}" --minify >/dev/null 2>&1
  if ! diff -q "${TMP}" "${OUTPUT}" >/dev/null 2>&1; then
    echo "build-css: FAIL -- ${OUTPUT#"${REPO_ROOT}/"} is stale." >&2
    echo "           A template, static .js, or tailwind.config.js changed without" >&2
    echo "           a rebuild. Run ./scripts/build-css.sh and commit the result." >&2
    exit 1
  fi
  echo "build-css: OK -- committed stylesheet matches the current sources."
  exit 0
fi

"${BIN}" -c "${CONFIG}" -i "${INPUT}" -o "${OUTPUT}" --minify
echo "build-css: wrote ${OUTPUT#"${REPO_ROOT}/"} ($(wc -c < "${OUTPUT}") bytes)"
