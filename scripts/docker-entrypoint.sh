#!/bin/sh
# Forward the configured process and its signals. Migrations run separately.
set -eu
exec "$@"
