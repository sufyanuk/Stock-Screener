# Setup & operations

## 1. Get API access (see ARCHITECTURE.md for costs)

| Variable | Where | Minimum plan |
|---|---|---|
| `SEC_USER_AGENT` | none — set `"YourApp you@domain.com"` | free |
| `ORATS_API_KEY` | orats.com → Data API | Delayed Data API |
| `MASSIVE_API_KEY` | massive.com (Polygon.io keys work) | Stocks + Options (Starter or higher) |
| `FMP_API_KEY` | financialmodelingprep.com | Starter |
| `SMTP_*`, `TELEGRAM_BOT_TOKEN` | optional | — |

Without a key, the affected criteria show as **missing** and nothing qualifies. This is intentional.

## 2. Run with Docker (recommended)

```bash
git clone https://github.com/sufyanuk/Stock-Screener.git && cd Stock-Screener
```

```bash
cp .env.example .env   # then fill in the values
```

```bash
docker compose up -d --build
```

```bash
docker compose exec api python -m app.cli create-admin you@example.com
```

```bash
docker compose exec api python -m app.cli verify-providers AAPL
```

Open http://localhost:3000. The first scan back-fills about 260 sessions of options history (≈520 ORATS requests).
Run it once manually:

```bash
docker compose exec api python -m app.cli scan --kind close
```

After that, Celery beat runs scans at 08:30, 09:45, every 30 minutes from 10:00 to 15:30 and at 16:20 New York time.
Weekends, NYSE holidays and early closes are skipped, and DST is handled.
Backups: a nightly `pg_dump` goes to `./backups/`. Prune old dumps periodically.

## 3. Local development

```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
```

```bash
alembic upgrade head && uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && BACKEND_URL=http://localhost:8000 npm run dev
```

## 4. Operating notes
* **Data health** card on the dashboard: `fresh` < 1 h since the last successful call, `stale` < 24 h, otherwise `down`.
* A scan with provider errors finishes as `partial`. Affected criteria are *missing* (never passed) and the errors are listed on the dashboard.
* Admin → Settings: every threshold change needs a note and is stored as a new version.
* Shariah "Needs Review" stocks in review industries need an admin business review.
* To correct a recommendation, use `POST /api/recommendations/{id}/revise` (admin). The original is kept.
