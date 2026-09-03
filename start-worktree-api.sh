#!/bin/bash
set -euo pipefail
cd /home/irc-data/code/sailratings/worktrees/3ce37ffe-f467-81fe-9376-e3dd1b43c108/api

source /home/irc-data/.credentials/op-service-account.env
if [ -f /home/irc-data/.credentials/clerk.env ]; then
    source /home/irc-data/.credentials/clerk.env
fi

export OP_CACHE=false
export PYTHONPATH=src

exec /home/irc-data/.local/bin/op run \
    --environment vzhxzxt7mgb4tolyepo5wqzcz4 \
    -- /home/irc-data/code/sailratings/api/.venv/bin/python -m uvicorn irc_data.api.app:app \
        --host 0.0.0.0 --port 4100 --reload
