#!/bin/bash
# Start the API server with all environment variables loaded from 1Password.
#
# Secrets and config come from the "Sail Ratings" vault's dev Environment
# (ID vzhxzxt7mgb4tolyepo5wqzcz4). The 1Password service-account token
# lives at ~/.credentials/op-service-account.env; `op run` resolves the
# Environment and injects every variable into uvicorn's process env.
#
# To restart manually:
#   /home/irc-data/code/sailratings/api/start-api.sh
#
# To run a one-off CLI command against the dev env without restarting the
# server:
#   source ~/.credentials/op-service-account.env
#   op run --environment vzhxzxt7mgb4tolyepo5wqzcz4 -- irc-data <command>

set -euo pipefail

cd /home/irc-data/code/sailratings/api

# Load the 1Password service-account token (OP_SERVICE_ACCOUNT_TOKEN).
# Without it, `op run` errors with "no account found".
source /home/irc-data/.credentials/op-service-account.env

export PYTHONPATH=src

# Resolve the dev Environment and exec uvicorn with secrets injected.
# Using the beta op binary explicitly so a future PATH change doesn't
# silently fall back to a release that lacks `--environment` support.
exec /home/irc-data/.local/bin/op run \
    --environment vzhxzxt7mgb4tolyepo5wqzcz4 \
    -- /home/irc-data/code/sailratings/api/.venv/bin/python -m uvicorn irc_data.api.app:app \
        --host 0.0.0.0 --port 4100 --reload
