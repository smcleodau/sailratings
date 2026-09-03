#!/bin/bash
# Start the persistent Temporal worker that actually executes scraper runs
# (SourceRunWorkflow, ScheduleSyncLoopWorkflow) on the "data-pipeline" task
# queue, with secrets loaded from 1Password.
#
# Until this had a supervised process, every scheduled scraper run started
# and then sat forever with no worker to pick it up — schedules existed and
# fired, but nothing executed them. See git history on
# irc_data/temporal/worker/main.py and irc_data/temporal/schedules/registry.py
# (task-queue drift fix) for the incident this closes out.
#
# To restart manually:
#   /home/irc-data/code/sailratings/api/start-source-worker.sh
# Normally supervised by the sailing-source-worker.service user unit.

set -euo pipefail

cd /home/irc-data/code/sailratings/api

source /home/irc-data/.credentials/op-service-account.env

export OP_CACHE=false
export PYTHONPATH=src
export TEMPORAL_ADDRESS=localhost:7233

exec /home/irc-data/.local/bin/op run \
    --environment vzhxzxt7mgb4tolyepo5wqzcz4 \
    -- /home/irc-data/code/sailratings/api/.venv/bin/python -u -m irc_data.temporal.worker.main
