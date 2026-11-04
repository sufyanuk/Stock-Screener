"""Data-source abstraction used by the scan engine and the backtester.

``LiveDataSource`` composes the provider adapters with fallbacks. Every call returns values plus
a ``Provenance``; failures raise ``ProviderError`` which the engine records — the affected
criterion becomes MISSING, never PASS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

import pandas as pd

from app.providers.http import NotConfigured, ProviderError
from app.screening.analyst import RatingAction
from app.screening.insider import InsiderTxn
from app.screening.options import ContractActivity
from app.screening.shariah import Fundamentals
from app.screening.types import Provenance


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
    async def insider_filings(self, day: date) -> tuple[list[InsiderTxn], Provenance]: ...
    async def fundamentals(self, ticker: str, cik: str, as_of: date, market_cap: float | None,
                           avg_24: float | None, avg_36: float | None) -> tuple[Fundamentals, float | None, Provenance]: ...
    async def analyst(self, ticker: str) -> tuple[AnalystData, Provenance]: ...
    async def float_info(self, ticker: str) -> tuple[dict, Provenance]: ...
    async def news(self, ticker: str) -> tuple[list[dict], Provenance]: ...


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
                "fmp": "configured" if self.fmp else "not configured"}

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
        m = self._need(self.massive, "massive")
        df = await m.daily_bars(ticker, start, end)
        ts = df.index[-1].to_pydatetime() if len(df) else None
        return df, m.prov("/v2/aggs/ticker/range/1/day", ts)

    async def quote(self, ticker):
        m = self._need(self.massive, "massive")
        q = await m.snapshot(ticker)
        return q, m.prov("/v2/snapshot/.../tickers", q.get("market_ts"))

    async def insider_filings(self, day: date):
        from app.providers.sec_edgar import parse_form4

        sec = self._need(self.sec, "sec_edgar")
        out: list[InsiderTxn] = []
        for f in await sec.form4_index(day):
            try:
                out.extend(parse_form4(await sec.filing_text(f["file"]), f["accession"], f["filed"]))
            except ProviderError as e:
                self.errors.append(str(e))
        return out, sec.prov("/Archives/edgar/daily-index form.idx", pd.Timestamp(day).to_pydatetime())

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
        f = self._need(self.fmp, "fmp")
        return AnalystData(await f.grades_consensus(ticker), await f.price_target_consensus(ticker),
                           await f.grades(ticker)), f.prov("/grades-consensus,/price-target-consensus,/grades")

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
        m = self._need(self.massive, "massive")
        return await m.news(ticker), m.prov("/v2/reference/news", None)


def lookback_start(as_of: date, days: int) -> date:
    return as_of - timedelta(days=days)
