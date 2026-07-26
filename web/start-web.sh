#!/bin/bash
set -euo pipefail
cd /home/irc-data/code/sailratings/web

source /home/irc-data/.credentials/op-service-account.env

export OP_CACHE=false

exec /home/irc-data/.local/bin/op run \
    --environment vzhxzxt7mgb4tolyepo5wqzcz4 \
    -- /usr/bin/node /home/irc-data/code/sailratings/web/.next/standalone/server.js
