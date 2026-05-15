#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting API server on port ${PORT:-4100}..."
exec uvicorn irc_data.api.app:app --host 0.0.0.0 --port "${PORT:-4100}" --workers 2
