"""Alpaca (stock prices), Finnhub (analyst ratings, prices fallback) and Tiingo (historical prices).

Each client raises ``NotConfigured`` when its key is missing and ``ProviderError`` on failures,
so the data source can fall through to the next provider.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from app.config import get_settings
from app.providers.http import HttpProvider, NotConfigured, ProviderError
from app.screening.types import Freshness, Provenance

COLS = ["open", "high", "low", "close", "volume"]


def _now():
    return datetime.now(timezone.utc)


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # drop NaN


def _frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(rows).set_index("date").sort_index()
    df.index = pd.DatetimeIndex(df.index)
    df = df[~df.index.duplicated(keep="last")]
    return df[COLS].astype(float)


class Alpaca:
    """Alpaca Market Data API v2 (https://docs.alpaca.markets/docs/about-market-data-api)."""

    name = "alpaca"

    def __init__(self):
        s = get_settings()
        if not s.alpaca_api_key_id or not s.alpaca_api_secret_key:
            raise NotConfigured(self.name, "ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
        self.feed = s.alpaca_data_feed or "iex"
        self.http = HttpProvider(self.name, "https://data.alpaca.markets", requests_per_second=3, cache_ttl=30,
                                 headers={"APCA-API-KEY-ID": s.alpaca_api_key_id,
                                          "APCA-API-SECRET-KEY": s.alpaca_api_secret_key})

    def prov(self, endpoint: str, market_ts: datetime | None = None) -> Provenance:
        fresh = Freshness.REALTIME if self.feed == "sip" else Freshness.DELAYED
        return Provenance(self.name, fresh, _now(), market_ts, endpoint=f"{endpoint} (feed={self.feed})")

    async def snapshot(self, ticker: str) -> dict:
        j = await self.http.get_json(f"/v2/stocks/{ticker}/snapshot", {"feed": self.feed})
        return parse_alpaca_snapshot(j)

    async def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        rows, token = [], None
        for _ in range(20):
            params = {"timeframe": "1Day", "start": start.isoformat(), "end": end.isoformat(), "adjustment": "all",
                      "feed": self.feed, "limit": 10000}
            if token:
                params["page_token"] = token
            j = await self.http.get_json(f"/v2/stocks/{ticker}/bars", params, cache_ttl=600)
            rows += [{"date": str(b["t"])[:10], "open": b.get("o"), "high": b.get("h"), "low": b.get("l"),
                      "close": b.get("c"), "volume": b.get("v")} for b in (j.get("bars") or [])]
            token = j.get("next_page_token")
            if not token:
                break
        return _frame(rows)


def parse_alpaca_snapshot(j: dict) -> dict:
    day, prev = j.get("dailyBar") or {}, j.get("prevDailyBar") or {}
    trade, q = j.get("latestTrade") or {}, j.get("latestQuote") or {}
    price = _f(trade.get("p")) or _f(day.get("c"))
    pc = _f(prev.get("c"))
    ts = trade.get("t") or day.get("t")
    try:
        market_ts = pd.Timestamp(ts).floor("us").to_pydatetime() if ts else None  # nanosecond ISO strings
    except (ValueError, TypeError):
        market_ts = None
    return {"price": price, "day_change_pct": (price / pc - 1) * 100 if price and pc else None,
            "bid": _f(q.get("bp")), "ask": _f(q.get("ap")), "volume": _f(day.get("v")),
            "open": _f(day.get("o")), "high": _f(day.get("h")), "low": _f(day.get("l")), "prev_close": pc,
            "market_ts": market_ts}


class Finnhub:
    """Finnhub.io REST API (https://finnhub.io/docs/api)."""

    name = "finnhub"

    def __init__(self):
        key = get_settings().finnhub_api_key
        if not key:
            raise NotConfigured(self.name, "FINNHUB_API_KEY not set")
        self.key = key
        self.http = HttpProvider(self.name, "https://finnhub.io/api/v1", requests_per_second=1, cache_ttl=60)

    def prov(self, endpoint: str, market_ts: datetime | None = None, freshness=Freshness.DELAYED) -> Provenance:
        return Provenance(self.name, freshness, _now(), market_ts, endpoint=endpoint)

    async def _get(self, path: str, cache_ttl: int | None = None, **params):
        return await self.http.get_json(path, {**params, "token": self.key}, cache_ttl=cache_ttl)

    async def quote(self, ticker: str) -> dict:
        j = await self._get("/quote", symbol=ticker)
        if not isinstance(j, dict) or not _f(j.get("c")):
            raise ProviderError(self.name, f"no quote for {ticker}")
        return parse_finnhub_quote(j)

    async def recommendation(self, ticker: str) -> dict | None:
        j = await self._get("/stock/recommendation", cache_ttl=3600, symbol=ticker)
        return parse_finnhub_recommendation(j)

    async def profile(self, ticker: str) -> dict:
        j = await self._get("/stock/profile2", cache_ttl=86400, symbol=ticker)
        return j if isinstance(j, dict) else {}

    async def earnings_calendar(self, start: date, end: date) -> list[dict]:
        j = await self._get("/calendar/earnings", cache_ttl=6 * 3600, **{"from": start.isoformat(), "to": end.isoformat()})
        return parse_finnhub_earnings(j)

    async def metrics(self, ticker: str) -> dict:
        j = await self._get("/stock/metric", cache_ttl=3600, symbol=ticker, metric="all")
        return (j.get("metric") or {}) if isinstance(j, dict) else {}


def parse_finnhub_quote(j: dict) -> dict:
    t = j.get("t")
    return {"price": _f(j.get("c")), "day_change_pct": _f(j.get("dp")), "open": _f(j.get("o")), "high": _f(j.get("h")),
            "low": _f(j.get("l")), "prev_close": _f(j.get("pc")), "bid": None, "ask": None, "volume": None,
            "market_ts": datetime.fromtimestamp(t, timezone.utc) if t else None}


def parse_finnhub_earnings(j) -> list[dict]:
    hours = {"bmo": "pre-market", "amc": "post-market", "dmh": "during market"}
    out = []
    for r in (j or {}).get("earningsCalendar") or [] if isinstance(j, dict) else []:
        if not r.get("symbol") or not r.get("date"):
            continue
        out.append({"symbol": str(r["symbol"]).upper(), "name": None, "report_date": str(r["date"])[:10],
                    "fiscal_date_ending": None, "eps_estimate": _f(r.get("epsEstimate")),
                    "time_of_day": hours.get(r.get("hour") or "", None)})
    return out


def parse_finnhub_recommendation(j) -> dict | None:
    """Latest monthly snapshot of analyst recommendation counts."""
    rows = [r for r in (j if isinstance(j, list) else []) if r.get("period")]
    if not rows:
        return None
    r = max(rows, key=lambda x: x["period"])
    return {"period": r["period"], "strong_buy": int(r.get("strongBuy") or 0), "buy": int(r.get("buy") or 0),
            "hold": int(r.get("hold") or 0), "sell": int(r.get("sell") or 0), "strong_sell": int(r.get("strongSell") or 0)}


class Tiingo:
    """Tiingo end-of-day prices (https://www.tiingo.com/documentation/end-of-day)."""

    name = "tiingo"

    def __init__(self):
        key = get_settings().tiingo_api_key
        if not key:
            raise NotConfigured(self.name, "TIINGO_API_KEY not set")
        self.key = key
        self.http = HttpProvider(self.name, "https://api.tiingo.com", requests_per_second=2, cache_ttl=1800)

    def prov(self, endpoint: str, market_ts: datetime | None = None) -> Provenance:
        return Provenance(self.name, Freshness.EOD, _now(), market_ts, endpoint=endpoint)

    async def daily_bars(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        j = await self.http.get_json(f"/tiingo/daily/{ticker.replace('.', '-')}/prices",
                                     {"startDate": start.isoformat(), "endDate": end.isoformat(), "token": self.key})
        return parse_tiingo(j)


def parse_tiingo(j) -> pd.DataFrame:
    """Split/dividend-adjusted OHLCV (adj* fields)."""
    rows = []
    for r in j if isinstance(j, list) else []:
        if not r.get("date"):
            continue
        g = lambda k: _f(r.get("adj" + k[0].upper() + k[1:])) if r.get("adj" + k[0].upper() + k[1:]) is not None else _f(r.get(k))  # noqa: E731
        rows.append({"date": str(r["date"])[:10], "open": g("open"), "high": g("high"), "low": g("low"),
                     "close": g("close"), "volume": g("volume")})
    return _frame(rows)


def parse_av_daily(j) -> pd.DataFrame:
    """Alpha Vantage TIME_SERIES_DAILY (unadjusted OHLCV)."""
    ts = (j or {}).get("Time Series (Daily)") if isinstance(j, dict) else None
    if not ts:
        msg = (j or {}).get("Note") or (j or {}).get("Information") or (j or {}).get("Error Message") if isinstance(j, dict) else None
        raise ProviderError("alphavantage", f"no daily series ({str(msg)[:120] if msg else 'empty response'})")
    rows = [{"date": d, "open": _f(v.get("1. open")), "high": _f(v.get("2. high")), "low": _f(v.get("3. low")),
             "close": _f(v.get("4. close")), "volume": _f(v.get("5. volume"))} for d, v in ts.items()]
    return _frame(rows)


class SentiSense:
    """SentiSense Rating API (/api/v1/rating/{ticker}) and Insights API (/api/v1/insights/stock/{ticker}).
    Free tier: 1,000 requests/month, 30/minute; the rating is recomputed once per trading day."""

    name = "sentisense"

    def __init__(self):
        key = get_settings().sentisense_api_key
        if not key:
            raise NotConfigured(self.name, "SENTISENSE_API_KEY not set")
        self.http = HttpProvider(self.name, "https://app.sentisense.ai", requests_per_second=2, cache_ttl=6 * 3600,
                                 timeout=15, max_retries=1,
                                 headers={"X-SentiSense-API-Key": key})

    def prov(self, endpoint: str) -> Provenance:
        return Provenance(self.name, Freshness.EOD, _now(), None, endpoint=endpoint)

    async def rating(self, ticker: str) -> dict | None:
        try:
            j = await self.http.get_json(f"/api/v1/rating/{ticker}")
        except ProviderError as e:
            if e.status == 404:  # entity_not_found
                return None
            raise
        return parse_sentisense_rating(j)

    async def insights(self, ticker: str, limit: int = 3) -> list[dict]:
        try:
            j = await self.http.get_json(f"/api/v1/insights/stock/{ticker}", cache_ttl=3600)
        except ProviderError as e:
            if e.status == 404:
                return []
            raise
        return parse_sentisense_insights(j)[:limit]


def parse_sentisense_rating(j) -> dict | None:
    """Rated: score (0-100), letter A-F, percentile, risk adjustments, analysts dimension.
    Not rated: {"rated": false, "reason": ...}. The letter is a score band, not a buy/sell call."""
    if not isinstance(j, dict) or "rated" not in j:
        return None
    out = {"ticker": j.get("ticker"), "rated": bool(j["rated"]), "as_of": j.get("asOf"), "reason": j.get("reason"),
           "disclaimer": j.get("disclaimer")}
    if not j["rated"]:
        return out
    dims = {d.get("key"): d for d in j.get("dimensions") or [] if isinstance(d, dict)}
    an = dims.get("analysts") or {}
    out.update({
        "score": _f(j.get("score")), "letter": j.get("letter"), "percentile": _f(j.get("percentile")),
        "penalty_points": _f(j.get("penaltyPoints")), "rated_count": j.get("ratedCount"),
        "methodology": j.get("methodologyVersion"),
        "risk_adjustments": [{"condition": str(r.get("condition")), "points": _f(r.get("points"))}
                             for r in j.get("riskAdjustments") or [] if isinstance(r, dict)],
        "analyst_favorable_pct": _f(an.get("raw")) if an.get("present") else None,
        "dimensions": [{"key": d.get("key"), "label": d.get("label"), "percentile": _f(d.get("percentile")),
                        "present": bool(d.get("present"))} for d in j.get("dimensions") or [] if isinstance(d, dict)],
        "flags": [{"key": f.get("key"), "label": f.get("label"), "active": bool(f.get("active"))}
                  for f in j.get("flags") or [] if isinstance(f, dict)],
    })
    return out


def parse_sentisense_insights(j) -> list[dict]:
    rows = (j.get("data") if isinstance(j, dict) else j) or []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not r.get("insightText"):
            continue
        ts = r.get("generatedAt")
        out.append({"type": r.get("insightType"), "category": r.get("category"), "text": str(r["insightText"])[:600],
                    "confidence": _f(r.get("confidence")), "urgency": r.get("urgency"),
                    "generated": datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if isinstance(ts, (int, float)) else None})
    return out
