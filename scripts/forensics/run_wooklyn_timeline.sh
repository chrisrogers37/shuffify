#!/usr/bin/env bash
# Run the WOOKLYN forensic timeline query against Neon.
#
# Usage:
#   ./run_wooklyn_timeline.sh                 # pattern '%WOOKLYN%', 60 days
#   ./run_wooklyn_timeline.sh '%my-playlist%' # custom pattern, 60 days
#   ./run_wooklyn_timeline.sh '%WOOKLYN%' 365 # custom pattern + window
#
# Requires DATABASE_URL in the environment (export it from .env or DO).
set -euo pipefail

PATTERN="${1:-%WOOKLYN%}"
DAYS_BACK="${2:-60}"

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL not set." >&2
    echo "Source it from .env or DO app spec before running, e.g.:" >&2
    echo "    export DATABASE_URL=\"<neon connection string>\"" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

psql "$DATABASE_URL" \
    -v ON_ERROR_STOP=1 \
    -v playlist_name_pattern="'${PATTERN}'" \
    -v days_back="${DAYS_BACK}" \
    -f "${SCRIPT_DIR}/wooklyn_loss_timeline.sql"
