# Testing

```bash
cd backend && . .venv/bin/activate && python -m pytest -q
```

```bash
TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db_test python -m pytest -q tests/test_engine_api.py
```

```bash
cd frontend && npx tsc --noEmit && npx eslint src && npm run build
```

## What is covered (48 tests, no warnings; all pass on SQLite, and the engine/API suite also passes on PostgreSQL 16)

| Area | Tests |
|---|---|
| Options | baseline window placement, insufficient history, single-session mode, IV rank, P/C, moneyness, exact-boundary cases (300% passes, IV rank 20 fails) |
| Insider | exclusion of grants/exercises/gifts, duplicate ingestion, joint-report collapse, amendment supersession, look-back, **filing-date look-ahead guard**, before/after ownership |
| Analyst | consensus math, unweighted flag, low-coverage → missing |
| Technical | indicators, support detection, near/extended/breakdown classification, trade plan sanity |
| Float/liquidity | categories, short % float, pump-and-dump flags |
| Shariah | compliant case, bank SIC, debt failure, AAOIFI vs MSCI denominators, missing/stale/SIC/keyword → Needs Review, mixed-revenue review, purification |
| Calendar / scheduler | Thanksgiving closed, early close, DST open times, scheduler skips holidays and post-early-close intraday runs |
| Ranking | never pads the list, mandatory rules, missing never passes, config guards, relaxed-criteria reporting |
| Tools | spreadsheet position example, allocation limit, short with fees/slippage, expectancy formulas, risk of ruin, FIFO P&L with fees/shorts/dividends |
| Providers | Form 4 XML parsing, daily-index de-duplication, XBRL point-in-time extraction (multi-class shares), HTTP retry/backoff, 4xx handling, key redaction, caching |
| End-to-end | full scan through the engine and DB, provider outage → `partial` run with no picks, relaxed config recorded, backtest runs point-in-time, auth/roles, httpOnly cookie session + CSRF guard + logout, admin validation, dashboard joint-filing de-duplication, shares-outstanding report date, recommendation revisions keep the original, journal per-user isolation |

Engine/API tests use a **synthetic** `FakeDataSource` (`tests/conftest.py`) that exists only for tests.

## Not yet verified
* **Live provider responses.** The build environment's network policy blocked sec.gov, ORATS,
  Massive and FMP, so adapters were tested against documented response shapes only.
  Run `python -m app.cli verify-providers` with real keys before relying on the output.
* Docker images were not built (no Docker daemon in the build environment); `docker compose config` validates.
* Celery beat was not run end to end; the schedule gating and market calendar are unit-tested.
* Email/Telegram delivery was not sent for real.
