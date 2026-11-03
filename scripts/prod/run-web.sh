#!/bin/bash
# Production website on :4400: the standalone Next.js build made by deploy.sh.
set -euo pipefail
cd /home/irc-data/code/sailratings-prod/web/.next/standalone
export NODE_ENV=production PORT=4400 HOSTNAME=127.0.0.1
exec /usr/bin/env node server.js
