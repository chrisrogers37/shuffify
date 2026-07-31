#!/bin/sh
# Container entrypoint: reconcile the database schema, then hand off to the
# real command.
#
# Migrations run here -- one process, before Gunicorn forks its workers --
# rather than inside the Flask app factory. That is what keeps concurrent
# workers from racing on the same upgrade, and it lets the app factory treat
# "schema is at head" as an invariant to verify rather than work to perform
# while requests are already arriving.
#
# A failed upgrade stops the container. That is the point: a stale schema
# produces data corruption and errors that read as application bugs.
# SHUFFIFY_ALLOW_SCHEMA_DRIFT is the break-glass override for an operator who
# needs to restore serving before the migration itself is fixed; it is read
# here and by the app factory's schema check, so one variable clears both.

set -eu

is_true() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        1 | true | yes | on) return 0 ;;
        *) return 1 ;;
    esac
}

# `flask db upgrade` imports run.py, which builds the whole application:
#   SCHEDULER_ENABLED=false     - otherwise this short-lived migration process
#                                 starts APScheduler and can execute jobs.
#   SHUFFIFY_MIGRATION_STEP=1   - otherwise the app factory's schema check
#                                 refuses to build the app for the very
#                                 schema state this command is here to fix.
echo "entrypoint: applying database migrations"
if SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1 flask db upgrade; then
    echo "entrypoint: migrations applied"
elif is_true "${SHUFFIFY_ALLOW_SCHEMA_DRIFT:-}"; then
    echo "entrypoint: migrations FAILED -- continuing because" \
        "SHUFFIFY_ALLOW_SCHEMA_DRIFT is set. The application will serve" \
        "against the current schema." >&2
else
    echo "entrypoint: migrations FAILED -- refusing to start. Fix the" \
        "migration, or set SHUFFIFY_ALLOW_SCHEMA_DRIFT=true to start anyway" \
        "and serve against the current schema." >&2
    exit 1
fi

exec "$@"
