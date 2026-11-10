#!/usr/bin/env bash
# Start the backend (port 8000) and the website (port 3000) in the background; logs in /tmp/screener-*.log.
# Pages show stored data only (JOBS_ON_GITHUB=1): heavy loads keep running on GitHub Actions.
set -uo pipefail
cd "$(dirname "$0")/.."
export JOBS_ON_GITHUB=1 APP_ENV="${APP_ENV:-production}" SINGLE_USER_MODE="${SINGLE_USER_MODE:-true}"
if [ -z "${DATABASE_URL:-}" ]; then
  echo "✗ DATABASE_URL is not set. Add it as a Codespaces secret (github.com → Settings → Codespaces → Secrets,"
  echo "  repository access: Stock-Screener), then stop and reopen this codespace." >&2
  exit 0
fi
if [ -z "${JWT_SECRET:-}" ]; then
  echo "✗ JWT_SECRET is not set. Add it as a Codespaces secret (any long random text), then stop and reopen." >&2
  exit 0
fi
[ -f frontend/.next/standalone/server.js ] || bash scripts/codespace-setup.sh
pkill -f "uvicorn app.main:app" 2>/dev/null; pkill -f "standalone/server.js" 2>/dev/null; sleep 1
(cd backend && python -m alembic upgrade head >/tmp/screener-migrate.log 2>&1) || echo "! database update failed: see /tmp/screener-migrate.log"
# setsid + nohup: keep both running after this script ends (Codespaces stops the startup command's own processes)
(cd backend && setsid nohup python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/screener-api.log 2>&1 </dev/null &)
(cd frontend/.next/standalone && PORT=3000 HOSTNAME=0.0.0.0 BACKEND_URL=http://localhost:8000 \
  setsid nohup node server.js >/tmp/screener-web.log 2>&1 </dev/null &)
for _ in $(seq 1 45); do
  if curl -fs -o /dev/null http://127.0.0.1:3000/api/health; then
    echo "✓ The Stock Screener is running: open the Ports tab and click the globe icon next to 3000."
    exit 0
  fi
  sleep 2
done
echo "✗ The app did not start. Last lines of the logs:"
echo "--- backend (/tmp/screener-api.log)"; tail -n 15 /tmp/screener-api.log
echo "--- website (/tmp/screener-web.log)"; tail -n 15 /tmp/screener-web.log
