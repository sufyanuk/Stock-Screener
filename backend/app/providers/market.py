"""Commercial market-data adapters: ORATS (options analytics), Massive/Polygon (prices, option
chains, short interest, news) and Financial Modeling Prep (analyst data, float).

Field names are centralised in ``*_FIELDS`` maps. They follow each provider's public
documentation but were **not verified against live responses in the build environment**
(outbound access to these hosts was blocked there). Run ``python -m app.cli verify-providers``
with real keys before relying on a provider; it checks every mapped field is present.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from app.config import get_settings
from app.providers.http import HttpProvider, NotConfigured, ProviderError
from app.screening.analyst import RatingAction
from app.screening.options import ContractActivity, DailyOptionsVolume
from app.screening.types import Freshness, Provenance


def _now():
    return datetime.now(timezone.utc)


# ===================================================================== ORATS
ORATS_FIELDS = {
    "cores": {"ticker": "ticker", "date": "tradeDate", "call_volume": "cVolu", "put_volume": "pVolu",
              "call_oi": "cOi", "put_oi": "pOi", "price": "stkPx"},
    "summaries": {"ticker": "ticker", "date": "tradeDate", "iv30": "iv30d", "price": "stockPrice"},
    "ivrank": {"ticker": "ticker", "date": "tradeDate", "iv_rank_1y": "ivRank1y", "iv_pct_1y": "ivPct1y"},
}


class Orats:
    """ORATS Data API (https://orats.com/docs). Delayed plan: 15-minute delayed / EOD history."""

    name = "orats"
    freshness = Freshness.DELAYED

    def __init__(self):
        key = get_settings().orats_api_key
        if not key:
            raise NotConfigured(self.name, "ORATS_API_KEY not set")
        self.key = key
        self.http = HttpProvider(self.name, "https://api.orats.io/datav2", requests_per_second=4, cache_ttl=600)

    def prov(self, endpoint: str, market_ts: datetime | None) -> Provenance:
        return Provenance(self.name, self.freshness, _now(), market_ts, endpoint=endpoint)

    async def _get(self, path: str, **params) -> list[dict]:
        j = await self.http.get_json(path, {"token": self.key, **params})
        return j.get("data", []) if isinstance(j, dict) else []

    async def hist_cores_for_date(self, day: date) -> list[dict]:
        """Options volume for *all* tickers on one trade date (1 request per day)."""
        f = ORATS_FIELDS["cores"]
        rows = await self._get("/hist/cores", tradeDate=day.isoformat(), fields=",".join(f.values()))
        return [{k: r.get(v) for k, v in f.items()} for r in rows]

    async def hist_summaries_for_date(self, day: date) -> list[dict]:
        f = ORATS_FIELDS["summaries"]
        rows = await self._get("/hist/summaries", tradeDate=day.isoformat(), fields=",".join(f.values()))
        return [{k: r.get(v) for k, v in f.items()} for r in rows]

    async def ivrank(self, tickers: list[str]) -> dict[str, dict]:
        f = ORATS_FIELDS["ivrank"]
        out = {}
        for i in range(0, len(tickers), 100):
            for r in await self._get("/ivrank", ticker=",".join(tickers[i:i + 100])):
                out[r.get(f["ticker"])] = {k: r.get(v) for k, v in f.items()}
        return out

    @staticmethod
    def to_daily_volume(rows: list[dict]) -> DailyOptionsVolume | None:
        r = rows[0] if rows else None
        if not r or r.get("call_volume") is None or r.get("put_volume") is None:
            return None
        return DailyOptionsVolume(date.fromisoformat(str(r["date"])[:10]), int(r["call_volume"]), int(r["put_volume"]))


# ===================================================================== Massive / Polygon
class Massive:
    """Massive.com (formerly Polygon.io) REST API. Freshness depends on the subscribed plan."""

    name = "massive"

    def __init__(self, realtime: bool = False):
        s = get_settings()
        if not s.massive_api_key:
            raise NotConfigured(self.name, "MASSIVE_API_KEY not set")
        self.key = s.massive_api_key
        self.freshness = Freshness.REALTIME if realtime else Freshness.DELAYED
        self.http = HttpProvider(self.name, s.massive_base_url, requests_per_second=20, cache_ttl=60)

    def prov(self, endpoint: str, market_ts: datetime | None) -> Provenance:
        return Provenance(self.name, self.freshness, _now(), market_ts, endpoint=endpoint)

    async def _get(self, path: str, **params):
        return await self.http.get_json(path, {"apiKey": self.key, **params})

    async def grouped_daily(self, day: date) -> list[dict]:
        j = await self._get(f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}", adjusted="true")
        return [{"ticker": r["T"], "open": r.get("o"), "high": r.get("h"), "low": r.get("l"), "close": r.get("c"),
                 "volume": r.get("v"), "vwap": r.get("vw"), "ts": r.get("t")} for r in j.get("results", []) or []]

    async def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        j = await self._get(f"/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}", adjusted="true", sort="asc", limit=50000)
        rows = j.get("results", []) or []
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["t"], unit="ms").dt.tz_localize("UTC").dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
        return df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})[
            ["open", "high", "low", "close", "volume"]]

    async def snapshot(self, ticker: str) -> dict:
        j = await self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
        t = j.get("ticker", {}) or {}
        q = t.get("lastQuote", {}) or {}
        day = t.get("day", {}) or {}
        last = (t.get("lastTrade") or {}).get("p") or day.get("c") or (t.get("prevDay") or {}).get("c")
        upd = t.get("updated")
        return {"price": last, "day_change_pct": t.get("todaysChangePerc"), "bid": q.get("p"), "ask": q.get("P"),
                "volume": day.get("v"),
                "market_ts": datetime.fromtimestamp(upd / 1e9, timezone.utc) if upd else None}

    async def option_chain(self, underlying: str) -> tuple[list[ContractActivity], float | None]:
        out: list[ContractActivity] = []
        spot = None
        path, params = f"/v3/snapshot/options/{underlying}", {"limit": 250}
        for _ in range(40):  # pagination guard
            j = await self._get(path, **params)
            for r in j.get("results", []) or []:
                d = r.get("details", {}) or {}
                day = r.get("day", {}) or {}
                q = r.get("last_quote", {}) or {}
                spot = spot or (r.get("underlying_asset") or {}).get("price")
                vol = int(day.get("volume") or 0)
                if not vol:
                    continue
                out.append(ContractActivity(
                    contract=d.get("ticker", ""), option_type=d.get("contract_type", ""),
                    expiration=date.fromisoformat(d["expiration_date"]), strike=float(d.get("strike_price", 0)),
                    volume=vol, open_interest=r.get("open_interest"), price=day.get("vwap") or day.get("close"),
                    bid=q.get("bid"), ask=q.get("ask")))
            nxt = j.get("next_url")
            if not nxt:
                break
            path, params = nxt.replace(self.http.base_url, ""), {}
        return out, spot

    async def ticker_details(self, ticker: str) -> dict:
        r = (await self._get(f"/v3/reference/tickers/{ticker}")).get("results", {}) or {}
        return {"name": r.get("name"), "market_cap": r.get("market_cap"), "description": r.get("description"),
                "shares_outstanding": r.get("share_class_shares_outstanding") or r.get("weighted_shares_outstanding"),
                "sic": r.get("sic_code"), "exchange": r.get("primary_exchange"), "homepage": r.get("homepage_url")}

    async def short_interest(self, ticker: str) -> dict | None:
        rows = (await self._get("/stocks/v1/short-interest", ticker=ticker, sort="settlement_date.desc", limit=1)).get("results") or []
        return rows[0] if rows else None

    async def news(self, ticker: str, limit: int = 10) -> list[dict]:
        rows = (await self._get("/v2/reference/news", ticker=ticker, limit=limit, order="desc")).get("results") or []
        return [{"title": r.get("title"), "url": r.get("article_url"), "published": r.get("published_utc"),
                 "publisher": (r.get("publisher") or {}).get("name")} for r in rows]


# ===================================================================== FMP
class Fmp:
    """Financial Modeling Prep 'stable' API (https://site.financialmodelingprep.com/developer/docs)."""

    name = "fmp"
    freshness = Freshness.EOD

    def __init__(self):
        key = get_settings().fmp_api_key
        if not key:
            raise NotConfigured(self.name, "FMP_API_KEY not set")
        self.key = key
        self.http = HttpProvider(self.name, "https://financialmodelingprep.com/stable", requests_per_second=5, cache_ttl=3600)

    def prov(self, endpoint: str, market_ts: datetime | None = None) -> Provenance:
        return Provenance(self.name, self.freshness, _now(), market_ts, endpoint=endpoint)

    async def _first(self, path: str, symbol: str) -> dict | None:
        j = await self.http.get_json(path, {"symbol": symbol, "apikey": self.key})
        return j[0] if isinstance(j, list) and j else None

    async def grades_consensus(self, symbol: str) -> dict[str, int] | None:
        r = await self._first("/grades-consensus", symbol)
        if not r:
            return None
        return {"strong_buy": r.get("strongBuy", 0), "buy": r.get("buy", 0), "hold": r.get("hold", 0),
                "sell": r.get("sell", 0), "strong_sell": r.get("strongSell", 0)}

    async def price_target_consensus(self, symbol: str) -> dict | None:
        r = await self._first("/price-target-consensus", symbol)
        if not r:
            return None
        return {"avg": r.get("targetConsensus"), "high": r.get("targetHigh"), "low": r.get("targetLow"),
                "median": r.get("targetMedian")}

    async def grades(self, symbol: str) -> list[RatingAction]:
        j = await self.http.get_json("/grades", {"symbol": symbol, "apikey": self.key})
        out = []
        for r in j if isinstance(j, list) else []:
            act = (r.get("action") or "").lower()
            out.append(RatingAction(date=date.fromisoformat(r["date"][:10]), firm=r.get("gradingCompany", ""),
                                    analyst=None, action={"up": "upgrade", "down": "downgrade"}.get(act, act),
                                    new_grade=r.get("newGrade", ""), previous_grade=r.get("previousGrade")))
        return out

    async def shares_float(self, symbol: str) -> dict | None:
        r = await self._first("/shares-float", symbol)
        if not r:
            return None
        return {"float_shares": r.get("floatShares"), "outstanding_shares": r.get("outstandingShares"),
                "free_float_pct": r.get("freeFloat"), "date": r.get("date")}


# ===================================================================== FMP (extra endpoints)
def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


async def fmp_quote(f: "Fmp", symbol: str) -> dict:
    q = await f._first("/quote", symbol) or {}
    ratios = await f._first("/ratios-ttm", symbol) or {}
    pe = _f(ratios.get("priceToEarningsRatioTTM")) or _f(q.get("pe"))
    return {"name": q.get("name"), "price": _f(q.get("price")), "day_change_pct": _f(q.get("changePercentage")),
            "market_cap": _f(q.get("marketCap")), "pe": pe, "year_high": _f(q.get("yearHigh")),
            "year_low": _f(q.get("yearLow")), "volume": _f(q.get("volume")),
            "earnings_announcement": q.get("earningsAnnouncement")}


async def fmp_earnings_calendar(f: "Fmp", start: date, end: date) -> list[dict]:
    j = await f.http.get_json("/earnings-calendar", {"from": start.isoformat(), "to": end.isoformat(), "apikey": f.key})
    out = []
    for r in j if isinstance(j, list) else []:
        if not r.get("symbol") or not r.get("date"):
            continue
        out.append({"symbol": r["symbol"].upper(), "name": r.get("name"), "report_date": r["date"][:10],
                    "fiscal_date_ending": r.get("fiscalDateEnding"), "eps_estimate": _f(r.get("epsEstimated")),
                    "time_of_day": r.get("time") or None})
    return out


async def fmp_news(f: "Fmp", symbol: str, limit: int = 10) -> list[dict]:
    j = await f.http.get_json("/news/stock", {"symbols": symbol, "limit": limit, "apikey": f.key}, cache_ttl=900)
    return [{"title": r.get("title"), "url": r.get("url"), "published": r.get("publishedDate"),
             "publisher": r.get("publisher") or r.get("site")} for r in (j if isinstance(j, list) else [])][:limit]


async def fmp_daily_bars(f: "Fmp", symbol: str, start: date, end: date) -> pd.DataFrame:
    j = await f.http.get_json("/historical-price-eod/full",
                              {"symbol": symbol, "from": start.isoformat(), "to": end.isoformat(), "apikey": f.key})
    rows = j.get("historical", []) if isinstance(j, dict) else (j if isinstance(j, list) else [])
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["date"])
    adj = "adjClose" if "adjClose" in df else "close"
    return df.sort_index()[["open", "high", "low", "close", "volume"]].assign(adj_close=df[adj].astype(float))


# ===================================================================== Alpha Vantage (earnings calendar)
class AlphaVantage:
    """Earnings calendar, daily history (fallback) and analyst counts from OVERVIEW (fallback)."""

    name = "alphavantage"

    def __init__(self):
        key = get_settings().alphavantage_api_key
        if not key:
            raise NotConfigured(self.name, "ALPHAVANTAGE_API_KEY not set")
        self.key = key
        self.http = HttpProvider(self.name, "https://www.alphavantage.co", requests_per_second=1, cache_ttl=6 * 3600)

    async def earnings_calendar(self) -> list[dict]:
        import csv
        import io

        txt = await self.http.get_text(f"/query?function=EARNINGS_CALENDAR&horizon=3month&apikey={self.key}")
        if not txt.lower().startswith("symbol"):
            from app.providers.http import ProviderError

            raise ProviderError(self.name, f"unexpected response: {txt[:120]!r}")
        return parse_av_earnings(txt)

    async def daily_bars(self, ticker: str, start: date, end: date):
        """TIME_SERIES_DAILY: "full" history when the plan allows it, otherwise the last 100 sessions."""
        from app.providers.extra import parse_av_daily

        size = "full" if (end - start).days > 140 else "compact"
        try:
            df = parse_av_daily(await self.http.get_json("/query", {"function": "TIME_SERIES_DAILY", "symbol": ticker,
                                                                   "outputsize": size, "apikey": self.key}))
        except ProviderError:
            if size == "compact":
                raise
            df = parse_av_daily(await self.http.get_json("/query", {"function": "TIME_SERIES_DAILY", "symbol": ticker,
                                                                   "outputsize": "compact", "apikey": self.key}))
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]

    async def analyst_counts(self, ticker: str) -> dict | None:
        """Analyst rating counts and target from COMPANY_OVERVIEW (fallback when FMP is unavailable)."""
        j = await self.http.get_json("/query", {"function": "OVERVIEW", "symbol": ticker, "apikey": self.key})
        return parse_av_overview(j)

    def prov(self, endpoint: str):
        return Provenance(self.name, Freshness.EOD, _now(), None, endpoint=endpoint)


def parse_av_overview(j) -> dict | None:
    if not isinstance(j, dict) or not j.get("Symbol"):
        return None
    keys = {"strong_buy": "AnalystRatingStrongBuy", "buy": "AnalystRatingBuy", "hold": "AnalystRatingHold",
            "sell": "AnalystRatingSell", "strong_sell": "AnalystRatingStrongSell"}
    counts = {k: int(_f(j.get(v))) for k, v in keys.items() if _f(j.get(v)) is not None}
    return {"counts": counts or None, "target": _f(j.get("AnalystTargetPrice"))}


def parse_av_earnings(txt: str) -> list[dict]:
    import csv
    import io

    out = []
    for r in csv.DictReader(io.StringIO(txt)):
        if not r.get("symbol") or not r.get("reportDate"):
            continue
        out.append({"symbol": r["symbol"].upper(), "name": r.get("name"), "report_date": r["reportDate"][:10],
                    "fiscal_date_ending": r.get("fiscalDateEnding") or None, "eps_estimate": _f(r.get("estimate")),
                    "time_of_day": r.get("timeOfTheDay") or None})
    return out


async def massive_monthly_closes(m: "Massive", symbol: str, start: date, end: date) -> list[tuple[date, float]]:
    j = await m._get(f"/v2/aggs/ticker/{symbol}/range/1/month/{start}/{end}", adjusted="true", sort="asc", limit=5000)
    out = []
    for r in j.get("results", []) or []:
        d = datetime.fromtimestamp(r["t"] / 1000, timezone.utc).date()
        out.append((date(d.year, d.month, 1), float(r["c"])))
    return out


# ===================================================================== whole-market daily closes
async def fmp_bulk_closes(f: "Fmp", day: date) -> dict[str, float]:
    """FMP end-of-day bulk file for one date (all symbols). Uses adjClose when present."""
    import csv
    import io

    try:
        j = await f.http.get_json("/eod-bulk", {"date": day.isoformat(), "apikey": f.key})
        rows = j if isinstance(j, list) else []
    except ValueError:  # endpoint can answer with CSV
        txt = await f.http.get_text(f"/eod-bulk?date={day.isoformat()}&apikey={f.key}")
        rows = list(csv.DictReader(io.StringIO(txt)))
    out = {}
    for r in rows:
        sym, px = (r.get("symbol") or "").upper(), _f(r.get("adjClose")) or _f(r.get("close"))
        if sym and px:
            out[sym] = px
    return out
