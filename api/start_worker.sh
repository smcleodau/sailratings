#!/bin/bash
source .venv/bin/activate
export PYTHONPATH=src
nohup python3 src/irc_data/temporal/worker.py >> /home/irc-data/logs/temporal_worker.log 2>&1 &
echo "Worker started with PID $!"
