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
    """Provider routing (first choice → fallbacks):
      stock prices        Alpaca → Finnhub → FMP → Massive
      historical prices   Tiingo → Alpha Vantage → Alpaca → Massive → FMP
      analyst ratings     Finnhub (counts; FMP price targets added when available) → FMP → Alpha Vantage
      fundamentals        FMP (P/E, market cap, float) → Finnhub metrics; SEC XBRL for Shariah ratios
      insider buying      SEC EDGAR Form 4
      options             CSV import (or ORATS)"""

    def __init__(self):
        from app.providers.market import Fmp, Massive, Orats
        from app.providers.sec_edgar import SecEdgar

        self.errors: list[str] = []
        self.sec = self._try(SecEdgar)
        self.orats = self._try(Orats)
        self.massive = self._try(Massive)
        self.fmp = self._try(Fmp)
        from app.providers.extra import Alpaca, Finnhub, SentiSense, Tiingo
        from app.providers.market import AlphaVantage

        self.av = self._try(AlphaVantage)
        self.alpaca = self._try(Alpaca)
        self.finnhub = self._try(Finnhub)
        self.tiingo = self._try(Tiingo)
        self.sentisense = self._try(SentiSense)
        from app.config import get_settings
        from app.providers.openinsider import OpenInsider

        self.openinsider = OpenInsider() if get_settings().insider_source == "openinsider" else None

    OPTIONAL = {"Orats", "SentiSense", "AlphaVantage"}  # a missing key here is not a data issue

    def _try(self, cls):
        try:
            return cls()
        except NotConfigured as e:
            if cls.__name__ not in self.OPTIONAL:
                self.errors.append(str(e))
            return None

    def describe(self) -> dict[str, str]:
        return {"sec_edgar": "configured" if self.sec else "not configured",
                "orats": "configured" if self.orats else "not configured",
                "massive": "configured" if self.massive else "not configured",
                "fmp": "configured" if self.fmp else "not configured",
                "alphavantage": "configured" if self.av else "not configured (optional)",
                "alpaca": "configured" if self.alpaca else "not configured",
                "finnhub": "configured" if self.finnhub else "not configured",
                "tiingo": "configured" if self.tiingo else "not configured",
                "sentisense": "configured" if self.sentisense else "not configured (optional)",
                "openinsider": "configured" if self.openinsider else "not used (INSIDER_SOURCE=sec)"}

    async def insider_recent(self, filed_days: int):
        """Open-market purchases filed in the last ``filed_days`` days from the OpenInsider screener."""
        p = self._need(self.openinsider, "openinsider")
        return await p.purchases(filed_days), p.prov()

    async def sentisense_info(self, ticker):
        """SentiSense Rating + top insights (stock panel only, to stay within the free quota)."""
        import asyncio as _a

        p = self._need(self.sentisense, "sentisense")
        rating, ins = await _a.gather(p.rating(ticker), p.insights(ticker), return_exceptions=True)
        if isinstance(rating, Exception) and isinstance(ins, Exception):
            raise rating
        if isinstance(ins, Exception):
            self.errors.append(str(ins))
            ins = []
        return {"rating": None if isinstance(rating, Exception) else rating, "insights": ins}, \
            p.prov("/api/v1/rating, /api/v1/insights/stock")

    async def _first(self, what: str, attempts):
        """Run (provider, coroutine-factory) pairs in order; return the first success."""
        errs = []
        for prov, make in attempts:
            if prov is None:
                continue
            try:
                return await make(prov)
            except ProviderError as e:
                errs.append(str(e))
                self.errors.append(str(e))
        if errs:
            raise ProviderError("fallbacks", f"{what}: " + " | ".join(errs[-3:]))
        raise NotConfigured("prices", f"no provider configured for {what}")

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
        """Daily OHLCV history: Tiingo → Alpha Vantage → Alpaca → Massive → FMP."""
        from app.providers.market import fmp_daily_bars

        def ts(df):
            return df.index[-1].to_pydatetime() if len(df) else None

        async def tiingo(p):
            df = await p.daily_bars(ticker, start, end)
            return df, p.prov("/tiingo/daily/prices", ts(df))

        async def av(p):
            df = await p.daily_bars(ticker, start, end)
            return df, Provenance(p.name, Freshness.EOD, datetime.now(timezone.utc), ts(df), endpoint="TIME_SERIES_DAILY")

        async def alpaca(p):
            df = await p.daily_bars(ticker, start, end)
            return df, p.prov("/v2/stocks/bars", ts(df))

        async def massive(p):
            df = await p.daily_bars(ticker, start, end)
            return df, p.prov("/v2/aggs/ticker/range/1/day", ts(df))

        async def fmp(p):
            df = (await fmp_daily_bars(p, ticker, start, end)).drop(columns=["adj_close"], errors="ignore")
            return df, p.prov("/historical-price-eod/full", ts(df))

        return await self._first("historical prices", [(self.tiingo, tiingo), (self.av, av), (self.alpaca, alpaca),
                                                       (self.massive, massive), (self.fmp, fmp)])

    async def quote(self, ticker):
        """Latest price / bid / ask: Alpaca → Finnhub → Massive."""
        async def alpaca(p):
            q = await p.snapshot(ticker)
            return q, p.prov("/v2/stocks/snapshot", q.get("market_ts"))

        async def finnhub(p):
            q = await p.quote(ticker)
            return q, p.prov("/quote", q.get("market_ts"))

        async def massive(p):
            q = await p.snapshot(ticker)
            return q, p.prov("/v2/snapshot/.../tickers", q.get("market_ts"))

        return await self._first("stock price", [(self.alpaca, alpaca), (self.finnhub, finnhub), (self.massive, massive)])

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
        """Analyst ratings: Finnhub recommendation counts (+ FMP price targets if configured) → FMP → Alpha Vantage."""
        async def finnhub(p):
            counts = await p.recommendation(ticker)
            if not counts:
                raise ProviderError("finnhub", f"no recommendations for {ticker}")
            targets, actions = None, []
            if self.fmp:
                try:
                    targets = await self.fmp.price_target_consensus(ticker)
                    actions = await self.fmp.grades(ticker)
                except ProviderError as e:
                    self.errors.append(str(e))
            period = counts.pop("period")
            return AnalystData(counts, targets, actions), p.prov(f"/stock/recommendation ({period})", freshness=Freshness.EOD)

        async def fmp(f):
            return AnalystData(await f.grades_consensus(ticker), await f.price_target_consensus(ticker),
                               await f.grades(ticker)), f.prov("/grades-consensus,/price-target-consensus,/grades")

        async def av(p):
            o = await p.analyst_counts(ticker)
            if not o:
                raise ProviderError("alphavantage", f"no overview for {ticker}")
            t = o["target"]
            return AnalystData(o["counts"], {"avg": t, "high": None, "low": None, "median": None} if t else None), \
                p.prov("OVERVIEW")

        return await self._first("analyst ratings", [(self.finnhub, finnhub), (self.fmp, fmp), (self.av, av)])

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
        """Price (Alpaca → Finnhub → FMP → Massive) merged with fundamentals (FMP → Finnhub)."""
        from app.providers.market import fmp_quote

        out: dict = {}
        sources: list[str] = []
        if self.fmp:  # fundamentals: name, P/E, market cap, 52-week range, next earnings
            try:
                out = await fmp_quote(self.fmp, ticker)
                sources.append("fmp")
            except ProviderError as e:
                self.errors.append(str(e))
        if self.finnhub and (out.get("pe") is None or out.get("market_cap") is None or not out.get("name")):
            try:
                m, pr = await self.finnhub.metrics(ticker), await self.finnhub.profile(ticker)
                cap = pr.get("marketCapitalization")
                out.setdefault("name", None)
                out["name"] = out.get("name") or pr.get("name")
                out["pe"] = out.get("pe") if out.get("pe") is not None else (m.get("peTTM") or m.get("peBasicExclExtraTTM"))
                out["market_cap"] = out.get("market_cap") or (cap * 1e6 if cap else None)
                out["year_high"] = out.get("year_high") or m.get("52WeekHigh")
                out["year_low"] = out.get("year_low") or m.get("52WeekLow")
                sources.append("finnhub")
            except ProviderError as e:
                self.errors.append(str(e))
        price_src = None
        for p, fn in ((self.alpaca, "snapshot"), (self.finnhub, "quote")):
            if p is None:
                continue
            try:
                q = await getattr(p, fn)(ticker)
                if q.get("price"):
                    out.update({"price": q["price"], "day_change_pct": q.get("day_change_pct")})
                    price_src = p.name
                    break
            except ProviderError as e:
                self.errors.append(str(e))
        if price_src is None and not out.get("price") and self.massive:
            q = await self.massive.snapshot(ticker)
            out.update({"price": q.get("price"), "day_change_pct": q.get("day_change_pct")})
            price_src = "massive"
        elif price_src is None and out.get("price"):
            price_src = "fmp"
        if not out:
            raise NotConfigured("prices", "no price or fundamentals provider configured")
        label = f"price: {price_src or '—'} · fundamentals: {', '.join(sources) or '—'}"
        return out, Provenance(price_src or (sources[0] if sources else "none"), Freshness.DELAYED,
                               datetime.now(timezone.utc), None, endpoint=label)

    async def earnings_calendar(self, start, end):
        """Union of FMP, Finnhub and Alpha Vantage earnings calendars (each plan may cover only part of
        the market), de-duplicated by symbol and date; the first source with a value wins per field."""
        import asyncio as _a

        from app.providers.market import fmp_earnings_calendar

        async def fmp():
            return await fmp_earnings_calendar(self.fmp, start, end)

        async def finnhub():
            return await self.finnhub.earnings_calendar(start, end)

        async def av():
            return [r for r in await self.av.earnings_calendar() if start.isoformat() <= r["report_date"] <= end.isoformat()]

        jobs = [(n, f) for n, p, f in (("fmp", self.fmp, fmp), ("finnhub", self.finnhub, finnhub), ("alphavantage", self.av, av)) if p]
        if not jobs:
            raise NotConfigured("earnings", "no earnings-calendar provider configured")
        results = await _a.gather(*(f() for _, f in jobs), return_exceptions=True)
        merged: dict[tuple, dict] = {}
        used = []
        for (name, _), res in zip(jobs, results):
            if isinstance(res, Exception):
                self.errors.append(str(res))
                continue
            used.append(name)
            for r in res:
                k = (r["symbol"], r["report_date"])
                if k not in merged:
                    merged[k] = dict(r)
                else:
                    for f, v in r.items():
                        if merged[k].get(f) in (None, "") and v not in (None, ""):
                            merged[k][f] = v
        if not used:
            raise ProviderError("earnings", "; ".join(str(r) for r in results if isinstance(r, Exception))[:300])
        return list(merged.values()), Provenance("+".join(used), Freshness.EOD, datetime.now(timezone.utc), None,
                                                 endpoint="earnings calendars")

    async def shares_outstanding(self, ticker):
        """Shares outstanding: Massive ticker details → FMP shares-float → Finnhub profile → Alpha Vantage OVERVIEW."""
        async def massive(p):
            n = await p.shares_outstanding(ticker)
            if not n:
                raise ProviderError("massive", f"no shares outstanding for {ticker}")
            return n, "massive"

        async def fmp(f):
            r = await f.shares_float(ticker) or {}
            n = r.get("outstanding_shares")
            if not n:
                raise ProviderError("fmp", f"no shares outstanding for {ticker}")
            return float(n), "fmp"

        async def finnhub(p):
            n = (await p.profile(ticker)).get("shareOutstanding")  # millions
            if not n:
                raise ProviderError("finnhub", f"no shares outstanding for {ticker}")
            return float(n) * 1e6, "finnhub"

        async def av(p):
            j = await p.http.get_json("/query", {"function": "OVERVIEW", "symbol": ticker, "apikey": p.key})
            n = j.get("SharesOutstanding") if isinstance(j, dict) else None
            try:
                return float(n), "alphavantage"
            except (TypeError, ValueError):
                raise ProviderError("alphavantage", f"no shares outstanding for {ticker}") from None

        return await self._first("shares outstanding", [(self.massive, massive), (self.fmp, fmp), (self.finnhub, finnhub),
                                                        (self.av, av)])

    async def market_bars(self, day):
        """Daily OHLCV of every US stock for one session (one request): Massive grouped daily → FMP bulk."""
        from app.providers.market import fmp_bulk_bars

        async def massive(p):
            rows = await p.grouped_daily(day)
            return {r["ticker"]: (r["open"], r["high"], r["low"], r["close"], r["volume"] or 0.0)
                    for r in rows if r.get("close")}, p.prov("/v2/aggs/grouped/locale/us/market/stocks", None)

        async def fmp(f):
            return await fmp_bulk_bars(f, day), f.prov("/eod-bulk")

        return await self._first("whole-market daily bars", [(self.massive, massive), (self.fmp, fmp)])

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
