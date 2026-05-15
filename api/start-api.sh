#!/bin/bash
# Start the API server with all environment variables loaded
set -euo pipefail
cd /home/irc-data/code/sailratings/api
source /home/irc-data/.env
export PYTHONPATH=src
exec /usr/bin/python3 -m uvicorn irc_data.api.app:app --host 0.0.0.0 --port 4100 --reload
