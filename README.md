# Stock Intel — US stock discovery with Shariah highlighting

Finds up to five US-listed stocks a day (under $100) that combine unusual
options activity, open-market insider buying and price near a validated support level. Ranking
also uses analyst sentiment, low float and liquidity. It never pads the list and never treats
missing data as a pass.

* Backend: Python 3.13 · FastAPI · SQLAlchemy 2 · Alembic · pandas/NumPy · Celery + Redis
* Data: PostgreSQL 16 + TimescaleDB
* Frontend: Next.js 16 · React 19 · Tailwind 4 · TradingView Lightweight Charts 5
* Providers: SEC EDGAR (free, official) · ORATS · Massive (Polygon) · Financial Modeling Prep

| Doc | |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | architecture, provider comparison, **cost estimates**, data gaps, schema, security, licensing |
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | every formula and threshold |
| [docs/SETUP.md](docs/SETUP.md) | API keys, Docker deployment, operations |
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | how to use each screen |
| [docs/TESTING.md](docs/TESTING.md) | test coverage and what is not yet verified |

Quick start: see [docs/SETUP.md](docs/SETUP.md) (`cp .env.example .env`, fill in keys, `docker compose up -d --build`).

## Status by phase

| Phase | Done | Not done / caveats |
|---|---|---|
| 1 Research & architecture | Provider comparison with costs, gaps, schema, methodology, licensing notes | Vendor prices conflicted between sources; re-check before subscribing |
| 2 Core | DB + migrations (tested on PostgreSQL), provider adapters with retry/rate-limit/cache/health, screening engine, Shariah module (5 methodologies + business reviews), Top-5 ranking, immutable history with revisions | **Adapters not verified against live APIs** (network blocked in build env). Run `python -m app.cli verify-providers`. |
| 3 Trading tools | Position sizing, expectancy (Monte Carlo ruin/drawdown, scenarios), purification, P&L journal with equity/drawdown curves | — |
| 4 Advanced | Scheduled scans (NYSE calendar/DST), email/Telegram alerts, largest-contract options analytics | Block trades need Massive options-trades tier; no WebSocket streaming yet (scheduled polling only); no institutional-ownership or halt feeds |
| 5 Testing & deploy | 48 backend tests, frontend typecheck/lint/build, UI checked in a headless browser, Docker Compose (validated) with nightly backups | Images not built here (no Docker daemon); no live email/Telegram sends |

Research tool only — not investment advice. Shariah screening is an analytical aid, not a fatwa.
