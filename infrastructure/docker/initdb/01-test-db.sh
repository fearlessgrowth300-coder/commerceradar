#!/bin/sh
# Runs once on first Postgres start: a separate DB so `make test` never touches dev data.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE \"${POSTGRES_DB}_test\";"
