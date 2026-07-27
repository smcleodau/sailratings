#!/bin/bash
set -euo pipefail

cd /home/irc-data/code/sailratings/api

source /home/irc-data/.credentials/op-service-account.env

export OP_CACHE=false
export PYTHONPATH=src
export TEMPORAL_ADDRESS=localhost:7233

exec /home/irc-data/.local/bin/op run \
    --environment vzhxzxt7mgb4tolyepo5wqzcz4 \
    -- /home/irc-data/code/sailratings/api/.venv/bin/python src/irc_data/temporal/worker.py
