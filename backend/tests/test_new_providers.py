"""Parsers and provider routing for Alpaca, Finnhub, Tiingo and Alpha Vantage daily history."""
import asyncio
from datetime import date

import pandas as pd
import pytest

from app.providers import extra
from app.providers.http import ProviderError


def test_alpaca_snapshot_parser():
    q = extra.parse_alpaca_snapshot({
        "latestTrade": {"p": 101.5, "t": "2026-11-04T20:59:59.123456789Z"},
        "latestQuote": {"bp": 101.4, "ap": 101.6},
        "dailyBar": {"o": 99.0, "h": 102.0, "l": 98.5, "c": 101.5, "v": 1234567, "t": "2026-11-04T05:00:00Z"},
        "prevDailyBar": {"c": 100.0}})
    assert q["price"] == 101.5 and q["day_change_pct"] == pytest.approx(1.5)
    assert (q["bid"], q["ask"], q["open"], q["high"], q["low"], q["volume"]) == (101.4, 101.6, 99.0, 102.0, 98.5, 1234567)
    assert q["market_ts"].date() == date(2026, 11, 4)


def test_finnhub_parsers():
    q = extra.parse_finnhub_quote({"c": 50.0, "d": 1.0, "dp": 2.04, "h": 51, "l": 49, "o": 49.5, "pc": 49.0, "t": 1793822400})
    assert q["price"] == 50.0 and q["day_change_pct"] == 2.04 and q["open"] == 49.5
    r = extra.parse_finnhub_recommendation([
        {"period": "2026-10-01", "strongBuy": 5, "buy": 10, "hold": 4, "sell": 1, "strongSell": 0},
        {"period": "2026-11-01", "strongBuy": 6, "buy": 11, "hold": 3, "sell": 0, "strongSell": 0}])
    assert r == {"period": "2026-11-01", "strong_buy": 6, "buy": 11, "hold": 3, "sell": 0, "strong_sell": 0}
    assert extra.parse_finnhub_recommendation([]) is None


def test_tiingo_uses_adjusted_prices():
    df = extra.parse_tiingo([
        {"date": "2026-11-03T00:00:00.000Z", "open": 200, "high": 210, "low": 190, "close": 205, "volume": 1000,
         "adjOpen": 100, "adjHigh": 105, "adjLow": 95, "adjClose": 102.5, "adjVolume": 2000},
        {"date": "2026-11-04T00:00:00.000Z", "open": 206, "high": 208, "low": 204, "close": 207, "volume": 900}])
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.loc["2026-11-03"].tolist() == [100, 105, 95, 102.5, 2000]
    assert df.loc["2026-11-04", "close"] == 207  # no adj fields -> raw


def test_alpha_vantage_daily_parser():
    df = extra.parse_av_daily({"Time Series (Daily)": {
        "2026-11-04": {"1. open": "10", "2. high": "11", "3. low": "9.5", "4. close": "10.5", "5. volume": "500"},
        "2026-11-03": {"1. open": "9", "2. high": "10", "3. low": "8.5", "4. close": "9.5", "5. volume": "400"}}})
    assert df.index[0] < df.index[1] and df["close"].tolist() == [9.5, 10.5]
    with pytest.raises(ProviderError, match="rate limit"):
        extra.parse_av_daily({"Information": "rate limit reached"})


class _Fail:
    name = "x"

    async def daily_bars(self, *a):
        raise ProviderError("tiingo", "down", 503)


class _Ok:
    name = "alphavantage"

    async def daily_bars(self, *a):
        return pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
                            index=pd.DatetimeIndex(["2026-11-04"]))


def test_history_falls_back_from_tiingo_to_alpha_vantage():
    from app.services.datasource import LiveDataSource

    ds = LiveDataSource.__new__(LiveDataSource)
    ds.errors = []
    ds.tiingo, ds.av, ds.alpaca, ds.massive, ds.fmp = _Fail(), _Ok(), None, None, None
    df, prov = asyncio.run(ds.bars("AAPL", date(2026, 10, 1), date(2026, 11, 4)))
    assert len(df) == 1 and prov.provider == "alphavantage" and any("down" in e for e in ds.errors)


def test_analyst_prefers_finnhub():
    from app.services.datasource import LiveDataSource

    class F:
        name = "finnhub"

        async def recommendation(self, t):
            return {"period": "2026-11-01", "strong_buy": 6, "buy": 11, "hold": 3, "sell": 0, "strong_sell": 0}

        def prov(self, endpoint, market_ts=None, freshness=None):
            from app.screening.types import Freshness, Provenance
            return Provenance("finnhub", freshness or Freshness.EOD, pd.Timestamp.now(tz="UTC").to_pydatetime(), None, endpoint=endpoint)

    ds = LiveDataSource.__new__(LiveDataSource)
    ds.errors = []
    ds.finnhub, ds.fmp, ds.av = F(), None, None
    ad, prov = asyncio.run(ds.analyst("AAPL"))
    assert ad.counts["strong_buy"] == 6 and "period" not in ad.counts and prov.provider == "finnhub"


def test_live_quote_routing_prefers_alpaca_then_finnhub():
    from app.services.datasource import LiveDataSource

    class A:
        name = "alpaca"

        async def snapshot(self, t):
            raise ProviderError("alpaca", "down", 503)

    class F:
        name = "finnhub"

        async def quote(self, t):
            return extra.parse_finnhub_quote({"c": 50.0, "dp": 1.0, "o": 49.0, "h": 51.0, "l": 48.0, "pc": 49.5, "t": 1793822400})

        def prov(self, endpoint, market_ts=None, freshness=None):
            from app.screening.types import Freshness, Provenance
            return Provenance("finnhub", Freshness.DELAYED, pd.Timestamp.now(tz="UTC").to_pydatetime(), market_ts, endpoint=endpoint)

    ds = LiveDataSource.__new__(LiveDataSource)
    ds.errors = []
    ds.alpaca, ds.finnhub, ds.massive = A(), F(), None
    q, prov = asyncio.run(ds.quote("AAPL"))
    assert q["price"] == 50.0 and prov.provider == "finnhub"


def test_sentisense_parsers():
    r = extra.parse_sentisense_rating({
        "ticker": "AAPL", "rated": True, "score": 85.6, "letter": "B", "percentile": 100.0, "penaltyPoints": 14.4,
        "riskAdjustments": [{"condition": "weak_dimension", "points": 2.4}, {"condition": "high_leverage", "points": 12.0}],
        "ratedCount": 1000, "asOf": "2026-09-03", "methodologyVersion": "2026.09-v5",
        "dimensions": [{"key": "analysts", "label": "Analysts", "percentile": 30.0, "raw": 57.0, "present": True},
                       {"key": "crowd", "label": "Crowd sentiment", "percentile": None, "raw": None, "present": False}],
        "flags": [{"key": "unusual_options_flow", "label": "Unusual options flow", "active": True}],
        "disclaimer": "research signal"})
    assert (r["letter"], r["score"], r["percentile"], r["penalty_points"]) == ("B", 85.6, 100.0, 14.4)
    assert r["analyst_favorable_pct"] == 57.0 and r["risk_adjustments"][1] == {"condition": "high_leverage", "points": 12.0}
    nr = extra.parse_sentisense_rating({"ticker": "SPY", "rated": False, "asOf": "2026-09-03", "reason": "not_rated_today"})
    assert nr["rated"] is False and nr["reason"] == "not_rated_today" and "score" not in nr
    ins = extra.parse_sentisense_insights({"isPreview": True, "data": [
        {"insightType": "insider_buy_signal", "category": "FUNDAMENTAL", "insightText": "Cluster buying.",
         "confidence": 0.8, "urgency": "high", "generatedAt": 1774958400}]})
    assert ins[0]["type"] == "insider_buy_signal" and ins[0]["urgency"] == "high" and ins[0]["generated"] == "2026-03-31"
