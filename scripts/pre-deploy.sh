#!/bin/sh
# Upgrade the schema in a separate pre-deploy process before services start.
# While SHUFFIFY_PREDEPLOY_CREATE_SCHEMA is set, build a new database first:
# flask create-schema builds only on the Neon endpoint the variable names.
set -eu
if [ -n "${SHUFFIFY_PREDEPLOY_CREATE_SCHEMA:-}" ]; then
  SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1 flask create-schema
fi
SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1 exec flask db upgrade
