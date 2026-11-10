#!/usr/bin/env bash
# One-time setup in a GitHub Codespace (or any Linux machine): install and build the app.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip install --user -q -r backend/requirements.txt
(cd frontend && npm ci --no-audit --no-fund && JOBS_ON_GITHUB=1 BACKEND_URL=http://localhost:8000 npm run build)
rm -rf frontend/.next/standalone/.next/static
cp -r frontend/.next/static frontend/.next/standalone/.next/static
[ -d frontend/public ] && { rm -rf frontend/.next/standalone/public; cp -r frontend/public frontend/.next/standalone/public; } || true
git rev-parse HEAD > frontend/.next/BUILT_COMMIT 2>/dev/null || true
echo "Setup done."
