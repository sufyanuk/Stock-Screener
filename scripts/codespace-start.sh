#!/usr/bin/env bash
# Start the backend (port 8000) and the website (port 3000) in the background; logs in /tmp/screener-*.log.
# Pages show stored data only (JOBS_ON_GITHUB=1): heavy loads keep running on GitHub Actions.
set -uo pipefail
cd "$(dirname "$0")/.."
export JOBS_ON_GITHUB=1
if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set: add it as a Codespaces secret (see .devcontainer/devcontainer.json), then rebuild." >&2
  exit 0
fi
[ -d frontend/.next/standalone ] || bash scripts/codespace-setup.sh
pkill -f "uvicorn app.main:app" 2>/dev/null; pkill -f "standalone/server.js" 2>/dev/null
(cd backend && python -m alembic upgrade head >/tmp/screener-migrate.log 2>&1 || true
 nohup python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/screener-api.log 2>&1 &)
(cd frontend/.next/standalone && PORT=3000 HOSTNAME=0.0.0.0 nohup node server.js >/tmp/screener-web.log 2>&1 &)
echo "Starting… the Stock Screener opens on port 3000 in a few seconds."
