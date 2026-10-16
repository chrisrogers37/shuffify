#!/usr/bin/env bash
# Local end-to-end harness for shuffify.
#
# Runs the real app the way production is shaped -- PostgreSQL, Redis, the
# worker scheduler on, a new database built from the models by `flask create-schema`,
# migrations applied by `flask db upgrade` before the app starts and verified at
# head when it does -- with everything on this machine.
#
#   scripts/harness.sh setup [--quick]  idempotent: deps, services, migrations,
#                                       artifact checks, lint + tests, then
#                                       (re)start the app and check it. --quick
#                                       skips the artifact checks, lint, tests.
#   scripts/harness.sh e2e [pytest args] the behaviour tests (tests/e2e) against
#                                       the running app and fake Spotify
#   scripts/harness.sh down             stop the app and services (data kept)
#   scripts/harness.sh status           show what is running
#   scripts/harness.sh reset-db         drop and re-create the database, then
#                                       setup --quick
#
# Linux, bash 4.4+ (mapfile, setsid, runuser).
#
# Safety:
#   * Harness processes start from an empty environment (env -i) plus an
#     explicit list, excluding other shell exports. FLASK_SKIP_DOTENV=1 also
#     prevents Flask's CLI and app.run() from loading .env or .flaskenv files
#     in this checkout or its parent directories.
#   * They run under APP_CONFIG=harness, whose config refuses to start unless
#     Postgres and Redis are on localhost and the Spotify credentials are the
#     harness fakes (config.py, HarnessConfig.assert_safe).
#   * They load harness/netguard/, which refuses any socket to anything but
#     this machine's harness ports. Every refusal is logged, and this script
#     fails if the log is not empty.
#   * Spotify is the local fake (harness/fake_spotify/), under the network
#     guard too: SPOTIFY_BASE_URL points every Spotify URL in the app at it.
#     It checks every call against Spotify's documentation, and this script
#     fails if it recorded anything undocumented.
#
# Variables:
#   HARNESS_SERVICES=local|external  external: use DATABASE_URL and REDIS_URL
#                                    as given (CI service containers), already
#                                    running. They must still be on localhost;
#                                    HarnessConfig checks before any connection.
#   HARNESS_PYTHON                   interpreter with deps already installed
#                                    (not installed here); default is .venv/,
#                                    created with python3.12 and kept in sync.
#   HARNESS_APP_PORT                 default 8000.
#   HARNESS_API_PORT                 default 8002; proxied by the React test origin.
#   HARNESS_FAKE_SPOTIFY_PORT        default 8900.
#   HARNESS_WORKER_CONTROL_PORT      default 8001; loopback-only worker health/control.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"
# libpq reads PG* variables, and PGHOSTADDR or PGSERVICE can send a connection
# elsewhere whatever host it names. This script's own psql/createdb calls take
# none of them; harness processes start from env -i regardless.
unset "${!PG@}"

STATE="${REPO_ROOT}/.harness"
SERVICES="${HARNESS_SERVICES:-local}"
APP_PORT="${HARNESS_APP_PORT:-8000}"
API_PORT="${HARNESS_API_PORT:-8002}"
# 127.0.0.1, not localhost: Spotify refuses localhost redirect URIs, and the
# fake does too.
APP_URL="http://127.0.0.1:${APP_PORT}"
API_URL="http://127.0.0.1:${API_PORT}"
REDIRECT_URI="${APP_URL}/callback"
# Where the local fake Spotify listens. Every Spotify host the app talks to
# (API, OAuth, public pages) is pointed here via SPOTIFY_BASE_URL.
FAKE_SPOTIFY_PORT="${HARNESS_FAKE_SPOTIFY_PORT:-8900}"
FAKE_SPOTIFY_URL="http://127.0.0.1:${FAKE_SPOTIFY_PORT}"
WORKER_CONTROL_PORT="${HARNESS_WORKER_CONTROL_PORT:-8001}"
WORKER_CONTROL_URL="http://127.0.0.1:${WORKER_CONTROL_PORT}"
for _port in "${APP_PORT}" "${API_PORT}" "${FAKE_SPOTIFY_PORT}" "${WORKER_CONTROL_PORT}"; do
  [[ "${_port}" =~ ^[0-9]+$ ]] || { echo "harness: port must be a number, got '${_port}'" >&2; exit 1; }
done
PG_PORT=54329
REDIS_PORT=63799
DB_NAME=shuffify_harness
DB_USER=shuffify
VIOLATIONS="${STATE}/netguard-violations.log"

log() { printf '\033[1mharness:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mharness: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
python_bin() {
  if [[ -n "${HARNESS_PYTHON:-}" ]]; then
    command -v "${HARNESS_PYTHON}" || die "HARNESS_PYTHON=${HARNESS_PYTHON} not found"
    return
  fi
  if [[ ! -x .venv/bin/python ]]; then
    local base
    base="$(command -v python3.12 || true)"
    if [[ -z "${base}" ]]; then
      base="$(command -v python3)" || die "python3.12 (or python3 >= 3.12) is required"
      log "python3.12 not found; using $(${base} --version) -- CI runs 3.12"
    fi
    log "creating .venv with $(${base} --version)"
    "${base}" -m venv .venv
  fi
  echo "${REPO_ROOT}/.venv/bin/python"
}

install_deps() {
  local py="$1" stamp="${STATE}/deps.sha" want
  [[ -z "${HARNESS_PYTHON:-}" ]] || return 0  # the caller manages that interpreter
  want="$(cat requirements/*.txt | sha256sum | cut -d' ' -f1):$(${py} --version)"
  if [[ -f "${stamp}" && "$(cat "${stamp}")" == "${want}" ]]; then
    log "dependencies up to date"
    return
  fi
  log "installing requirements/dev.txt"
  "${py}" -m pip install -q --upgrade pip
  "${py}" -m pip install -q -r requirements/dev.txt
  echo "${want}" > "${stamp}"
}

# ---------------------------------------------------------------------------
# Secrets: generated once per checkout, never printed, never committed
# (.harness/ is gitignored).
# ---------------------------------------------------------------------------
ensure_secrets() {
  local py="$1" file="${STATE}/secrets.env"
  if [[ ! -s "${file}" ]]; then
    ( umask 077
      "${py}" - > "${file}" <<'EOF'
import secrets
from cryptography.fernet import Fernet
print(f"SECRET_KEY={secrets.token_hex(32)}")
print(f"TOKEN_ENCRYPTION_KEY={Fernet.generate_key().decode()}")
EOF
    )
  fi
  if ! grep -q '^WORKER_CONTROL_TOKEN=' "${file}"; then
    ( umask 077
      "${py}" -c 'import secrets; print("WORKER_CONTROL_TOKEN=" + secrets.token_urlsafe(32))' >> "${file}"
    )
  fi
}

# ---------------------------------------------------------------------------
# Service URLs
# ---------------------------------------------------------------------------
database_url() {
  if [[ "${SERVICES}" == external ]]; then
    [[ -n "${DATABASE_URL:-}" ]] || die "HARNESS_SERVICES=external needs DATABASE_URL"
    echo "${DATABASE_URL}"
  else
    echo "postgresql://${DB_USER}@127.0.0.1:${PG_PORT}/${DB_NAME}"
  fi
}

redis_url() {
  if [[ "${SERVICES}" == external ]]; then
    [[ -n "${REDIS_URL:-}" ]] || die "HARNESS_SERVICES=external needs REDIS_URL"
    echo "${REDIS_URL}"
  else
    echo "redis://127.0.0.1:${REDIS_PORT}/0"
  fi
}

# The ports harness processes may connect to: Redis, PostgreSQL, the app and
# the fake Spotify. The URL ports are parsed once per run.
allow_ports() {
  local py="$1"
  if [[ -z "${URL_PORTS:-}" ]]; then
    URL_PORTS="$("${py}" -c '
import sys
from urllib.parse import urlsplit
print(",".join(str(urlsplit(u).port or d) for u, d in zip(sys.argv[1:], (6379, 5432))))
' "$(redis_url)" "$(database_url)")"
  fi
  ALLOW_PORTS="${URL_PORTS},${APP_PORT},${API_PORT},${FAKE_SPOTIFY_PORT},${WORKER_CONTROL_PORT}"
}

# ---------------------------------------------------------------------------
# Running a command as a harness process
# ---------------------------------------------------------------------------
# clean_env <python>
# Fills CLEAN_ENV with the start of every harness launch: an empty environment
# with only the basics, and no .env/.flaskenv loading by Flask's CLI or app.run().
clean_env() {
  CLEAN_ENV=(env -i
    PATH="$(dirname "$1"):/usr/local/bin:/usr/bin:/bin"
    HOME="${HOME:-/tmp}" LANG=C.UTF-8 TMPDIR="${TMPDIR:-/tmp}"
    FLASK_SKIP_DOTENV=1)
}

# harness_cmd <python> [VAR=value ...] -- <command...>
# Fills HARNESS_CMD with the command wrapped in the harness environment.
harness_cmd() {
  local py="$1"; shift
  local overrides=() secrets=()
  while [[ "$1" != "--" ]]; do overrides+=("$1"); shift; done
  shift

  local db redis
  db="$(database_url)"
  redis="$(redis_url)"
  allow_ports "${py}"
  mapfile -t secrets < "${STATE}/secrets.env"
  clean_env "${py}"

  # No proxy variables: harness processes have no business on the internet,
  # and the network guard would refuse the proxy's port regardless.
  HARNESS_CMD=("${CLEAN_ENV[@]}"
    APP_CONFIG=harness
    FLASK_APP=run.py
    DATABASE_URL="${db}"
    REDIS_URL="${redis}"
    SPOTIFY_CLIENT_ID=harness-fake-client-id
    SPOTIFY_CLIENT_SECRET=harness-fake-client-secret
    SPOTIFY_REDIRECT_URI="${REDIRECT_URI}"
    SPOTIFY_BASE_URL="${FAKE_SPOTIFY_URL}"
    SCHEDULER_ENABLED=true
    SCHEDULER_TIMEZONE=UTC
    WORKER_CONTROL_URL="${WORKER_CONTROL_URL}"
    WORKER_CONTROL_HOST=127.0.0.1
    WORKER_CONTROL_PORT="${WORKER_CONTROL_PORT}"
    SENTRY_DSN=
    PYTHONPATH="${REPO_ROOT}/harness/netguard"
    SHUFFIFY_NETGUARD=1
    SHUFFIFY_NETGUARD_ALLOW_PORTS="${ALLOW_PORTS}"
    SHUFFIFY_NETGUARD_LOG="${VIOLATIONS}"
    "${secrets[@]}"
    "${overrides[@]}"
    "$@")
}

run_harness() {
  harness_cmd "$@"
  "${HARNESS_CMD[@]}"
}

check_violations() {
  if [[ -s "${VIOLATIONS}" ]]; then
    cat "${VIOLATIONS}" >&2
    die "the network guard blocked the connections above"
  fi
}

# A new run starts a fresh log, but never by silently dropping the old one:
# whatever the previous app recorded is shown and kept alongside.
start_violations_log() {
  if [[ -s "${VIOLATIONS}" ]]; then
    log "the network guard blocked these connections since the last setup:"
    cat "${VIOLATIONS}" >&2
    cat "${VIOLATIONS}" >> "${STATE}/netguard-violations.previous.log"
    log "(kept in .harness/netguard-violations.previous.log)"
  fi
  : > "${VIOLATIONS}"
}

# ---------------------------------------------------------------------------
# PostgreSQL and Redis (local mode)
# ---------------------------------------------------------------------------
pg_bin() {
  local dir
  if command -v pg_ctl >/dev/null && command -v initdb >/dev/null; then
    dirname "$(command -v pg_ctl)"
    return
  fi
  dir="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)"
  [[ -n "${dir}" ]] || die "PostgreSQL server binaries (initdb, pg_ctl) not found"
  echo "${dir}"
}

# PostgreSQL refuses to run as root; containers often run as root.
as_pg() {
  if [[ "$(id -u)" == 0 ]]; then
    runuser -u postgres -- "$@"
  else
    "$@"
  fi
}

start_postgres() {
  local bin data="${STATE}/pg/data" run="${STATE}/pg/run"
  bin="$(pg_bin)"
  mkdir -p "${STATE}/pg"
  [[ "$(id -u)" == 0 ]] && chown postgres:postgres "${STATE}/pg"
  if [[ ! -f "${data}/PG_VERSION" ]]; then
    log "initialising PostgreSQL ($("${bin}/postgres" --version))"
    as_pg mkdir -p "${data}" "${run}"
    # A throwaway database on a loopback-only listener needs no password.
    as_pg "${bin}/initdb" -D "${data}" -U "${DB_USER}" --auth=trust -E UTF8 --locale=C >/dev/null
  fi
  if ! as_pg "${bin}/pg_ctl" -D "${data}" status >/dev/null 2>&1; then
    log "starting PostgreSQL on 127.0.0.1:${PG_PORT}"
    # Trust auth on 127.0.0.1 admits any local user; the socket at least is
    # the postgres user's alone. Fine for a single-user container or laptop.
    as_pg "${bin}/pg_ctl" -D "${data}" -l "${STATE}/pg/postgres.log" -w \
      -o "-p ${PG_PORT} -k ${run} -c listen_addresses=127.0.0.1 -c unix_socket_permissions=0700" start >/dev/null
  fi
  if ! "${bin}/psql" -h 127.0.0.1 -p "${PG_PORT}" -U "${DB_USER}" -d postgres -Atqc \
      "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    log "creating database ${DB_NAME}"
    "${bin}/createdb" -h 127.0.0.1 -p "${PG_PORT}" -U "${DB_USER}" "${DB_NAME}"
  fi
}

stop_postgres() {
  local bin data="${STATE}/pg/data"
  [[ -f "${data}/PG_VERSION" ]] || return 0
  bin="$(pg_bin)"
  if as_pg "${bin}/pg_ctl" -D "${data}" status >/dev/null 2>&1; then
    log "stopping PostgreSQL"
    as_pg "${bin}/pg_ctl" -D "${data}" -m fast -w stop >/dev/null
  fi
}

start_redis() {
  mkdir -p "${STATE}/redis"
  if redis-cli -h 127.0.0.1 -p "${REDIS_PORT}" ping >/dev/null 2>&1; then
    return
  fi
  command -v redis-server >/dev/null || die "redis-server not found"
  log "starting Redis on 127.0.0.1:${REDIS_PORT}"
  redis-server --bind 127.0.0.1 --port "${REDIS_PORT}" --save '' --appendonly no \
    --daemonize yes --dir "${STATE}/redis" \
    --pidfile "${STATE}/redis/redis.pid" --logfile "${STATE}/redis/redis.log"
  for _ in $(seq 50); do
    redis-cli -h 127.0.0.1 -p "${REDIS_PORT}" ping >/dev/null 2>&1 && return
    sleep 0.1
  done
  die "Redis did not start; see ${STATE}/redis/redis.log"
}

stop_redis() {
  if redis-cli -h 127.0.0.1 -p "${REDIS_PORT}" ping >/dev/null 2>&1; then
    log "stopping Redis"
    redis-cli -h 127.0.0.1 -p "${REDIS_PORT}" shutdown nosave >/dev/null 2>&1 || true
  fi
}

start_services() {
  local py="$1"
  if [[ "${SERVICES}" == external ]]; then
    # Nothing to start or probe: the first connection is the migration step,
    # a harness process, so HarnessConfig.assert_safe vets both URLs first.
    log "using external PostgreSQL and Redis"
  else
    start_postgres
    start_redis
  fi
}

# ---------------------------------------------------------------------------
# Migrations and the app
# ---------------------------------------------------------------------------
migrate() {
  local py="$1"
  log "building a new database (flask create-schema), then applying migrations (flask db upgrade)"
  # SHUFFIFY_MIGRATION_STEP exempts these processes from the schema-at-head
  # check they exist to satisfy; the scheduler has no business running here.
  # create-schema leaves a database that already has a revision as it is.
  { harness_cmd "${py}" SHUFFIFY_MIGRATION_STEP=1 SCHEDULER_ENABLED=false -- "${py}" -m flask \
      && "${HARNESS_CMD[@]}" create-schema && "${HARNESS_CMD[@]}" db upgrade; } \
    >"${STATE}/migrate.log" 2>&1 \
    || { tail -40 "${STATE}/migrate.log" >&2; die "migrations failed; see ${STATE}/migrate.log"; }
}

# A background harness process <name>: .harness/<name>.pid and <name>.log.
proc_pid() {  # proc_pid <name>
  local pid
  [[ -f "${STATE}/$1.pid" ]] || return 1
  pid="$(cat "${STATE}/$1.pid")"
  kill -0 "${pid}" 2>/dev/null && echo "${pid}"
}

stop_proc() {  # stop_proc <name>
  local pid attempts=20
  if [[ "$1" == worker ]]; then attempts=7200; fi
  if pid="$(proc_pid "$1")"; then
    log "stopping $1 (pid ${pid})"
    kill "${pid}" 2>/dev/null || true
    for _ in $(seq "${attempts}"); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.25; done
    if kill -0 "${pid}" 2>/dev/null; then kill -9 "${pid}" 2>/dev/null || true; fi
  fi
  rm -f "${STATE}/$1.pid"
}

# 000 for no answer, including one that hangs past the timeout.
url_code() {
  curl --noproxy '*' -s --max-time 5 -o /dev/null -w '%{http_code}' "$1"
}

# launch_proc <name> <url>: run HARNESS_CMD in the background as <name> and
# wait for <url> to answer 200. The wrapper records its own pid and then
# becomes the command via exec.
launch_proc() {
  local name="$1" url="$2"
  [[ "$(url_code "${url}")" == 000 ]] || die "something else is already answering ${url}"
  : > "${STATE}/${name}.log"
  rm -f "${STATE}/${name}.pid"
  setsid nohup bash -c 'echo $$ > "$0"; exec "$@"' "${STATE}/${name}.pid" "${HARNESS_CMD[@]}" \
    >>"${STATE}/${name}.log" 2>&1 < /dev/null &
  for _ in $(seq 60); do
    [[ "$(url_code "${url}")" == 200 ]] && return
    if [[ -f "${STATE}/${name}.pid" ]] && ! proc_pid "${name}" >/dev/null; then
      tail -40 "${STATE}/${name}.log" >&2
      die "${name} exited during startup; see ${STATE}/${name}.log"
    fi
    sleep 0.5
  done
  tail -40 "${STATE}/${name}.log" >&2
  die "${name} did not answer ${url} within 30s"
}

# The guard loads at interpreter start; prove it did in the harness
# environment rather than assume it from the variables.
require_netguard() {
  local py="$1"
  run_harness "${py}" -- "${py}" -c \
    'import socket, sys; sys.exit(0 if getattr(socket, "SHUFFIFY_NETGUARD_ACTIVE", False) else 1)' \
    || die "the network guard is not active in harness processes"
}

# The fake Spotify every Spotify URL in the app points at. It accepts the one
# redirect URI the app sends, as a registered Spotify app would. It needs
# none of the app's settings or secrets, so it gets none: only the network
# guard, allowing nothing but its own port.
start_fake_spotify() {
  local py="$1"
  stop_proc fake-spotify
  log "starting the fake Spotify on ${FAKE_SPOTIFY_URL}"
  clean_env "${py}"
  HARNESS_CMD=("${CLEAN_ENV[@]}"
    PYTHONPATH="${REPO_ROOT}/harness/netguard"
    SHUFFIFY_NETGUARD=1
    SHUFFIFY_NETGUARD_ALLOW_PORTS="${FAKE_SPOTIFY_PORT}"
    SHUFFIFY_NETGUARD_LOG="${VIOLATIONS}"
    "${py}" -m harness.fake_spotify --port "${FAKE_SPOTIFY_PORT}" --redirect-uri "${REDIRECT_URI}")
  launch_proc fake-spotify "${FAKE_SPOTIFY_URL}/_fake/health"
}

# The fake records every request or response that departs from Spotify's
# documentation: an undocumented parameter or field the app sent, or a shape
# the fake itself got wrong. Either is a failure of the run.
check_fake_spotify() {
  local recorded
  recorded="$(curl --noproxy '*' -fsS "${FAKE_SPOTIFY_URL}/_fake/violations")" \
    || die "could not read the fake Spotify's violations"
  [[ "${recorded}" == "[]" ]] \
    || die "the fake Spotify recorded calls Spotify does not document: ${recorded}"
  log "fake Spotify: every call matched Spotify's documentation"
}

# Always a fresh process: one left from an earlier run would be checked
# against code, a schema and a violations log it never saw.
start_app() {
  local py="$1"
  stop_proc app
  log "starting the API on ${API_URL}"
  require_netguard "${py}"
  harness_cmd "${py}" -- "${py}" -m gunicorn --worker-class gthread \
    --workers 1 --threads 8 --timeout 30 --bind "127.0.0.1:${API_PORT}" run:app
  # The schema check runs here for real: MIGRATE_ON_STARTUP is off, so the
  # app refuses to start unless migrate() left the schema at head.
  launch_proc app "${API_URL}/health"
}

build_frontend() {
  local py="$1" want contact
  command -v npm >/dev/null || die "Node 24 and npm are required for the React harness"
  [[ "$(node -p 'process.versions.node.split(".")[0]')" == 24 ]] || die "the React harness requires Node 24"
  want="$(sha256sum web/package-lock.json | cut -d' ' -f1)"
  if [[ ! -d web/node_modules || ! -f "${STATE}/frontend-deps.sha" || \
        "$(cat "${STATE}/frontend-deps.sha")" != "${want}" ]]; then
    npm --prefix web ci --no-audit --no-fund
    echo "${want}" > "${STATE}/frontend-deps.sha"
  fi
  contact="$("${py}" -c 'import runpy; print(runpy.run_path("scripts/build-public-pages.py")["DEFAULT_CONTACT_ADDRESS"])')"
  log "building the production React app"
  CONTACT_ADDRESS="${CONTACT_ADDRESS:-${contact}}" npm --prefix web run build
}

start_frontend() {
  local py="$1"
  stop_proc frontend
  log "starting the React test origin on ${APP_URL}"
  harness_cmd "${py}" -- "${py}" -m tests.frontend_preview --port "${APP_PORT}" --api-url "${API_URL}" \
    --fake-origin "${FAKE_SPOTIFY_URL}"
  launch_proc frontend "${APP_URL}/"
}

start_worker() {
  local py="$1"
  stop_proc worker
  log "starting the operation worker on ${WORKER_CONTROL_URL}"
  harness_cmd "${py}" -- "${py}" -m shuffify.worker
  launch_proc worker "${WORKER_CONTROL_URL}/health"
}

# A whole login through the fake: the app's /login, the fake's consent page
# (as a seeded user), the app's /callback exchanging the code with the fake,
# then the JSON session, which confirms identity and CSRF. Run against
# the redirect URI's own host, so the cookie set at /login comes back at
# /callback.
check_login() {
  local py="$1" jar="${STATE}/login-check.cookies" consent callback answer session
  rm -f "${jar}"
  consent="$(curl --noproxy '*' -sS -c "${jar}" -b "${jar}" -o /dev/null -w '%{redirect_url}' \
    "${APP_URL}/login?legal_consent=true")" || die "GET /login failed"
  # The OAuth hop must go to the local fake, never accounts.spotify.com.
  [[ "${consent}" == "${FAKE_SPOTIFY_URL}/authorize?"* ]] \
    || die "/login redirected to ${consent:-nothing}, not ${FAKE_SPOTIFY_URL}/authorize"
  callback="$(curl --noproxy '*' -sS -o /dev/null -w '%{redirect_url}' "${consent}&fake_user=alice")" \
    || die "the fake Spotify's consent page failed"
  [[ "${callback}" == "${REDIRECT_URI}?"* ]] \
    || die "the fake Spotify's consent step went to ${callback:-nothing}, not ${REDIRECT_URI}"
  answer="$(curl --noproxy '*' -sS -c "${jar}" -b "${jar}" -o /dev/null -w '%{http_code} %{redirect_url}' \
    "${callback}")" || die "GET /callback failed"
  [[ "${answer}" == "302 ${APP_URL}/" ]] || die "/callback answered '${answer}', not a redirect home"
  session="$(curl --noproxy '*' -fsS -b "${jar}" "${APP_URL}/api/session")" || die "GET /api/session failed"
  printf '%s' "${session}" | "${py}" -c '
import json,sys
value=json.load(sys.stdin)
assert value["signed_in"] is True and value["user"]["id"] == "alice" and value["csrf_token"]
' || die "the JSON session did not confirm the seeded user and CSRF token"
  rm -f "${jar}"
  log "login: OAuth through the fake Spotify as alice, JSON session and CSRF confirmed"
}

check_app() {
  local py="$1" body code page
  body="$(curl --noproxy '*' -fsS "${APP_URL}/health")"
  grep -Eq '"status": ?"healthy"' <<<"${body}" || die "/health is not healthy: ${body}"
  log "/health: healthy (database reachable)"
  for page in / /terms /privacy; do
    code="$(url_code "${APP_URL}${page}")"
    [[ "${code}" == 200 ]] || die "GET ${page} returned ${code}"
  done
  log "logged-out pages: / /terms /privacy all 200"
  check_login "${py}"
  check_fake_spotify
  # /health stays healthy if the schema check errors out rather than
  # refusing, so require the app's own confirmation.
  SCHEMA_REVISION="$(sed -n 's/.*Database schema verified at Alembic head (\([^)]*\)).*/\1/p' "${STATE}/app.log" | tail -1)"
  [[ -n "${SCHEMA_REVISION}" ]] || die "the app did not verify its schema at Alembic head; see ${STATE}/app.log"
  log "schema: verified at Alembic head (${SCHEMA_REVISION})"
  [[ "$(url_code "${WORKER_CONTROL_URL}/health")" == 200 ]] \
    || die "the worker scheduler is not ready; see ${STATE}/worker.log"
  if grep -q "APScheduler started" "${STATE}/app.log"; then
    die "the API must not start APScheduler; see ${STATE}/app.log"
  fi
  log "scheduler: ready in the worker (memory job store)"
  # start_app confirmed the guard loads in a harness process before the app
  # started; an empty log now means it refused nothing since.
  check_violations
  log "network guard: active, no blocked connections"
}

# ---------------------------------------------------------------------------
# Checks that mirror CI's backend and css jobs
# ---------------------------------------------------------------------------
run_checks() {
  local py="$1"
  log "checking generated artifacts"
  "${py}" scripts/build-public-pages.py --check
  ./scripts/build-css.sh --check
  log "ruff check ."
  "$(dirname "${py}")/ruff" check .
  log "migration chain integrity"
  "${py}" scripts/check-migration-chain.py
  log "pytest tests/ -m \"not integration\""
  # CI's backend job values plus disabled Flask dotenv loading. Exported
  # SENTRY_DSN or DATABASE_URL values must not reach the suite.
  clean_env "${py}"
  "${CLEAN_ENV[@]}" APP_CONFIG=testing SPOTIFY_CLIENT_ID=test_client_id SPOTIFY_CLIENT_SECRET=test_client_secret \
    SPOTIFY_REDIRECT_URI=http://localhost:8000/callback SECRET_KEY=ci-test-secret-key \
    "${py}" -m pytest tests/ -m "not integration" -q -p no:cacheprovider
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
cmd_setup() {
  local quick=0 py
  [[ "${1:-}" == "--quick" ]] && quick=1
  mkdir -p "${STATE}"
  start_violations_log
  py="$(python_bin)"
  install_deps "${py}"
  ensure_secrets "${py}"
  start_services "${py}"
  migrate "${py}"
  build_frontend "${py}"
  if (( ! quick )); then
    run_checks "${py}"
  fi
  start_fake_spotify "${py}"
  start_app "${py}"
  start_worker "${py}"
  start_frontend "${py}"
  check_app "${py}"
  log "logs: .harness/app.log, .harness/worker.log, .harness/fake-spotify.log; stop: scripts/harness.sh down"
  # The one line on stdout.
  echo "harness: ready at ${APP_URL} (schema ${SCHEMA_REVISION} at head, network guard active)"
}

cmd_down() {
  stop_proc frontend
  stop_proc app
  stop_proc worker
  stop_proc fake-spotify
  if [[ "${SERVICES}" == local ]]; then
    stop_redis
    stop_postgres
  fi
}

cmd_status() {
  local bin pid
  if pid="$(proc_pid frontend)"; then echo "frontend:   running (pid ${pid}) ${APP_URL}"; else echo "frontend:   stopped"; fi
  if pid="$(proc_pid app)"; then echo "api:        running (pid ${pid}) ${API_URL}"; else echo "api:        stopped"; fi
  if pid="$(proc_pid worker)"; then echo "worker:     running (pid ${pid})"; else echo "worker:     stopped"; fi
  if pid="$(proc_pid fake-spotify)"; then echo "spotify:    fake, running (pid ${pid}) ${FAKE_SPOTIFY_URL}"; else echo "spotify:    fake, stopped"; fi
  if [[ "${SERVICES}" == local ]]; then
    bin="$(pg_bin)"
    if [[ -f "${STATE}/pg/data/PG_VERSION" ]] && as_pg "${bin}/pg_ctl" -D "${STATE}/pg/data" status >/dev/null 2>&1; then
      echo "postgres:   running 127.0.0.1:${PG_PORT}/${DB_NAME}"
    else
      echo "postgres:   stopped"
    fi
    if redis-cli -h 127.0.0.1 -p "${REDIS_PORT}" ping >/dev/null 2>&1; then
      echo "redis:      running 127.0.0.1:${REDIS_PORT}"
    else
      echo "redis:      stopped"
    fi
  fi
  if [[ -s "${VIOLATIONS}" ]]; then echo "netguard:   $(wc -l < "${VIOLATIONS}") blocked connection(s), see ${VIOLATIONS}"; else echo "netguard:   no blocked connections"; fi
}

cmd_reset_db() {
  [[ "${SERVICES}" == local ]] || die "reset-db only manages the local database"
  local bin
  stop_proc frontend
  stop_proc app
  stop_proc worker
  start_postgres
  bin="$(pg_bin)"
  log "dropping and re-creating ${DB_NAME}"
  "${bin}/dropdb" -h 127.0.0.1 -p "${PG_PORT}" -U "${DB_USER}" --if-exists "${DB_NAME}"
  "${bin}/createdb" -h 127.0.0.1 -p "${PG_PORT}" -U "${DB_USER}" "${DB_NAME}"
  cmd_setup --quick
}

# The behaviour tests drive the app setup started, over HTTP, as a harness
# process: the same empty environment and network guard as the app. Between
# tests they empty the app's tables, clear its Redis cache and rate limits and
# reset the fake, and they fail on anything the fake records.
cmd_e2e() {
  [[ "$(url_code "${APP_URL}/health")" == 200 ]] \
    || die "the app is not answering ${APP_URL}; run scripts/harness.sh setup first"
  [[ "$(url_code "${FAKE_SPOTIFY_URL}/_fake/health")" == 200 ]] \
    || die "the fake Spotify is not answering ${FAKE_SPOTIFY_URL}; run scripts/harness.sh setup first"
  local py rc=0
  py="$(python_bin)"
  log "behaviour tests: tests/e2e against ${APP_URL} and the fake Spotify"
  # The two settings that say where Playwright's browser is, when either is set.
  harness_cmd "${py}" SHUFFIFY_E2E_APP_URL="${APP_URL}" SHUFFIFY_E2E_FAKE_URL="${FAKE_SPOTIFY_URL}" \
    ${PLAYWRIGHT_BROWSERS_PATH:+"PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH}"} \
    ${XDG_CACHE_HOME:+"XDG_CACHE_HOME=${XDG_CACHE_HOME}"} -- \
    "${py}" -m pytest tests/e2e -p no:cacheprovider "$@"
  # Failed tests or not, show what the network guard blocked and what the
  # fake recorded: either may be why they failed.
  "${HARNESS_CMD[@]}" || rc=$?
  check_violations
  check_fake_spotify
  return "${rc}"
}

usage() {  # the header comment, up to `set -euo pipefail`
  sed -n '2,/^set -euo pipefail/{/^#/s/^# \{0,1\}//p}' "$0"
  exit 2
}

case "${1:-}" in
  setup)    shift; cmd_setup "$@" ;;
  e2e)      shift; cmd_e2e "$@" ;;
  down)     cmd_down ;;
  status)   cmd_status ;;
  reset-db) cmd_reset_db ;;
  *) usage ;;
esac
