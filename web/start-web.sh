#!/bin/bash
set -euo pipefail
cd /home/irc-data/code/sailratings/web

source /home/irc-data/.credentials/op-service-account.env

export OP_CACHE=false

# Load all environment variables from .env.local
if [ -f .env.local ]; then
  set -a
  source .env.local
  set +a
fi

# Next.js standalone does not include static assets by default, we must copy them
cp -r public .next/standalone/ || true
cp -r .next/static .next/standalone/.next/ || true
mkdir -p .next/standalone/node_modules/@clerk
cp -r node_modules/@clerk/* .next/standalone/node_modules/@clerk/ || true

exec /home/irc-data/.local/bin/op run \
    --environment vzhxzxt7mgb4tolyepo5wqzcz4 \
    -- /usr/bin/node /home/irc-data/code/sailratings/web/.next/standalone/server.js
