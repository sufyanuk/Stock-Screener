# Architecture, data providers & costs

## 1. System overview

```
 Browser ──► Next.js 16 (web)  ──/api/* rewrite──►  FastAPI (api)  ──►  PostgreSQL 16 + TimescaleDB
              React 19, Tailwind 4,                   │   SQLAlchemy 2, Alembic       (bars, options history,
              Lightweight Charts 5                    │                                filings, scans, journal)
                                                      ├──► Redis (Celery broker / results)
                                                      │
                Celery beat (NY time) ─► Celery worker ┴──► Provider adapters
                pre-market · open · every 30m · close        SEC EDGAR · ORATS · Massive · FMP
```

* **No API key ever reaches the browser.** The browser only talks to the Next.js server; Next.js
  proxies `/api/*` to FastAPI, which holds the keys (env vars only).
* **Provider adapters** (`backend/app/providers/`) sit behind one `DataSource` interface
  (`services/datasource.py`). Replacing a provider means writing one adapter class.
* **Shared HTTP client** (`providers/http.py`): token-bucket rate limiting per provider,
  exponential backoff with jitter on 429/5xx (honours `Retry-After`), TTL cache, key redaction in
  error messages. Provider health (last success, last error, failure counts) is recorded per call.
* **Scan engine** (`services/engine.py`) – ingest → funnel → evaluate → rank → persist → notify.
  Every run stores the full config snapshot, the list of relaxed criteria, the funnel counts and
  every provider error. A failed run never produces recommendations.

### Funnel (keeps whole-market scanning inside API budgets)

| Stage | Universe | Data | Requests |
|---|---|---|---|
| 0 | SEC `company_tickers_exchange.json` → NYSE / Nasdaq / NYSE American | SEC | 1 / week |
| 1 | every optionable ticker | ORATS `hist/cores` + `hist/summaries` for **all tickers** of one date | 2 / day (≈520 one-off backfill) |
| 1 | price < $100 + options criteria | stored history (no requests) | 0 |
| 2 | survivors (typically < 50) | SEC XBRL & submissions, Massive bars/quote/chain, FMP analyst/float | ≈ 8 per survivor |
| — | insider filings, whole market | SEC daily Form-4 index + each filing | ≈ 300–800 / day (free) |

## 2. Data-provider comparison

Prices are from public pricing pages and third-party summaries checked in Nov 2026. Several
sources conflicted, so **re-check each vendor's pricing page before you subscribe**.

| Need | Provider | Coverage / notes | Freshness | Indicative cost | Licensing |
|---|---|---|---|---|---|
| Insider transactions (Form 4), SIC, XBRL financials, shares outstanding | **SEC EDGAR** (chosen, primary) | Official source. Transaction codes (P/S/A/M/G/F…) let us isolate open-market buys. | Filing-time (minutes after acceptance) | **Free** (≤10 req/s, descriptive User-Agent) | Public domain |
| Options volume, IV history, IV rank, all tickers per date | **ORATS Data API** (chosen) | One request returns every ticker for a date. 15+ yrs history. | Delayed plan: 15-min delayed / EOD | Delayed ≈ **$99/mo** (20k req); Live ≈ $199/mo; intraday $399–599 (sources differ) | Personal/internal use; redistribution needs agreement |
| Prices (daily bars, snapshots), option-chain snapshots, short interest, news | **Massive.com** (formerly Polygon.io) (chosen) | Grouped daily bars, per-underlying chain snapshot with OI/greeks. | Plan-dependent (15-min delayed on lower tiers; real-time on Advanced) | Stocks Starter ≈ $29, Developer ≈ $79, Advanced ≈ $199/mo; **options is a separate asset-class plan** (≈ $29–199) | Individual plans are **non-professional only**; commercial use needs business pricing |
| Analyst consensus, price targets, grade history, float | **Financial Modeling Prep** (chosen) | `grades-consensus`, `price-target-consensus`, `grades`, `shares-float` | EOD | Starter ≈ $22, Premium ≈ $59, Ultimate ≈ $149/mo (annual billing; free tier 250 calls/day) | Display/redistribution terms vary by plan |
| Options + quotes (alternative) | Tradier | Chains with greeks via brokerage API | Real-time with account | Free with funded brokerage account; data-only plans extra | Brokerage ToS |
| Single-vendor alternative | Alpha Vantage Premium | Has options history, insider data, overview | Premium tiers | $49.99–$249.99/mo (by rate limit) | Realtime options for personal use only |
| Analyst accuracy (track records) | TipRanks | Only reliable source of analyst success rates | — | Enterprise API, quote-based | Restrictive |
| Shariah screening data | Zoya API / Musaffa API | Turnkey compliance status | — | Zoya commercial tier listed at ≈ $1,399/mo; Musaffa custom pricing | Commercial |

### Why this combination
* **SEC EDGAR is the primary insider source.** We checked Alpha Vantage's insider endpoint
  against a live response: it reports only `A`/`D`-style flags plus `M`/`F`/`S` and no transaction
  code `P`. So it **cannot tell an open-market purchase from a grant**, which the spec forbids
  counting. Form 4 XML carries the exact code.
* **ORATS** is the most cost-effective way to get *market-wide* daily options volume and an IV
  series long enough for a 52-week IV rank: two requests per day instead of thousands of per-ticker
  chain calls.
* **Massive** supplies prices, chain snapshots (largest contracts, OI, moneyness), short interest and news.
* **FMP** is the cheapest source of analyst consensus and grade history.
* **Shariah** is computed in-house from SEC XBRL using a documented, configurable methodology,
  with an admin business-review workflow. A commercial Shariah API can be added later as an
  adapter. It is not a dependency because of its cost.

### Estimated monthly cost (personal, non-professional use)

| Tier | Components | ≈ USD / month |
|---|---|---|
| Minimum viable (EOD) | SEC (free) + ORATS Delayed $99 + Massive Stocks Starter $29 + Massive Options Starter ≈$29 + FMP Starter $22 | **≈ $180** |
| Recommended | ORATS Delayed $99 + Massive Stocks Developer $79 + Options Developer ≈$79 + FMP Premium $59 | **≈ $315** |
| Real-time | ORATS Live $199 + Massive Advanced stocks $199 + options $199 + FMP Premium $59 | **≈ $660** |
| Commercial/redistribution | Business licences from each vendor | quote-based, usually $1k+ |

Hosting (1 small VM, 2 vCPU / 4 GB, managed backups) adds ≈ $20–60/month.

## 3. Data availability gaps (stated, not papered over)

| Spec item | Status |
|---|---|
| Block trades / opening vs closing | Needs Massive options **trades** (higher tier). Chain snapshots give day aggregates; opening vs closing is reported as an *inference* (volume > prior OI), labelled as such. |
| Verified analyst track records | Not available from FMP. Weighting code exists but is only applied when a sourced success rate is present; otherwise ratings are equal-weighted and the UI says so. |
| Institutional ownership change | Needs 13F aggregation (FMP higher tier or SEC 13F parsing); field is shown as unavailable. |
| Trading halts | No adapter yet; reported as "unknown" in liquidity checks. |
| Mixed-revenue Shariah screening | XBRL has no prohibited-segment revenue; requires admin business review (recorded with reviewer/date). |

## 4. Database schema (Alembic migrations `0001`, `0002`)

| Table | Purpose |
|---|---|
| `users` | email, bcrypt hash, role (`admin`/`user`), notification prefs |
| `app_settings` | append-only config versions (who/when/why) = audit trail |
| `securities` | ticker, CIK, exchange, SIC, active flag (inactive kept for survivorship) |
| `daily_bars` | OHLCV per ticker/session + provider (Timescale hypertable) |
| `options_daily` | call/put volume, OI, IV30, stock price per ticker/session (hypertable) |
| `insider_transactions` | every Form 4 row; unique (accession, txn_index, owner_cik) |
| `fundamentals_snapshots` | optional cache of XBRL extracts |
| `shariah_screens` | per-ticker, per-date screening result (status, ratios, reasons) |
| `business_reviews` | manual Shariah business decisions; superseded, never deleted |
| `scan_runs` | kind, session, config snapshot, relaxed criteria, funnel, errors, status |
| `scan_results` | every evaluated stock with all criterion results |
| `recommendations` | immutable Top-5 rows (ORM blocks UPDATE) |
| `recommendation_revisions` | before/after/reason/author for any correction |
| `journal_fills`, `journal_dividends` | per-user trading journal |
| `provider_health` | per-provider call/failure counters, last error |
| `notification_log` | one notification per user/session/ticker/channel |

## 5. Security
* bcrypt (cost 12) password hashes, HS256 JWT with expiry; production refuses to start with a default secret.
* Role checks on admin routes; journal rows are scoped to the owner.
* Pydantic validation on every input. Config validation rejects price > $100, Top-N > 5, and weights that don't sum to 100.
* Keys are only in backend env vars and are redacted from error messages.
* Security headers on the web app; containers run as non-root.
* **Single-user mode (default):** no app login; every request acts as the owner, and access control is
  the hosting layer (Vercel Authentication). Writes still require the `X-Requested-With` header so other
  websites cannot trigger them. With `SINGLE_USER_MODE=false` the multi-user login below applies.
* Browser sessions (multi-user mode) use an **httpOnly, SameSite=Lax** cookie (`Secure` in production), so page scripts
  cannot read the token. Cookie-authenticated writes must send `X-Requested-With: stockintel`
  (CSRF guard). API clients can still use `Authorization: Bearer`.

## 6. Compliance & licensing considerations
* Not investment advice; disclaimers on every page.
* Shariah output is labelled an analytical aid, not a fatwa.
* Respect each vendor's display/redistribution terms. Individual plans generally forbid showing data to third parties.
* SEC data is public, but you must follow the fair-access policy (rate, User-Agent).
