#!/bin/sh
# Upgrade the schema in a separate pre-deploy process before services start.
set -eu
SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1 exec flask db upgrade
