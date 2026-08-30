#!/bin/bash
set -euo pipefail

cd /home/irc-data/code/sailratings/api

source /home/irc-data/.credentials/op-service-account.env
source /home/irc-data/.credentials/openhands.env

export OP_CACHE=false
export OPENHANDS_SUPPRESS_BANNER=1
export PYTHONPATH=src
export TEMPORAL_ADDRESS=localhost:7233

LOG=/home/irc-data/logs/factory-worker.log
mkdir -p /home/irc-data/logs

exec /home/irc-data/.local/bin/op run \
    --environment 5ux2t36klqustptq3lxu6djem4,vzhxzxt7mgb4tolyepo5wqzcz4 \
    -- env \
        LLM_MODEL="${LLM_MODEL}" \
        LLM_API_KEY="${LLM_API_KEY}" \
        LLM_BASE_URL="${LLM_BASE_URL}" \
        OH_PERSISTENCE_DIR="${OH_PERSISTENCE_DIR}" \
        OPENHANDS_SUPPRESS_BANNER=1 \
        PYTHONPATH=src \
        TEMPORAL_ADDRESS=localhost:7233 \
    /home/irc-data/code/sailratings/api/.venv/bin/python -m irc_data.temporal.orchestrator.worker \
    >> "${LOG}" 2>&1
