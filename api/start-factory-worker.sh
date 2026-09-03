#!/bin/bash
# Start the factory's Temporal worker (EpicExecutionWorkflow: OpenHands
# lane-worker, reviewer, sprint-manager agents), with secrets loaded from
# 1Password + the local LiteLLM credentials stopgap.
#
# Deliberately does NOT set LITELLM_MODEL_HINT. Each role picks its own
# default in code (irc_data/temporal/orchestrator/activities.py):
# lane-worker -> coding-deep (trial, was coding-fast; see the comment at
# its call site), reviewer -> review-independent, sprint-manager ->
# coding-deep. A blanket LITELLM_MODEL_HINT env var overrides every one of
# those identically — that's what this repo ran under for this whole
# session before 2026-09-03, which meant the reviewer was never actually
# independent of the worker it was reviewing.
#
# To restart manually:
#   /home/irc-data/code/sailratings/api/start-factory-worker.sh
# Normally supervised by the sailing-factory-worker.service user unit.

set -euo pipefail

cd /home/irc-data/code/sailratings/api

source /home/irc-data/.credentials/op-service-account.env
source /home/irc-data/.credentials/litellm.env

export OP_CACHE=false
export PYTHONPATH=src
export TEMPORAL_ADDRESS=localhost:7233
export OH_PERSISTENCE_DIR=/home/irc-data/.openhands_state
export OPENHANDS_SUPPRESS_BANNER=1

# Environment 5ux2t36klqustptq3lxu6djem4 carries SAILRATINGS_NOTION_TOKEN
# (Notion card updates) and GH_TOKEN (PR creation) — both required, not in
# the main dev Environment.
exec /home/irc-data/.local/bin/op run \
    --environment 5ux2t36klqustptq3lxu6djem4,vzhxzxt7mgb4tolyepo5wqzcz4 \
    -- /home/irc-data/code/sailratings/api/.venv/bin/python -u -m irc_data.temporal.orchestrator.worker
