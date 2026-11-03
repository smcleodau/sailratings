#!/bin/bash
# Production API (sailratings.com) served from this box on :4300 via the
# Cloudflare tunnel. Runs the prod checkout (main) with the prod 1Password
# Environment, overriding the two values that must differ here:
#   DATABASE_URL   -> this box's Postgres (the env still names Railway's)
#   ADMIN_PASSWORD -> ~/.credentials/sailratings-prod-admin.env
set -euo pipefail
PROD_DIR=/home/irc-data/code/sailratings-prod
PROD_OP_ENV=ewoupn6p7qbyaxzrrda7tmhkr4
source /home/irc-data/.credentials/op-service-account.env
source /home/irc-data/.credentials/sailratings-prod-admin.env   # ADMIN_PASSWORD_PROD
cd "$PROD_DIR/api"
export OP_CACHE=false PYTHONPATH=src
exec /home/irc-data/.local/bin/op run --environment "$PROD_OP_ENV" -- \
  env DATABASE_URL="postgresql+psycopg://irc:irc@localhost:5433/irc_data" \
      ADMIN_PASSWORD="$ADMIN_PASSWORD_PROD" \
      ENVIRONMENT=production \
  /home/irc-data/code/sailratings/api/.venv/bin/python -m uvicorn irc_data.api.app:app \
      --host 127.0.0.1 --port 4300 --workers 2 --proxy-headers --forwarded-allow-ips 127.0.0.1
