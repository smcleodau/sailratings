#!/bin/bash
# Deploy origin/main to production on this box.
#   1. fast-forward the prod checkout to origin/main
#   2. run migrations (prod and dev share this box's Postgres)
#   3. rebuild the website with the prod 1Password Environment's NEXT_PUBLIC_* values
#   4. restart both services and smoke-test them locally
# Promote first: git push origin origin/develop:refs/heads/main
set -euo pipefail
PROD_DIR=/home/irc-data/code/sailratings-prod
PROD_OP_ENV=ewoupn6p7qbyaxzrrda7tmhkr4
VENV=/home/irc-data/code/sailratings/api/.venv
source /home/irc-data/.credentials/op-service-account.env

git -C "$PROD_DIR" fetch -q origin
git -C "$PROD_DIR" merge --ff-only -q origin/main
echo "prod checkout at $(git -C "$PROD_DIR" log --oneline -1)"

(cd "$PROD_DIR/api" && DATABASE_URL="postgresql+psycopg://irc:irc@localhost:5433/irc_data" "$VENV/bin/alembic" upgrade head)

cd "$PROD_DIR/web"
npm ci --no-audit --no-fund --loglevel=error
OP_CACHE=false /home/irc-data/.local/bin/op run --environment "$PROD_OP_ENV" -- \
  env NODE_ENV=production ENVIRONMENT=production NEXT_PUBLIC_ENVIRONMENT=production npm run build

systemctl --user restart sailing-prod-api.service sailing-prod-web.service
for i in $(seq 1 30); do
  a=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:4300/v1/health || true)
  w=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:4400/ || true)
  [ "$a" = 200 ] && [ "$w" = 200 ] && { echo "prod healthy: api $a, web $w"; exit 0; }
  sleep 2
done
echo "prod NOT healthy: api $a, web $w" >&2; exit 1
