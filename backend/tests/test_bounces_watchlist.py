"""Bounce screens from whole-market daily bars, and the custom watchlist."""
from datetime import date

import pytest

from app import market_calendar as mc
from app.services import bounces as bn
from tests.conftest import FakeDataSource
from tests.test_research import _auth, client  # noqa: F401  (fixture)

DAYS = mc.previous_sessions(date(2026, 11, 4), 260)


def _series():
    """symbol -> list of (o, h, l, c, v) per session in DAYS."""
    n = len(DAYS)
    out = {}
    # LOWB: falls 60 -> 30, then bounces 3% off the low on 3x volume
    lowb = []
    for i in range(n - 1):
        c = 60 - 30 * i / (n - 2)
        lowb.append((c, c * 1.005, c * 0.99, c, 1e6))
    low = min(b[2] for b in lowb)
    lowb.append((low * 1.01, low * 1.035, low * 1.005, low * 1.03, 3e6))
    out["LOWB"] = lowb
    # EMAB: steady uptrend; last day dips to the 50 EMA and closes 2% above it on 2x volume
    st = bn._new_state()
    emab = []
    for i in range(n - 1):
        c = 20 + 0.05 * i
        emab.append((c, c * 1.01, c * 0.995, c, 1e6))
        bn.update_state(st, DAYS[i].isoformat(), *emab[-1])
    e = st["ema"]
    emab.append((e * 1.01, e * 1.03, e * 1.0, e * 1.02, 2e6))
    out["EMAB"] = emab
    out["FLAT"] = [(10.0, 10.1, 9.9, 10.0, 1e6)] * n
    out["PRICY"] = [(150.0, 151, 149, 150.0, 1e6)] * n
    out["ABC.W"] = [(1.0, 1, 1, 1.0, 1)] * n
    return out


SERIES = _series()


class FakeAlpaca:
    """Multi-symbol daily bars over the synthetic SERIES (one entry per session in DAYS)."""
    calls = 0
    upto = len(DAYS)  # sessions "published" so far (to test the daily update)
    split_from = None  # (symbol, index): bars from this index are halved, as an unadjusted split would be

    async def multi_daily_bars(self, symbols, start, end):
        FakeAlpaca.calls += 1
        out = {}
        for s in symbols:
            if s not in SERIES:
                continue
            rows = []
            for i, d in enumerate(DAYS[:FakeAlpaca.upto]):
                if start <= d <= end:
                    o, h, lo, c, v = SERIES[s][i]
                    k = 0.5 if FakeAlpaca.split_from and FakeAlpaca.split_from[0] == s and i >= FakeAlpaca.split_from[1] else 1
                    rows.append((d.isoformat(), o * k, h * k, lo * k, c * k, v))
            out[s] = rows
        return out


class BarsDS(FakeDataSource):
    alpaca = FakeAlpaca()


@pytest.fixture
def bars_client(client, db, monkeypatch):  # noqa: F811
    import app.main as main
    from app.models import Security

    for t in ["LOWB", "EMAB", "FLAT", "PRICY", "ABC.W", "GONE"]:
        db.add(Security(ticker=t, cik="1", name=f"{t} Inc", exchange="Nasdaq"))
    db.commit()
    FakeAlpaca.calls, FakeAlpaca.upto, FakeAlpaca.split_from = 0, len(DAYS), None
    monkeypatch.setattr(main, "make_datasource", lambda: BarsDS())
    monkeypatch.setattr(bn, "_target_days", lambda today=None: DAYS[:FakeAlpaca.upto])
    monkeypatch.setattr(bn.mc, "last_completed_session", lambda now=None: DAYS[FakeAlpaca.upto - 1])
    return client


def test_rsi_and_ema_state():
    st = bn._new_state()
    for i, d in enumerate(DAYS[:60]):
        c = 10 + (i % 2)  # alternating up/down -> RSI near 50
        bn.update_state(st, d.isoformat(), c, c, c, c, 1)
    assert st["ema"] is not None and 10 < st["ema"] < 11
    assert 40 < st["rsih"][-1] < 60
    bn.update_state(st, DAYS[10].isoformat(), 1, 1, 1, 1, 1)  # out-of-order bar ignored
    assert st["days"][-1] == DAYS[59].isoformat()


def test_bounce_screens_from_api(bars_client, db):
    h = _auth(bars_client)
    r = bars_client.post("/api/bounces/refresh", headers=h, json={"max_seconds": 200}).json()
    # 5 common-stock tickers (warrant excluded) in one multi-symbol request; GONE has no bars
    assert r["remaining"] == 0 and r["symbols"] == 4 and r["universe"] == 5 and FakeAlpaca.calls == 1
    assert r["as_of"] == DAYS[-1].isoformat() and r["stale"] == 0
    again = bars_client.post("/api/bounces/refresh", headers=h, json={}).json()
    assert again["processed"] == 0 and FakeAlpaca.calls == 1  # nothing new to fetch

    low = bars_client.get("/api/bounces?kind=low52", headers=h).json()
    assert [x["symbol"] for x in low["rows"]] == ["LOWB"]
    x = low["rows"][0]
    assert 2 <= x["above_low52_pct"] <= 5 and x["vol_ratio"] == pytest.approx(3.0)
    ema = bars_client.get("/api/bounces?kind=ema50", headers=h).json()
    assert [x["symbol"] for x in ema["rows"]] == ["EMAB"] and ema["rows"][0]["price"] > ema["rows"][0]["ema50"]
    # PRICY is excluded by the $100 cap; stricter volume multiple removes both
    assert bars_client.get("/api/bounces?kind=ema50&vol_mult=2.5", headers=h).json()["rows"] == []
    # RSI filter is optional
    rsi = bars_client.get("/api/bounces?kind=low52&use_rsi=true&rsi_low_min=90", headers=h).json()
    assert rsi["rows"] == []
    assert bars_client.get("/api/bounces?kind=nope", headers=h).status_code == 422


def test_custom_watchlist(bars_client, db):
    h = _auth(bars_client)
    bars_client.post("/api/bounces/refresh", headers=h, json={})
    r = bars_client.post("/api/custom-watchlist", headers=h, json={"symbols": "emab, GOOD; good  XYZ.B", "note": "test"}).json()
    assert r["added"] == ["EMAB", "GOOD", "XYZ.B"]
    assert bars_client.post("/api/custom-watchlist", headers=h, json={"symbols": "GOOD"}).json()["already"] == ["GOOD"]
    assert bars_client.post("/api/custom-watchlist", headers=h, json={"symbols": "BAD$"}).status_code == 422
    rows = {x["symbol"]: x for x in bars_client.get("/api/custom-watchlist?live=true", headers=h).json()["rows"]}
    assert set(rows) == {"EMAB", "GOOD", "XYZ.B"}
    assert rows["GOOD"]["bars_source"] == "fake-bars" and rows["GOOD"]["price"] == 20.3
    off = {x["symbol"]: x for x in bars_client.get("/api/custom-watchlist", headers=h).json()["rows"]}
    assert off["EMAB"]["bars_source"] == "daily market data"
    assert off["EMAB"]["vs_ema50_pct"] is not None and off["EMAB"]["note"] == "test"
    bars_client.delete("/api/custom-watchlist/xyz.b", headers=h)
    assert len(bars_client.get("/api/custom-watchlist", headers=h).json()["rows"]) == 2
    r2 = bars_client.post("/api/auth/register", json={"email": "z@example.com", "password": "correct horse battery"})
    assert bars_client.get("/api/custom-watchlist", headers={"Authorization": f"Bearer {r2.json()['token']}"}).json()["rows"] == []


def test_daily_update_adds_new_session_and_reloads_splits(bars_client, db):
    h = _auth(bars_client)
    FakeAlpaca.upto = len(DAYS) - 1  # yesterday's view: last session not published yet
    r = bars_client.post("/api/bounces/refresh", headers=h, json={}).json()
    assert r["as_of"] == DAYS[-2].isoformat()
    FakeAlpaca.upto = len(DAYS)  # next day: one new session, and FLAT "splits" 2:1 on it
    FakeAlpaca.split_from = ("FLAT", len(DAYS) - 1)
    r = bars_client.post("/api/bounces/refresh", headers=h, json={}).json()
    assert r["as_of"] == DAYS[-1].isoformat() and r["stale"] == 0
    from app.models import PriceSnapshot

    flat = db.get(PriceSnapshot, "FLAT")
    db.refresh(flat)
    assert flat.as_of == DAYS[-1] and flat.data["lows"][0] < 10  # history reloaded at the new (split) scale
    low = bars_client.get("/api/bounces?kind=low52", headers=h).json()
    assert [x["symbol"] for x in low["rows"]] == ["LOWB"]


def test_refresh_without_alpaca(client, db, monkeypatch):  # noqa: F811
    import app.main as main

    monkeypatch.setattr(main, "make_datasource", lambda: FakeDataSource())
    h = _auth(client)
    r = client.post("/api/bounces/refresh", headers=h, json={}).json()
    assert "Alpaca keys are not set" in r["errors"][0]
