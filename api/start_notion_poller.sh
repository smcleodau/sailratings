#!/bin/bash
set -euo pipefail

cd /home/irc-data/code/sailratings/api

source /home/irc-data/.credentials/op-service-account.env

export OP_CACHE=false
export PYTHONPATH=src
export TEMPORAL_ADDRESS=localhost:7233
export FACTORY_MAX_CONCURRENT="${FACTORY_MAX_CONCURRENT:-5}"
export FACTORY_MAX_PER_POLL="${FACTORY_MAX_PER_POLL:-5}"

LOG=/home/irc-data/logs/factory-poller.log
mkdir -p /home/irc-data/logs

exec /home/irc-data/.local/bin/op run \
    --environment 5ux2t36klqustptq3lxu6djem4,vzhxzxt7mgb4tolyepo5wqzcz4 \
    -- env \
        PYTHONPATH=src \
        TEMPORAL_ADDRESS=localhost:7233 \
        FACTORY_MAX_CONCURRENT="${FACTORY_MAX_CONCURRENT}" \
        FACTORY_MAX_PER_POLL="${FACTORY_MAX_PER_POLL}" \
    /home/irc-data/code/sailratings/api/.venv/bin/python -m irc_data.temporal.orchestrator.notion_poller \
    >> "${LOG}" 2>&1
