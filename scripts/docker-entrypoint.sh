#!/bin/sh
# Container entrypoint: reconcile the database schema, then hand off to the
# real command.
#
# ============================================================================
# THIS SCRIPT DOES NOT RUN IN PRODUCTION. Do not reason about production
# migrations from anything below this banner.
#
# Production is DigitalOcean App Platform, whose `run_command` REPLACES the
# container ENTRYPOINT rather than being passed to it. So the `exec "$@"` at
# the bottom is never reached, because this file is never entered. Confirmed
# from a deploy log whose first line is `Starting gunicorn`, with none of the
# `entrypoint:` output below it.
#
# The consequence is not academic: production served a stale schema silently
# for weeks, because this file existed and everyone -- humans and bots --
# read it as the mechanism. That is the whole reason for this banner.
#
# Where it DOES run and is correct: `docker run`, docker-compose, and any
# platform that honours ENTRYPOINT. Do not delete it. It is correct code in
# an inert position.
#
# The intended production fix is a `PRE_DEPLOY` job (`db-migrate`) running
# `flask db upgrade` before traffic cuts over. It is PROPOSED, NOT
# IMPLEMENTED -- tracked in
# https://github.com/chrisrogers37/shuffify/issues/531. Until it lands,
# nothing applies migrations in production and a migration freeze is in
# effect. The env-var contract below (SCHEDULER_ENABLED=false,
# SHUFFIFY_MIGRATION_STEP=1) is the spec that job must reproduce.
# ============================================================================
#
# Migrations run here -- one process, before Gunicorn forks its workers --
# rather than inside the Flask app factory, ON THE PLATFORMS WHERE THIS FILE
# ACTUALLY RUNS (see the banner above; production is not one of them). That
# is what keeps concurrent
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

# Surrounding whitespace is trimmed before matching, so this predicate agrees
# with the app factory's `_env_is_true` (`.strip().lower()`) on every input.
# The two are separate implementations of one operator-facing switch: a value
# pasted into a platform console field can carry a stray space, and a variable
# documented as clearing both gates has to clear both or neither.
is_true() {
    _value=$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]' |
        sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    case "$_value" in
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
