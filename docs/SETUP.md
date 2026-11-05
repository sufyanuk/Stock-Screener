# Setup & operations

## 1. Get API access (see ARCHITECTURE.md for costs)

| Variable | Where | Minimum plan |
|---|---|---|
| `SEC_USER_AGENT` | none — set `"YourApp you@domain.com"` | free |
| `ORATS_API_KEY` | orats.com → Data API | Only if Unusual Options → criteria → Options data source = ORATS (default is CSV import) |
| `MASSIVE_API_KEY` | massive.com (Polygon.io keys work) | Stocks + Options (Starter or higher) |
| `FMP_API_KEY` | financialmodelingprep.com | Starter — analysts, P/E, float, earnings calendar, news & price history (stock panel and earnings screen) |
| Seasonality data | none — import your historical OHLCV CSV on the Seasonality tab | — |
| `ALPHAVANTAGE_API_KEY` | alphavantage.co (free key) | Optional — earnings calendar and analyst Strong Buy counts if FMP is not set (free tier: 25 requests/day) |
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

No account is needed: the app runs in **single-user mode** (`SINGLE_USER_MODE=true`, the default) and
every request acts as the owner. Keep the deployment private (Vercel Authentication, a VPN, or an
authenticating reverse proxy). To use logins instead, set `SINGLE_USER_MODE=false` and create an admin:

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
* Criteria are edited on the tab they belong to (Unusual Options, Insider Buying, Watchlist); every change is stored as a new version.
* Shariah "Needs Review" stocks in review industries need a business review (Watchlist tab).
* To correct a recommendation, use `POST /api/recommendations/{id}/revise` (admin). The original is kept.

## 5. Deploying on Vercel

`vercel.json` deploys both parts as one project using Vercel **Services**:
`frontend/` (Next.js) serves the pages and `backend/` (FastAPI, `app.main:app`) serves `/api/*`.

| Docker setup | Vercel equivalent |
|---|---|
| PostgreSQL container | **Neon** Postgres (Vercel Marketplace). Connecting it sets `DATABASE_URL` / `DATABASE_URL_UNPOOLED`. |
| `alembic upgrade head` on start | `AUTO_MIGRATE=1`: migrations run at cold start under a Postgres advisory lock |
| Celery beat + worker + Redis | **Vercel Cron** calls `/api/cron/scan`. The Hobby plan allows one cron per day, so `vercel.json` runs the **closing scan** on weekdays at 21:15 UTC (16:15–17:15 New York). The backend maps the time to premarket / open / intraday / close in New York time and skips holidays. On Pro you can use `"0,30 12-22 * * 1-5"` to get every scan window. |
| Manual scan in the background | Runs inside the request (serverless instances can freeze after responding) |

Environment variables (project → Settings → Environment Variables): `APP_ENV=production`, `JWT_SECRET`,
`CRON_SECRET`, `AUTO_MIGRATE=1`, `BACKFILL_SESSIONS_PER_RUN=60`, `INSIDER_FILINGS_PER_RUN=400`, `SEC_USER_AGENT`, plus the provider keys
and optional SMTP/Telegram values from `.env.example`.

**Access:** there is no app login (single-user mode). The project's **Vercel Authentication** (Settings →
Deployment Protection) is what keeps the site private: only members of your Vercel team can open it. Leave
it enabled. If you add a custom domain, choose a protection setting that covers it, because the default
"all except custom domains" would leave a custom domain public.

The first options backfill (~260 sessions) is spread over several scans (60 sessions each) so that each
invocation finishes within the function time limit (300 s on Hobby); Form 4 filings are fetched
incrementally (at most 400 new filings per scan, already-processed filings are skipped). Until a backfill
completes, scans finish as `partial` and say so.
