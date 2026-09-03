#!/bin/bash
# Start the factory's Notion poller — picks the active epic and dispatches
# ready tasks as EpicExecutionWorkflows.
#
# To restart manually:
#   /home/irc-data/code/sailratings/api/start-notion-poller.sh
# Normally supervised by the sailing-notion-poller.service user unit.

set -euo pipefail

cd /home/irc-data/code/sailratings/api

source /home/irc-data/.credentials/op-service-account.env

export OP_CACHE=false
export PYTHONPATH=src
export TEMPORAL_ADDRESS=localhost:7233
export FACTORY_MAX_CONCURRENT="${FACTORY_MAX_CONCURRENT:-5}"
export FACTORY_MAX_PER_POLL="${FACTORY_MAX_PER_POLL:-5}"

# SAILRATINGS_NOTION_TOKEN lives in 5ux2t36klqustptq3lxu6djem4, not the main
# dev Environment (see start-factory-worker.sh).
exec /home/irc-data/.local/bin/op run \
    --environment 5ux2t36klqustptq3lxu6djem4,vzhxzxt7mgb4tolyepo5wqzcz4 \
    -- /home/irc-data/code/sailratings/api/.venv/bin/python -u -m irc_data.temporal.orchestrator.notion_poller
