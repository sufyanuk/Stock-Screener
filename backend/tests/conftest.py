"""Test fixtures. ``FakeDataSource`` produces SYNTHETIC data for tests only — it is never wired
into the running application."""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-bytes-long")
os.environ.setdefault("SEC_USER_AGENT", "StockIntel tests test@example.com")
# Most API tests exercise the multi-user login; single-user mode has its own tests.
os.environ.setdefault("SINGLE_USER_MODE", "false")

from app import market_calendar as mc  # noqa: E402
from app.providers.http import ProviderError  # noqa: E402
from app.screening.insider import InsiderTxn  # noqa: E402
from app.screening.shariah import Fundamentals  # noqa: E402
from app.screening.types import Freshness, Provenance  # noqa: E402
from app.services.datasource import AnalystData, OptionsDayRow  # noqa: E402

AS_OF = date(2026, 10, 30)
SESSIONS = mc.previous_sessions(AS_OF, 400)


def prov(name="fake"):
    return Provenance(name, Freshness.EOD, datetime.now(timezone.utc), None)


def oscillating_bars(n=400, base=22.5, amp=2.5):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in SESSIONS[-n:]])
    x = base + amp * np.sin(np.arange(n) / 8.0)
    x[-6:] = [21.0, 20.6, 20.3, 20.1, 20.2, 20.3]
    c = pd.Series(x, index=idx)
    return pd.DataFrame({"open": c * 0.995, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 2_000_000.0})


class FakeDataSource:
    errors: list[str] = []
    TICKERS = {
        "GOOD": {"cik": "0000000001", "sic": "3571", "name": "Good Widgets Inc", "put_last": 300},
        "PCR": {"cik": "0000000002", "sic": "3571", "name": "High Put Corp", "put_last": 3000},
        "BANK": {"cik": "0000000003", "sic": "6022", "name": "Some Bancorp", "put_last": 300},
        "PRICY": {"cik": "0000000004", "sic": "3571", "name": "Pricey Inc", "put_last": 300, "price": 150.0},
    }

    def __init__(self, fail_analyst=False):
        self.fail_analyst = fail_analyst

    def describe(self):
        return {"fake": "configured"}

    async def universe(self):
        return [{"cik": v["cik"], "ticker": k, "name": v["name"], "exchange": "Nasdaq"} for k, v in self.TICKERS.items()]

    async def options_day(self, day):
        rows = []
        i = SESSIONS.index(day) if day in SESSIONS else 0
        for t, v in self.TICKERS.items():
            last = day == AS_OF
            call = 9000 if last else 900
            put = v["put_last"] if last else 100
            iv = 0.2 + 0.4 * ((i % 252) / 251) if not last else 0.45
            rows.append(OptionsDayRow(t, day, call, put, iv, None, v.get("price", 20.3)))
        return rows, prov("fake-options")

    async def option_contracts(self, ticker):
        return [], None, prov()

    async def bars(self, ticker, start, end):
        df = oscillating_bars()
        if self.TICKERS.get(ticker, {}).get("price"):
            df = df * 0 + 150.0
            df["volume"] = 1e6
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))], prov("fake-bars")

    async def quote(self, ticker):
        return {"price": None, "day_change_pct": None, "bid": 20.28, "ask": 20.32}, prov()

    filing_fetches = 0  # counts simulated per-filing downloads (for incremental-ingest tests)

    async def insider_filings(self, day, skip=None, limit=None):
        if day != AS_OF:
            return [], prov(), set(), 0
        t = InsiderTxn("0001-26-1", 1, "0000000001", "GOOD", "Jane Doe", "9", "Chief Executive Officer", False, True,
                       False, date(2026, 10, 28), AS_OF, "P", "A", 2_000_000, 20.0, 3_000_000, "D")
        rows = [t, InsiderTxn(**(t.__dict__ | {"owner_name": "Doe Trust", "owner_cik": "10", "direct_indirect": "I"})),
                InsiderTxn(**(t.__dict__ | {"ticker": "PCR", "issuer_cik": "0000000002", "accession": "0002-26-1"})),
                InsiderTxn(**(t.__dict__ | {"ticker": "BANK", "issuer_cik": "0000000003", "accession": "0003-26-1"}))]
        todo = sorted({r.accession for r in rows} - (skip or set()))
        batch = set(todo if limit is None else todo[:limit])
        FakeDataSource.filing_fetches += len(batch)
        return [r for r in rows if r.accession in batch], prov("sec"), batch, len(todo) - len(batch)

    async def fundamentals(self, ticker, cik, as_of, market_cap, a24, a36):
        v = next(x for x in self.TICKERS.values() if x["cik"] == cik)
        f = Fundamentals(ticker, v["name"], v["sic"], date(2026, 6, 30), date(2026, 8, 1), "fake XBRL", 1e9, 5e7, 8e7,
                         5e7, 4e8, 1e6)
        f.shares_outstanding_date = date(2026, 9, 30)
        return f, 100_000_000.0, prov("sec")

    async def analyst(self, ticker):
        if self.fail_analyst:
            raise ProviderError("fmp", "simulated outage", 503)
        return AnalystData({"strong_buy": 3, "buy": 5, "hold": 2, "sell": 0, "strong_sell": 0},
                           {"avg": 26.0, "high": 30.0, "low": 21.0, "median": 26.0}, []), prov("fmp")

    async def shares_outstanding(self, ticker):
        if ticker == "BANK":
            raise ProviderError("fmp", "no shares outstanding for BANK", 404)
        return {"GOOD": 100_000_000.0, "PCR": 400_000_000.0}.get(ticker, 50_000_000.0), "fmp"

    async def float_info(self, ticker):
        return {"float_shares": 15_000_000, "outstanding_shares": 100_000_000}, prov("fmp")

    async def news(self, ticker):
        return [{"title": f"{ticker} news", "url": "https://example.com/n", "published": "2026-11-01", "publisher": "X"}], prov()

    async def company_quote(self, ticker):
        return {"name": f"{ticker} Inc", "price": 20.3, "day_change_pct": 0.5, "pe": 18.4, "market_cap": 2.03e9}, prov("fmp")

    async def earnings_calendar(self, start, end):
        from datetime import timedelta as td

        rows = [("GOOD", 2, 0.25, "post-market"), ("BANK", 5, 1.1, "pre-market"), ("LATE", 10, None, None)]
        return [{"symbol": s, "name": f"{s} Inc", "report_date": (start + td(days=d)).isoformat(),
                 "fiscal_date_ending": None, "eps_estimate": e, "time_of_day": t} for s, d, e, t in rows], prov("fmp")

    market_calls = 0

    async def market_closes(self, day):
        """Whole-market closes for one session. GOOD gains 10% every November, BANK loses 5% every
        November, FLAT never moves; plus symbols that must be filtered out (warrant / long name)."""
        FakeDataSource.market_calls += 1
        novembers = sum(1 for y in range(2010, day.year + 1) for m in [11] if (y, m) <= (day.year, day.month))
        return {"GOOD": 100 * 1.10 ** novembers, "BANK": 100 * 0.95 ** novembers, "FLAT": 50.0,
                "PENNY": 0.5, "ABC.W": 1.0, "TOOLONG": 10.0}, prov("massive")


@pytest.fixture
def db(tmp_path):
    from app.db import SessionLocal, engine, reset_engine_for_tests
    from app.models import Base

    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/t.db"
    reset_engine_for_tests(url)
    Base.metadata.drop_all(engine())
    Base.metadata.create_all(engine())
    s = SessionLocal()
    yield s
    s.close()
