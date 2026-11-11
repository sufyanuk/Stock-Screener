"""Dashboard market pulse: breadth, Market Mood, Macro backdrop, next Fed decision."""
import asyncio
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from app.services import market_pulse as mp


def test_fomc_calendar_from_the_fed_page():
    dates = mp.parse_fomc((Path(__file__).parent / "data" / "fomc_calendar.html").read_text())
    assert date(2026, 12, 9) in dates and date(2027, 6, 9) in dates and date(2025, 12, 10) in dates
    assert date(2025, 8, 22) not in dates  # notation vote, not a meeting
    assert mp.next_fed(dates, date(2026, 11, 11)) == date(2026, 12, 9)
    assert mp.next_fed(dates, date(2026, 12, 9)) == date(2026, 12, 9)  # decision day itself
    assert mp.next_fed(mp.FOMC_FALLBACK, date(2026, 12, 10)) == date(2027, 1, 27)


def test_breadth_counts():
    assert mp.breadth({"A": 1.2, "B": -0.4, "C": 0.0, "D": None, "E": 0.0001}) == \
        {"advancing": 1, "declining": 1, "unchanged": 2, "total": 4}


def test_mood_extremes():
    up = {f"S{i}": pd.Series(np.linspace(50, 100, 260)) for i in range(20)}
    down = {f"S{i}": pd.Series(np.linspace(100, 50, 260)) for i in range(20)}
    greed = mp.mood({"advancing": 20, "declining": 0}, up, pd.Series(np.linspace(300, 400, 260)))
    fear = mp.mood({"advancing": 0, "declining": 20}, down, pd.Series(np.linspace(400, 300, 260)))
    assert greed["score"] >= 90 and greed["label"] == "Extreme Greed" and len(greed["components"]) == 5
    assert fear["score"] <= 10 and fear["label"] == "Extreme Fear"
    assert mp.mood({"advancing": 0, "declining": 0}, {}, None)["score"] is None


def _series(start_val, end_val, today, days=200):
    idx = pd.date_range(today - timedelta(days=days), today, freq="D")
    return pd.Series(np.linspace(start_val, end_val, len(idx)), index=idx)


def test_macro_hostile_and_supportive():
    t = date(2026, 11, 10)
    hostile = mp.macro({"DGS10": _series(3.5, 4.5, t), "T10Y2Y": _series(0.2, -0.3, t),
                        "BAMLH0A0HYM2": _series(3.0, 4.5, t), "DTWEXBGS": _series(100, 108, t),
                        "DFF": _series(4.0, 4.75, t)}, t)
    assert hostile["score"] == -5 and hostile["label"] == "Hostile"
    assert hostile["components"][1]["detail"].endswith("(inverted)")
    supportive = mp.macro({"DGS10": _series(4.5, 3.5, t), "T10Y2Y": _series(0.5, 0.8, t),
                           "BAMLH0A0HYM2": _series(4.5, 3.0, t), "DTWEXBGS": _series(108, 100, t),
                           "DFF": _series(5.0, 4.0, t)}, t)
    assert supportive["score"] == 5 and supportive["label"] == "Supportive"
    assert mp.macro({}, t)["score"] is None


def test_compute_stores_each_part_and_skips_what_is_missing(db, monkeypatch):
    from app.models import IndexMember

    for s in ("AAA", "BBB", "CCC"):
        db.add(IndexMember(index_code="sp500", symbol=s, name=s, source="test"))
    db.commit()

    class Alp:
        async def multi_snapshots(self, symbols):
            return {"AAA": {"day_change_pct": 1.0}, "BBB": {"day_change_pct": -2.0}, "CCC": {"day_change_pct": 0.5}}

        async def multi_daily_bars(self, symbols, start, end, timeframe="1Day"):
            return {s: [("d", 1, 1, 1, float(v), 1) for v in np.linspace(10, 20, 260)] for s in symbols}

    class DS:
        alpaca = Alp()

    class Resp:
        status_code, text = 200, (Path(__file__).parent / "data" / "fomc_calendar.html").read_text()

        def raise_for_status(self):
            pass

    async def fake_get(self, url, **k):
        return Resp()

    async def members(db, ds, code):
        return {"AAA", "BBB", "CCC"}

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    monkeypatch.setattr("app.services.indexes.members", members)
    out = asyncio.run(mp.compute(db, DS(), date(2026, 11, 11)))
    assert out["breadth"]["advancing"] == 2 and out["breadth"]["declining"] == 1
    assert out["mood"]["score"] is not None and out["fed"]["next_decision"] == "2026-12-09"
    assert out["macro"]["score"] is None and "FRED_API_KEY" in out["macro"]["missing"]  # no key: not invented
    assert mp.stored(db)["breadth"]["total"] == 3


MOOD_JSON = {"market": {"currentScore": 28.6, "phase": "Fear", "weeklyChange": -4.1,
                        "signals": [{"key": "spy_trend", "label": "S&P 500 Trend", "value": 31.0, "change": -2.0}],
                        "history": [{"date": "2026-11-09", "score": 30}, {"date": "2026-11-10", "score": 28.6}]},
             "sectors": {}}
HEATMAP_JSON = {"isPreview": True, "previewReason": "PRO_REQUIRED",
                "data": {"asOf": 1794355200000, "meta": {"breadthUp": 245, "breadthDown": 249, "tileCount": 499},
                         "rows": [{"ticker": f"T{i}", "changePercent": c} for i, c in
                                  enumerate([1.0] * 245 + [-1.0] * 249 + [0.0] * 5)]}}


def test_sentisense_parsers():
    from app.providers.extra import parse_sentisense_breadth, parse_sentisense_mood

    m = parse_sentisense_mood(MOOD_JSON)
    assert m["score"] == 28.6 and m["label"] == "Fear" and m["as_of"] == "2026-11-10"
    assert m["components"][0]["name"] == "S&P 500 Trend"
    b = parse_sentisense_breadth(HEATMAP_JSON)
    assert (b["advancing"], b["declining"], b["unchanged"], b["total"]) == (245, 249, 5, 499)
    assert parse_sentisense_mood({"market": {"currentScore": None}}) is None
    assert parse_sentisense_breadth({"error": "x"}) is None


def test_compute_prefers_sentisense_and_skips_alpaca(db, monkeypatch):
    from app.providers.extra import parse_sentisense_breadth, parse_sentisense_mood

    class SS:
        async def market_mood(self):
            return parse_sentisense_mood(MOOD_JSON)

        async def market_breadth(self, scope="sp500"):
            return parse_sentisense_breadth(HEATMAP_JSON)

    class Alp:  # must not be needed
        async def multi_snapshots(self, symbols):
            raise AssertionError("Alpaca used although SentiSense answered")

    class DS:
        sentisense, alpaca = SS(), Alp()

    class Resp:
        status_code, text = 200, (Path(__file__).parent / "data" / "fomc_calendar.html").read_text()

        def raise_for_status(self):
            pass

    async def fake_get(self, url, **k):
        return Resp()

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    out = asyncio.run(mp.compute(db, DS(), date(2026, 11, 11)))
    assert out["mood"]["label"] == "Fear" and out["mood"]["source"] == "SentiSense"
    assert out["breadth"]["advancing"] == 245 and out["breadth"]["source"] == "SentiSense"
    assert out["fed"]["next_decision"] == "2026-12-09" and not out["errors"]
