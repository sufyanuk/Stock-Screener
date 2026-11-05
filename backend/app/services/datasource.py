"""Data-source abstraction used by the scan engine.

``LiveDataSource`` composes the provider adapters with fallbacks. Every call returns values plus
a ``Provenance``; failures raise ``ProviderError`` which the engine records — the affected
criterion becomes MISSING, never PASS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

import pandas as pd

from app.providers.http import NotConfigured, ProviderError
from app.screening.analyst import RatingAction
from app.screening.insider import InsiderTxn
from app.screening.options import ContractActivity
from app.screening.shariah import Fundamentals
from app.screening.types import Freshness, Provenance


@dataclass
class OptionsDayRow:
    ticker: str
    session: date
    call_volume: int | None
    put_volume: int | None
    iv30: float | None
    provider_iv_rank: float | None
    stock_price: float | None
    call_oi: int | None = None
    put_oi: int | None = None


@dataclass
class AnalystData:
    counts: dict[str, int] | None
    targets: dict | None
    actions: list[RatingAction] = field(default_factory=list)


class DataSource(Protocol):
    def describe(self) -> dict[str, str]: ...
    async def universe(self) -> list[dict]: ...
    async def options_day(self, day: date) -> tuple[list[OptionsDayRow], Provenance]: ...
    async def option_contracts(self, ticker: str) -> tuple[list[ContractActivity], float | None, Provenance]: ...
    async def bars(self, ticker: str, start: date, end: date) -> tuple[pd.DataFrame, Provenance]: ...
    async def quote(self, ticker: str) -> tuple[dict, Provenance]: ...
    async def insider_filings(self, day: date, skip: set[str] | None = None, limit: int | None = None
                              ) -> tuple[list[InsiderTxn], Provenance, set[str], int]:
        """Returns (transactions, provenance, accessions processed, filings left unfetched)."""
        ...
    async def fundamentals(self, ticker: str, cik: str, as_of: date, market_cap: float | None,
                           avg_24: float | None, avg_36: float | None) -> tuple[Fundamentals, float | None, Provenance]: ...
    async def analyst(self, ticker: str) -> tuple[AnalystData, Provenance]: ...
    async def float_info(self, ticker: str) -> tuple[dict, Provenance]: ...
    async def news(self, ticker: str) -> tuple[list[dict], Provenance]: ...
    async def company_quote(self, ticker: str) -> tuple[dict, Provenance]: ...
    async def earnings_calendar(self, start: date, end: date) -> tuple[list[dict], Provenance]: ...
    async def market_closes(self, day: date) -> tuple[dict[str, float], Provenance]: ...


class LiveDataSource:
    """SEC EDGAR (insiders, fundamentals, SIC) + ORATS (options) + Massive (prices/chains) + FMP (analyst/float)."""

    def __init__(self):
        from app.providers.market import Fmp, Massive, Orats
        from app.providers.sec_edgar import SecEdgar

        self.errors: list[str] = []
        self.sec = self._try(SecEdgar)
        self.orats = self._try(Orats)
        self.massive = self._try(Massive)
        self.fmp = self._try(Fmp)
        from app.providers.market import AlphaVantage

        self.av = self._try(AlphaVantage)

    def _try(self, cls):
        try:
            return cls()
        except NotConfigured as e:
            self.errors.append(str(e))
            return None

    def describe(self) -> dict[str, str]:
        return {"sec_edgar": "configured" if self.sec else "not configured",
                "orats": "configured" if self.orats else "not configured",
                "massive": "configured" if self.massive else "not configured",
                "fmp": "configured" if self.fmp else "not configured",
                "alphavantage": "configured" if self.av else "not configured (optional)"}

    def _need(self, p, name):
        if p is None:
            raise NotConfigured(name, f"{name} is not configured")
        return p

    async def universe(self):
        return await self._need(self.sec, "sec_edgar").universe()

    async def options_day(self, day: date):
        o = self._need(self.orats, "orats")
        cores = {r["ticker"]: r for r in await o.hist_cores_for_date(day)}
        summ = {r["ticker"]: r for r in await o.hist_summaries_for_date(day)}
        rows = []
        for t, c in cores.items():
            s = summ.get(t, {})
            rows.append(OptionsDayRow(t, day, c.get("call_volume"), c.get("put_volume"), s.get("iv30"), None,
                                      c.get("price") or s.get("price"), c.get("call_oi"), c.get("put_oi")))
        return rows, o.prov("/hist/cores+/hist/summaries", pd.Timestamp(day).to_pydatetime())

    async def option_contracts(self, ticker: str):
        m = self._need(self.massive, "massive")
        c, spot = await m.option_chain(ticker)
        return c, spot, m.prov("/v3/snapshot/options", None)

    async def bars(self, ticker, start, end):
        """Massive first; FMP end-of-day history as fallback."""
        from app.providers.market import fmp_daily_bars

        if self.massive:
            df = await self.massive.daily_bars(ticker, start, end)
            ts = df.index[-1].to_pydatetime() if len(df) else None
            return df, self.massive.prov("/v2/aggs/ticker/range/1/day", ts)
        f = self._need(self.fmp, "fmp")
        df = (await fmp_daily_bars(f, ticker, start, end)).drop(columns=["adj_close"], errors="ignore")
        ts = df.index[-1].to_pydatetime() if len(df) else None
        return df, f.prov("/historical-price-eod/full", ts)

    async def quote(self, ticker):
        m = self._need(self.massive, "massive")
        q = await m.snapshot(ticker)
        return q, m.prov("/v2/snapshot/.../tickers", q.get("market_ts"))

    async def insider_filings(self, day: date, skip: set[str] | None = None, limit: int | None = None):
        from app.providers.sec_edgar import parse_form4

        sec = self._need(self.sec, "sec_edgar")
        todo = [f for f in await sec.form4_index(day) if f["accession"] not in (skip or set())]
        batch = todo if limit is None else todo[:limit]
        out: list[InsiderTxn] = []
        processed: set[str] = set()
        for f in batch:
            try:
                out.extend(parse_form4(await sec.filing_text(f["file"]), f["accession"], f["filed"]))
                processed.add(f["accession"])
            except ProviderError as e:  # not marked processed, so it is retried next run
                self.errors.append(str(e))
        prov = sec.prov("/Archives/edgar/daily-index form.idx", pd.Timestamp(day).to_pydatetime())
        return out, prov, processed, len(todo) - len(batch)

    async def fundamentals(self, ticker, cik, as_of, market_cap, avg_24, avg_36):
        from app.providers.sec_edgar import fundamentals_from_xbrl, shares_outstanding

        sec = self._need(self.sec, "sec_edgar")
        cf = await sec.companyfacts(cik)
        sub = await sec.submissions(cik)
        so, so_date = shares_outstanding(cf, as_of)
        f = fundamentals_from_xbrl(cf, sub, ticker, as_of, market_cap, avg_24, avg_36)
        f.shares_outstanding_date = so_date
        return f, so, sec.prov("/api/xbrl/companyfacts", None)

    async def analyst(self, ticker):
        if self.fmp:
            try:
                f = self.fmp
                return AnalystData(await f.grades_consensus(ticker), await f.price_target_consensus(ticker),
                                   await f.grades(ticker)), f.prov("/grades-consensus,/price-target-consensus,/grades")
            except ProviderError as e:
                if not self.av:
                    raise
                self.errors.append(str(e))
        av = self._need(self.av, "fmp or alphavantage")
        o = await av.analyst_counts(ticker)
        if not o:
            raise ProviderError("alphavantage", f"no overview for {ticker}")
        t = o["target"]
        return AnalystData(o["counts"], {"avg": t, "high": None, "low": None, "median": None} if t else None), \
            av.prov("OVERVIEW")

    async def float_info(self, ticker):
        f = self._need(self.fmp, "fmp")
        info = await f.shares_float(ticker) or {}
        if self.massive:
            try:
                si = await self.massive.short_interest(ticker)
                if si:
                    info |= {"short_interest": si.get("short_interest"), "days_to_cover": si.get("days_to_cover"),
                             "short_settlement_date": si.get("settlement_date")}
            except ProviderError as e:
                self.errors.append(str(e))
        return info, f.prov("/shares-float (+massive short-interest)")

    async def news(self, ticker):
        from app.providers.market import fmp_news

        if self.massive:
            return await self.massive.news(ticker), self.massive.prov("/v2/reference/news", None)
        f = self._need(self.fmp, "fmp")
        return await fmp_news(f, ticker), f.prov("/news/stock")

    async def company_quote(self, ticker):
        """Price, day change, P/E and market cap (FMP; Massive snapshot for price if FMP is absent)."""
        from app.providers.market import fmp_quote

        if self.fmp:
            q = await fmp_quote(self.fmp, ticker)
            return q, self.fmp.prov("/quote + /ratios-ttm")
        m = self._need(self.massive, "massive")
        q = await m.snapshot(ticker)
        return {"price": q.get("price"), "day_change_pct": q.get("day_change_pct"), "pe": None}, \
            m.prov("/v2/snapshot", q.get("market_ts"))

    async def earnings_calendar(self, start, end):
        """FMP earnings calendar; Alpha Vantage's free EARNINGS_CALENDAR as fallback."""
        from app.providers.market import fmp_earnings_calendar

        if self.fmp:
            try:
                return await fmp_earnings_calendar(self.fmp, start, end), self.fmp.prov("/earnings-calendar")
            except ProviderError as e:
                if not self.av:
                    raise
                self.errors.append(str(e))
        av = self._need(self.av, "alphavantage")
        rows = [r for r in await av.earnings_calendar() if start.isoformat() <= r["report_date"] <= end.isoformat()]
        return rows, Provenance(av.name, Freshness.EOD, datetime.now(timezone.utc), None, endpoint="EARNINGS_CALENDAR")

    async def market_closes(self, day):
        """Split-adjusted closing price of every US stock for one session (one request).
        Massive grouped daily bars; FMP bulk end-of-day as fallback."""
        from app.providers.market import fmp_bulk_closes

        if self.massive:
            rows = await self.massive.grouped_daily(day)
            return {r["ticker"]: float(r["close"]) for r in rows if r.get("close")}, \
                self.massive.prov("/v2/aggs/grouped/locale/us/market/stocks", None)
        f = self._need(self.fmp, "fmp")
        return await fmp_bulk_closes(f, day), f.prov("/eod-bulk")


def lookback_start(as_of: date, days: int) -> date:
    return as_of - timedelta(days=days)
